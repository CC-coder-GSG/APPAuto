from __future__ import annotations

import asyncio
import logging
import re
import uuid
from typing import Any

import httpx
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import SessionLocal
from app.models.user_zentao_binding import UserZentaoBinding
from app.services import story_ai_result_service
from app.services.sse_service import sse_publish
from app.services.zentao_auth_service import get_valid_token, invalidate_token
from app.services.zentao_client_service import ZentaoAPIError, ZentaoClient

logger = logging.getLogger(__name__)

_N8N_TIMEOUT = httpx.Timeout(connect=10.0, read=300.0, write=30.0, pool=30.0)
_MAX_STORY_DETAIL_CONCURRENCY = 6


class ZentaoAIServiceError(Exception):
    def __init__(self, status_code: int, message: str):
        self.status_code = status_code
        self.message = message
        super().__init__(message)


# ---------------------------------------------------------------------------
# Access control (tab permission + optional username allowlist)
# ---------------------------------------------------------------------------

def ensure_zentao_ai_access(user) -> None:
    """Raise ZentaoAIServiceError(403) if this user cannot use the AI forwarder.

    Layers:
      1. The "zentao-ai" tab permission (handled by route via ensure_tab_access)
      2. Optional username allowlist configured via APP_ZENTAO_AI_ALLOWED_USERNAMES
    """
    allowlist = settings.zentao_ai_allowed_usernames
    if not allowlist:
        return
    username = str(getattr(user, "username", "") or "")
    if username not in allowlist:
        raise ZentaoAIServiceError(403, "当前账号不在禅道AI功能白名单内")


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
# Generate-and-save: synchronous preflight + background n8n + DB write + SSE
# ---------------------------------------------------------------------------

def prepare_batch(
    *,
    user_id: int,
    username: str,
    db: Session,
    execution_id: int,
    story_ids: list[int],
    select_all: bool,
    user_note: str,
) -> dict:
    """
    Synchronous preflight. Resolves the execution + stories (with full detail),
    seeds pending StoryAIResult rows, and returns everything the background task
    needs to POST the payload to n8n.
    """
    if not settings.n8n_zentao_ai_webhook_url:
        raise ZentaoAIServiceError(503, "尚未配置 N8N_ZENTAO_AI_WEBHOOK_URL，无法转发")

    execution_info: dict = {}
    story_rows: list[dict] = []

    def _prefetch(client: ZentaoClient):
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
        _call_with_401_retry(user_id, db, _prefetch)
    except ZentaoAPIError as exc:
        raise ZentaoAIServiceError(exc.status_code or 502, f"禅道预检失败: {exc.message}")

    if not story_rows:
        raise ZentaoAIServiceError(400, "未找到任何需要转发的需求")

    client = _get_client(user_id, db)
    if client is None:
        raise ZentaoAIServiceError(400, "当前用户未配置可用的禅道绑定")

    detailed: list[dict] = []
    retry_after_401 = False
    for s in story_rows:
        try:
            sid = int(s.get("id"))
        except (TypeError, ValueError):
            continue
        try:
            raw = client.get_story(sid)
        except ZentaoAPIError as exc:
            if exc.status_code == 401 and not retry_after_401:
                invalidate_token(user_id, db)
                client = _get_client(user_id, db)
                if client is None:
                    raise ZentaoAIServiceError(400, "禅道 token 刷新失败")
                retry_after_401 = True
                try:
                    raw = client.get_story(sid)
                except ZentaoAPIError as exc2:
                    logger.warning("prepare_batch.get_story story=%s err=%s", sid, exc2)
                    continue
            else:
                logger.warning("prepare_batch.get_story story=%s err=%s", sid, exc)
                continue
        if isinstance(raw, dict):
            detailed.append(_normalize_story_detail(raw))

    if not detailed:
        raise ZentaoAIServiceError(502, "禅道需求详情全部获取失败，已中止")

    batch_id = uuid.uuid4().hex

    story_ai_result_service.create_pending_rows(
        db,
        batch_id=batch_id,
        user_id=user_id,
        execution_id=int(execution_info.get("id") or execution_id),
        execution_name=str(execution_info.get("name") or ""),
        stories=detailed,
    )

    payload = {
        "request_source": "appauto",
        "batch_id": batch_id,
        "operator": username,
        "execution": {"id": execution_info.get("id"), "name": execution_info.get("name")},
        "selected_story_ids": [s["id"] for s in detailed if s.get("id")],
        "select_all": select_all,
        "user_note": user_note,
        "stories": detailed,
    }

    return {
        "batch_id": batch_id,
        "payload": payload,
        "story_ids": payload["selected_story_ids"],
        "execution_id": int(execution_info.get("id") or execution_id),
        "execution_name": str(execution_info.get("name") or ""),
    }


async def run_background_batch(batch_id: str, payload: dict) -> None:
    """
    Coroutine scheduled as a FastAPI BackgroundTask. Posts to n8n, parses the
    response, writes results to DB, and emits an SSE event on completion.
    """
    story_ids = payload.get("selected_story_ids") or []
    try:
        n8n_status, n8n_body = await _post_to_n8n(payload)
    except ZentaoAIServiceError as exc:
        logger.error("zentao_ai.run_background_batch: batch=%s n8n err=%s", batch_id, exc.message)
        _sync_mark_failed_and_notify(batch_id, story_ids, exc.message, raw=None)
        return
    except Exception as exc:  # noqa: BLE001
        logger.exception("zentao_ai.run_background_batch: batch=%s unexpected", batch_id)
        _sync_mark_failed_and_notify(batch_id, story_ids, f"后台转发异常: {exc}", raw=None)
        return

    if not (200 <= (n8n_status or 0) < 300):
        message = f"n8n 返回 HTTP {n8n_status}"
        _sync_mark_failed_and_notify(batch_id, story_ids, message, raw=n8n_body)
        return

    def _persist() -> dict:
        db = SessionLocal()
        try:
            return story_ai_result_service.save_ai_results(
                db, batch_id=batch_id, n8n_response=n8n_body
            )
        finally:
            db.close()

    try:
        summary = await asyncio.to_thread(_persist)
    except Exception as exc:  # noqa: BLE001
        logger.exception("zentao_ai.run_background_batch: persist failed batch=%s", batch_id)
        _sync_mark_failed_and_notify(batch_id, story_ids, f"结果入库失败: {exc}", raw=n8n_body)
        return

    sse_publish(
        "zentao_ai_batch_completed",
        {
            "batch_id": batch_id,
            "story_ids": story_ids,
            "success": summary.get("success", 0),
            "failed": summary.get("failed", 0),
            "per_story": summary.get("per_story", {}),
        },
        channels=["global"],
    )


def _sync_mark_failed_and_notify(batch_id: str, story_ids: list[int], message: str, raw: Any) -> None:
    db = SessionLocal()
    try:
        story_ai_result_service.mark_batch_failed(db, batch_id=batch_id, error_message=message, raw=raw)
    except Exception:  # noqa: BLE001
        logger.exception("zentao_ai._sync_mark_failed_and_notify: DB write failed batch=%s", batch_id)
    finally:
        db.close()
    sse_publish(
        "zentao_ai_batch_completed",
        {
            "batch_id": batch_id,
            "story_ids": story_ids,
            "success": 0,
            "failed": len(story_ids),
            "error": message,
        },
        channels=["global"],
    )


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
        body = resp.text[:4000]
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
    if isinstance(val, dict):
        return _as_int(val.get("id")), str(val.get("name") or "")
    return _as_int(val), ""


_TAG_RE = re.compile(r"<[^>]+>")


def _html_to_text(html: str) -> str:
    if not html:
        return ""
    cleaned = _TAG_RE.sub(" ", html)
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
