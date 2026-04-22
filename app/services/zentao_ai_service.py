from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

import httpx
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.user_zentao_binding import UserZentaoBinding
from app.services.zentao_auth_service import get_valid_token, invalidate_token
from app.services.zentao_client_service import ZentaoAPIError, ZentaoClient

logger = logging.getLogger(__name__)

_N8N_TIMEOUT = 45.0
_MAX_STORY_DETAIL_CONCURRENCY = 6


class ZentaoAIServiceError(Exception):
    def __init__(self, status_code: int, message: str):
        self.status_code = status_code
        self.message = message
        super().__init__(message)


# ---------------------------------------------------------------------------
# Client acquisition (same pattern as other Zentao routes)
# ---------------------------------------------------------------------------

def _get_client(user_id: int, db: Session) -> ZentaoClient | None:
    binding = (
        db.query(UserZentaoBinding)
        .filter(UserZentaoBinding.user_id == user_id)
        .first()
    )
    if not binding or not binding.base_url:
        return None
    token = get_valid_token(user_id, db)
    if not token:
        return None
    return ZentaoClient(base_url=binding.base_url.rstrip("/"), token=token)


def _call_with_401_retry(user_id: int, db: Session, fn):
    """Run fn(client); on 401 invalidate the token and try once more."""
    client = _get_client(user_id, db)
    if client is None:
        raise ZentaoAIServiceError(400, "当前用户未配置可用的禅道绑定")
    try:
        return fn(client)
    except ZentaoAPIError as exc:
        if exc.status_code != 401:
            raise
        invalidate_token(user_id, db)
        client2 = _get_client(user_id, db)
        if client2 is None:
            raise ZentaoAIServiceError(400, "禅道 token 刷新失败")
        return fn(client2)


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------

def list_executions(user_id: int, db: Session, project_id: int | None = None) -> list[dict]:
    def _do(client: ZentaoClient) -> list[dict]:
        if project_id:
            rows = client.list_project_executions(project_id)
            return [_normalize_execution(r, default_project_name="") for r in rows]
        # no project_id → aggregate across all accessible projects (cap for latency)
        projects = client.list_projects(limit=100)
        picked = [p for p in projects if p.get("id")][:20]
        out: list[dict] = []
        for p in picked:
            try:
                rows = client.list_project_executions(int(p["id"]))
            except ZentaoAPIError as exc:
                if exc.status_code == 401:
                    raise
                logger.warning("list_executions: project=%s err=%s", p.get("id"), exc)
                continue
            pname = str(p.get("name") or "")
            out.extend(_normalize_execution(r, default_project_name=pname) for r in rows)
        return out

    try:
        return _call_with_401_retry(user_id, db, _do)
    except ZentaoAPIError as exc:
        raise ZentaoAIServiceError(exc.status_code or 502, f"禅道读取执行列表失败: {exc.message}")


def list_stories(user_id: int, db: Session, execution_id: int) -> list[dict]:
    def _do(client: ZentaoClient) -> list[dict]:
        rows = client.list_execution_stories(execution_id)
        return [_normalize_story_list_item(r) for r in rows]

    try:
        return _call_with_401_retry(user_id, db, _do)
    except ZentaoAPIError as exc:
        raise ZentaoAIServiceError(exc.status_code or 502, f"禅道读取需求列表失败: {exc.message}")


# ---------------------------------------------------------------------------
# Generate: collect, build payload, forward to n8n
# ---------------------------------------------------------------------------

async def generate(
    *,
    user_id: int,
    username: str,
    db: Session,
    execution_id: int,
    story_ids: list[int],
    select_all: bool,
    user_note: str,
) -> dict:
    if not settings.n8n_zentao_ai_webhook_url:
        raise ZentaoAIServiceError(503, "尚未配置 N8N_ZENTAO_AI_WEBHOOK_URL，无法转发")

    # Resolve execution + stories via one-shot client (token refresh included).
    execution_info: dict = {}
    story_rows: list[dict] = []

    def _prepare(client: ZentaoClient):
        nonlocal execution_info, story_rows
        exec_raw = client.get_execution(execution_id) or {}
        if isinstance(exec_raw, dict):
            execution_info = {
                "id": exec_raw.get("id") or execution_id,
                "name": exec_raw.get("name") or "",
                "project": exec_raw.get("project"),
                "status": exec_raw.get("status") or "",
            }
        else:
            execution_info = {"id": execution_id, "name": "", "project": None, "status": ""}
        if select_all:
            story_rows = client.list_execution_stories(execution_id)
        else:
            by_id = {int(s.get("id")): s for s in client.list_execution_stories(execution_id) if s.get("id")}
            story_rows = [by_id[i] for i in story_ids if i in by_id]

    try:
        _call_with_401_retry(user_id, db, _prepare)
    except ZentaoAPIError as exc:
        raise ZentaoAIServiceError(exc.status_code or 502, f"禅道预检失败: {exc.message}")

    if not story_rows:
        raise ZentaoAIServiceError(400, "未找到任何需要转发的需求")

    # Fetch full story detail concurrently.
    detailed = await _fetch_story_details_concurrent(user_id, db, [int(s["id"]) for s in story_rows if s.get("id")])

    selected_ids = [s["id"] for s in detailed]
    payload = {
        "request_source": "appauto",
        "operator": username,
        "execution": {"id": execution_info.get("id"), "name": execution_info.get("name")},
        "selected_story_ids": selected_ids,
        "select_all": select_all,
        "user_note": user_note,
        "stories": detailed,
    }

    n8n_status, n8n_body = await _post_to_n8n(payload)

    return {
        "ok": 200 <= (n8n_status or 0) < 300,
        "forwarded_story_count": len(detailed),
        "payload": payload,
        "n8n_status": n8n_status,
        "n8n_response": n8n_body,
    }


# ---------------------------------------------------------------------------
# Story detail hydration
# ---------------------------------------------------------------------------

async def _fetch_story_details_concurrent(user_id: int, db: Session, story_ids: list[int]) -> list[dict]:
    sem = asyncio.Semaphore(_MAX_STORY_DETAIL_CONCURRENCY)
    results: dict[int, dict] = {}

    # Build one client up-front; re-acquire once if any call returns 401.
    client_holder: dict[str, ZentaoClient | None] = {"c": _get_client(user_id, db)}
    if client_holder["c"] is None:
        raise ZentaoAIServiceError(400, "当前用户未配置可用的禅道绑定")
    got_401 = {"v": False}

    async def _fetch_one(sid: int) -> None:
        async with sem:
            try:
                raw = await asyncio.to_thread(client_holder["c"].get_story, sid)
            except ZentaoAPIError as exc:
                if exc.status_code == 401:
                    got_401["v"] = True
                    return
                logger.warning("zentao_ai.fetch_story_detail: id=%s err=%s", sid, exc)
                return
            except Exception as exc:  # noqa: BLE001
                logger.warning("zentao_ai.fetch_story_detail: id=%s unexpected=%s", sid, exc)
                return
            if isinstance(raw, dict):
                results[sid] = _normalize_story_detail(raw)

    await asyncio.gather(*[_fetch_one(sid) for sid in story_ids])

    if got_401["v"]:
        invalidate_token(user_id, db)
        client_holder["c"] = _get_client(user_id, db)
        if client_holder["c"] is None:
            raise ZentaoAIServiceError(400, "禅道 token 刷新失败")
        missing = [sid for sid in story_ids if sid not in results]
        await asyncio.gather(*[_fetch_one(sid) for sid in missing])

    # preserve caller order
    return [results[sid] for sid in story_ids if sid in results]


# ---------------------------------------------------------------------------
# n8n webhook delivery
# ---------------------------------------------------------------------------

async def _post_to_n8n(payload: dict) -> tuple[int | None, Any]:
    headers = {"Content-Type": "application/json"}
    if settings.n8n_zentao_ai_webhook_token:
        headers["Authorization"] = f"Bearer {settings.n8n_zentao_ai_webhook_token}"
    try:
        async with httpx.AsyncClient(timeout=_N8N_TIMEOUT) as client:
            resp = await client.post(settings.n8n_zentao_ai_webhook_url, json=payload, headers=headers)
    except httpx.TimeoutException as exc:
        raise ZentaoAIServiceError(504, f"n8n 转发超时: {exc}")
    except httpx.HTTPError as exc:
        raise ZentaoAIServiceError(502, f"n8n 转发失败: {exc}")
    body: Any
    try:
        body = resp.json()
    except ValueError:
        body = resp.text[:2000]
    return resp.status_code, body


# ---------------------------------------------------------------------------
# Normalizers
# ---------------------------------------------------------------------------

def _flatten_person(val: Any) -> str:
    if not val:
        return ""
    if isinstance(val, dict):
        return str(val.get("realname") or val.get("account") or "")
    return str(val)


def _as_int(val: Any) -> int | None:
    if val is None:
        return None
    try:
        return int(val)
    except (TypeError, ValueError):
        return None


def _flatten_ref(val: Any) -> tuple[int | None, str]:
    """Zentao nests things like {"id":15,"name":"xxx"}. Extract (id, name)."""
    if isinstance(val, dict):
        return _as_int(val.get("id")), str(val.get("name") or "")
    return _as_int(val), ""


_TAG_RE = re.compile(r"<[^>]+>")


def _html_to_text(html: str) -> str:
    if not html:
        return ""
    cleaned = _TAG_RE.sub(" ", html)
    # collapse runs of whitespace
    return re.sub(r"\s+", " ", cleaned).strip()


def _normalize_execution(raw: dict, default_project_name: str = "") -> dict:
    pid, pname = _flatten_ref(raw.get("project"))
    return {
        "id": _as_int(raw.get("id")),
        "name": str(raw.get("name") or ""),
        "project": pid,
        "project_name": pname or default_project_name,
        "begin": str(raw.get("begin") or ""),
        "end": str(raw.get("end") or ""),
        "status": str(raw.get("status") or ""),
    }


def _normalize_story_list_item(raw: dict) -> dict:
    prod_id, prod_name = _flatten_ref(raw.get("product"))
    return {
        "id": _as_int(raw.get("id")),
        "title": str(raw.get("title") or ""),
        "pri": _as_int(raw.get("pri")),
        "status": str(raw.get("status") or ""),
        "stage": str(raw.get("stage") or ""),
        "assigned_to": _flatten_person(raw.get("assignedTo")),
        "product": prod_id,
        "product_name": prod_name,
    }


def _normalize_story_detail(raw: dict) -> dict:
    story = raw.get("story") if isinstance(raw.get("story"), dict) else raw
    prod_id, prod_name = _flatten_ref(story.get("product"))
    mod_id, mod_name = _flatten_ref(story.get("module"))
    spec_html = str(story.get("spec") or "")
    verify_html = str(story.get("verify") or "")

    actions_raw = raw.get("actions") or story.get("actions") or []
    actions: list[dict] = []
    if isinstance(actions_raw, list):
        for a in actions_raw:
            if not isinstance(a, dict):
                continue
            actions.append({
                "id": _as_int(a.get("id")),
                "actor": _flatten_person(a.get("actor")),
                "action": str(a.get("action") or ""),
                "date": str(a.get("date") or ""),
                "desc": str(a.get("desc") or ""),
                "comment": str(a.get("comment") or ""),
                "extra": a.get("extra"),
            })

    return {
        "id": _as_int(story.get("id")),
        "title": str(story.get("title") or ""),
        "pri": _as_int(story.get("pri")),
        "status": str(story.get("status") or ""),
        "stage": str(story.get("stage") or ""),
        "product": prod_id,
        "product_name": prod_name,
        "module": mod_id,
        "module_name": mod_name,
        "assigned_to": _flatten_person(story.get("assignedTo")),
        "opened_by": _flatten_person(story.get("openedBy")),
        "reviewed_by": _flatten_person(story.get("reviewedBy")),
        "spec_html": spec_html,
        "spec_text": _html_to_text(spec_html),
        "verify_html": verify_html,
        "estimate": story.get("estimate"),
        "consumed": story.get("consumed"),
        "actions": actions,
    }
