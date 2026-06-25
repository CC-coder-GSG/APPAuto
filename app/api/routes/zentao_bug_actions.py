"""
Zentao Bug Action endpoints.

Provides Overall Test with the ability to close, reactivate, reassign, edit and
delete Zentao bugs directly from within the OmniQA interface, using the
calling user's stored Zentao credentials.

Routes
------
GET  /zentao/bugs/{zt_id}/action-meta      – users + builds from page JSON
PUT  /zentao/bugs/{zt_id}                  – edit bug (title)
POST /zentao/bugs/{zt_id}/close            – close bug, optionally with comment
POST /zentao/bugs/{zt_id}/active           – reactivate closed bug
POST /zentao/bugs/{zt_id}/assign           – reassign without changing status
DELETE /zentao/bugs/{zt_id}               – soft-delete in Zentao, mark locally

All actions that modify Zentao state also update the relevant BugTracking
row(s) in the local database to keep cached fields in sync.
"""
from __future__ import annotations

import json
import logging
import mimetypes
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

import httpx as _httpx
from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.models.bug import BugTracking, BugSourceType
from app.models.version import Version
from app.models.enums import VersionType
from app.models.user_zentao_binding import UserZentaoBinding
from app.services.audit_service import audit
from app.services.zentao_auth_service import get_valid_token, invalidate_token
from app.services.zentao_client_service import ZentaoClient, ZentaoAPIError
from app.services.zentao_normalizer import normalize_bug_detail
from app.utils.time_utils import local_now

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/zentao", tags=["zentao_bug_actions"])


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------

class ClosePayload(BaseModel):
    comment: str = ""


class ActivePayload(BaseModel):
    assigned_to: str = ""
    opened_build: list[str] = []
    comment: str = ""


class AssignPayload(BaseModel):
    assigned_to: str
    comment: str = ""


class EditBugPayload(BaseModel):
    title: str


class CreateBugPayload(BaseModel):
    product_id: int
    project_id: Optional[int] = None
    execution_id: Optional[int] = None
    title: str
    severity: int = 3
    pri: int = 3
    steps: str = ""
    assigned_to: str = ""
    opened_build: list[str] = []
    bug_type: str = "codeerror"
    module_id: Optional[str] = None
    story_id: Optional[str] = None
    # Local tracking context
    major_version_id: Optional[int] = None
    requirement_id: Optional[int] = None
    found_minor_version_id: Optional[int] = None


def _create_access_message(probe: dict | None, *, binding: UserZentaoBinding | None = None, product_id: int | None = None) -> str:
    reason = str((probe or {}).get("reason") or "").strip().lower()
    account = (binding.zentao_account or "").strip() if binding else ""
    account_part = f"账号 {account} " if account else ""
    if reason == "missing_binding":
        return "当前用户未配置禅道绑定，请先在禅道账号绑定页保存账号密码。"
    if reason == "access_denied":
        product_part = f"产品 {product_id}" if product_id else "当前产品"
        return f"当前禅道{account_part}无权访问{product_part}，该实例会对创建接口返回空成功但不真正落库，请联系禅道管理员开通该产品的访问或提 Bug 权限。"
    if reason == "login_required":
        return "当前禅道 token 无法访问创建页，请先刷新禅道绑定后再试。"
    return "禅道创建接口返回空成功响应，但未查询到新建 Bug。经排查该 IPD 实例的 v1 创建接口未真正落库，请联系禅道管理员检查接口配置。"


def _find_option_by_label(options: dict[str, str], keyword: str) -> str | None:
    target = (keyword or "").strip().lower()
    if not target:
        return None
    for key, label in options.items():
        if target in str(label or "").strip().lower():
            return str(key)
    return None


def _meta_requires_login(meta: dict | None) -> bool:
    if not isinstance(meta, dict):
        return False
    if meta.get("loginExpired") is True:
        return True
    return str(meta.get("title") or "").strip() in {"用户登录", "鐢ㄦ埛鐧诲綍"}


def _meta_is_effectively_empty(meta: dict | None) -> bool:
    if not isinstance(meta, dict) or not meta:
        return True
    option_keys = (
        "products",
        "projects",
        "executions",
        "users",
        "builds",
        "moduleOptionMenu",
        "modules",
    )
    for key in option_keys:
        if _normalize_meta_options(meta.get(key)):
            return False
    return True


def _load_create_bug_meta_with_retry(
    client: ZentaoClient,
    *,
    user_id: int,
    db: Session,
    product_id: int,
    execution_id: int,
) -> tuple[ZentaoClient, dict]:
    meta = client.get_create_bug_meta(product_id, execution_id) or {}
    if not (_meta_requires_login(meta) or _meta_is_effectively_empty(meta)):
        return client, meta

    invalidate_token(user_id, db)
    refreshed_client = _get_client(user_id, db)
    if refreshed_client is None:
        return client, meta

    refreshed_meta = refreshed_client.get_create_bug_meta(product_id, execution_id) or {}
    if _meta_requires_login(refreshed_meta) or _meta_is_effectively_empty(refreshed_meta):
        return refreshed_client, refreshed_meta or meta
    return refreshed_client, refreshed_meta


def _probe_create_access_with_retry(
    client: ZentaoClient,
    *,
    user_id: int,
    db: Session,
    product_id: int,
    execution_id: int,
) -> tuple[ZentaoClient, dict]:
    probe = client.probe_bug_create_access(product_id, execution_id)
    if str((probe or {}).get("reason") or "").strip().lower() != "login_required":
        return client, probe

    invalidate_token(user_id, db)
    refreshed_client = _get_client(user_id, db)
    if refreshed_client is None:
        return client, probe

    refreshed_probe = refreshed_client.probe_bug_create_access(product_id, execution_id)
    return refreshed_client, refreshed_probe or probe


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/bugs/create-meta")
def get_create_bug_meta(
    major_version_id: Optional[int] = None,
    product_id: Optional[int] = None,
    project_id: Optional[int] = None,
    execution_id: Optional[int] = None,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Return metadata needed for the Create Bug form, including:
    - current product_ids
    - available executions for the mapped project
    - users / builds / modules / stories / bug types for the selected execution
    """
    client = _get_client(current_user.id, db)
    if client is None:
        return {
            "product_ids": [],
            "execution_id": None,
            "selected_execution_id": None,
            "executions": {},
            "users": {},
            "builds": {},
            "modules": {},
            "stories": {},
            "bug_types": {},
            "binding_status": "missing",
            "create_access": {
                "ok": False,
                "reason": "missing_binding",
                "message": _create_access_message({"reason": "missing_binding"}),
            },
        }

    requested_product_id = int(product_id) if product_id else None
    requested_project_id = int(project_id) if project_id else None
    requested_execution_id = int(execution_id) if execution_id else None
    inferred_execution_id: Optional[int] = requested_execution_id
    inferred_product_ids: list[int] = []
    inferred_project_id: Optional[int] = requested_project_id
    if major_version_id:
        major = db.query(Version).filter(
            Version.id == major_version_id,
            Version.version_type == VersionType.MAJOR,
        ).first()
        if major:
            if not inferred_execution_id and major.zentao_execution_id:
                inferred_execution_id = int(major.zentao_execution_id)
            if major.zentao_project_id:
                inferred_project_id = int(major.zentao_project_id)

        if inferred_execution_id:
            ctx = client.get_execution_context(inferred_execution_id)
            inferred_product_ids = ctx.get("product_ids") or []
            inferred_project_id = ctx.get("project_id") or inferred_project_id

    # If we have no product yet, try to infer from an existing synced bug
    if not inferred_product_ids and major_version_id:
        sample = db.query(BugTracking).filter(
            BugTracking.major_version_id == major_version_id,
            BugTracking.zentao_product_id.isnot(None),
        ).first()
        if sample and sample.zentao_product_id:
            try:
                inferred_product_ids = [int(sample.zentao_product_id)]
            except (TypeError, ValueError):
                pass

    anchor_product_id = requested_product_id or (inferred_product_ids[0] if inferred_product_ids else None) or 15
    client, anchor_meta = _load_create_bug_meta_with_retry(
        client,
        user_id=current_user.id,
        db=db,
        product_id=anchor_product_id,
        execution_id=requested_execution_id or inferred_execution_id or 0,
    )
    all_products = _normalize_meta_options(anchor_meta.get("products"))

    selected_product_key = None
    if requested_product_id and str(requested_product_id) in all_products:
        selected_product_key = str(requested_product_id)
    if not selected_product_key:
        selected_product_key = _find_option_by_label(all_products, "survey master")
    if not selected_product_key and inferred_product_ids:
        for pid in inferred_product_ids:
            if str(pid) in all_products:
                selected_product_key = str(pid)
                break
    if not selected_product_key and str(anchor_product_id) in all_products:
        selected_product_key = str(anchor_product_id)
    if not selected_product_key and all_products:
        selected_product_key = next(iter(all_products.keys()))

    selected_product_id = int(selected_product_key) if selected_product_key else None
    product_meta = anchor_meta
    if selected_product_id and selected_product_id != anchor_product_id:
        client, product_meta = _load_create_bug_meta_with_retry(
            client,
            user_id=current_user.id,
            db=db,
            product_id=selected_product_id,
            execution_id=requested_execution_id or inferred_execution_id or 0,
        )

    all_projects = _normalize_meta_options(product_meta.get("projects"))
    selected_project_key = None
    if requested_project_id and str(requested_project_id) in all_projects:
        selected_project_key = str(requested_project_id)
    if not selected_project_key and inferred_project_id and str(inferred_project_id) in all_projects:
        selected_project_key = str(inferred_project_id)
    if not selected_project_key:
        selected_project_key = _find_option_by_label(all_projects, "survey master")
    if not selected_project_key and len(all_projects) == 1:
        selected_project_key = next(iter(all_projects.keys()))
    if not selected_project_key and all_projects:
        selected_project_key = next(iter(all_projects.keys()))

    selected_project_id = int(selected_project_key) if selected_project_key else None
    executions: dict[str, str] = {}
    if selected_project_id:
        for row in client.list_project_executions(selected_project_id):
            exec_id = row.get("id")
            exec_name = str(row.get("name") or "").strip()
            if exec_id and exec_name:
                executions[str(exec_id)] = exec_name
    if not executions:
        executions = _normalize_meta_options(product_meta.get("executions"))

    selected_execution_key = None
    if requested_execution_id and str(requested_execution_id) in executions:
        selected_execution_key = str(requested_execution_id)
    if not selected_execution_key and inferred_execution_id and str(inferred_execution_id) in executions:
        selected_execution_key = str(inferred_execution_id)
    if not selected_execution_key and len(executions) == 1:
        selected_execution_key = next(iter(executions.keys()))

    selected_execution_id = int(selected_execution_key) if selected_execution_key else None
    field_meta = product_meta
    if selected_product_id and (selected_execution_id or 0) != (requested_execution_id or inferred_execution_id or 0):
        client, field_meta = _load_create_bug_meta_with_retry(
            client,
            user_id=current_user.id,
            db=db,
            product_id=selected_product_id,
            execution_id=selected_execution_id or 0,
        )
        field_meta = field_meta or product_meta
    elif selected_product_id and selected_product_id != anchor_product_id:
        client, field_meta = _load_create_bug_meta_with_retry(
            client,
            user_id=current_user.id,
            db=db,
            product_id=selected_product_id,
            execution_id=selected_execution_id or 0,
        )
        field_meta = field_meta or product_meta

    # Fetch user list / builds / modules from the selected create-bug meta
    users: dict[str, str] = {}
    builds: dict[str, str] = {}
    modules: dict[str, str] = {}
    stories: dict[str, str] = {}
    bug_types: dict[str, str] = {}
    meta_scope = "none"
    create_access = {"ok": None, "reason": "unavailable", "message": ""}
    if selected_product_id:
        if field_meta:
            meta_scope = str(field_meta.get("__meta_scope__") or "unknown")
            users_raw = field_meta.get("users") or {}
            for account, display in (users_raw.items() if isinstance(users_raw, dict) else []):
                if isinstance(display, str) and len(display) > 2 and display[1] == ":":
                    display = display[2:]
                users[account] = display
            builds = _normalize_meta_options(field_meta.get("builds"))
            modules = _normalize_meta_options(field_meta.get("moduleOptionMenu") or field_meta.get("modules") or field_meta.get("module"))
            stories = _normalize_meta_options(field_meta.get("stories") or field_meta.get("story"))
            bug_types = _normalize_meta_options(field_meta.get("typeList") or field_meta.get("type"))
            if selected_execution_id and builds:
                execution_build_ids = set(client.get_execution_build_ids(selected_execution_id))
                if execution_build_ids:
                    filtered_builds = {bid: name for bid, name in builds.items() if bid in execution_build_ids}
                    if filtered_builds:
                        builds = filtered_builds
        client, create_access = _probe_create_access_with_retry(
            client,
            user_id=current_user.id,
            db=db,
            product_id=selected_product_id,
            execution_id=selected_execution_id or 0,
        )
        create_access["message"] = _create_access_message(create_access, product_id=selected_product_id)

    return {
        "product_ids": [selected_product_id] if selected_product_id else [],
        "products": all_products,
        "projects": all_projects,
        "selected_product_id": selected_product_id,
        "selected_project_id": selected_project_id,
        "execution_id": selected_execution_id,
        "selected_execution_id": selected_execution_id,
        "executions": executions,
        "users": users,
        "builds": builds,
        "modules": modules,
        "stories": stories,
        "bug_types": bug_types,
        "meta_scope": meta_scope,
        "binding_status": "ok",
        "create_access": create_access,
    }


@router.post("/bugs")
def create_zentao_bug(
    payload: CreateBugPayload,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Create a new bug in Zentao, then write the result back to local BugTracking.
    """
    client = _get_client(current_user.id, db)
    if client is None:
        raise HTTPException(status_code=400, detail="当前用户未配置可用的禅道绑定")

    body: dict = {
        "title": payload.title,
        "severity": payload.severity,
        "pri": payload.pri,
        "type": payload.bug_type,
    }
    if payload.project_id:
        body["project"] = payload.project_id
    if payload.execution_id:
        body["execution"] = payload.execution_id
    if payload.steps:
        body["steps"] = _format_steps_html(payload.steps)
    if payload.assigned_to:
        body["assignedTo"] = payload.assigned_to
    if payload.opened_build:
        body["openedBuild"] = payload.opened_build
    if payload.module_id:
        body["module"] = payload.module_id
    if payload.story_id:
        body["story"] = payload.story_id

    try:
        result = client.create_bug(payload.product_id, body)
    except ZentaoAPIError as exc:
        if exc.status_code == 401:
            invalidate_token(current_user.id, db)
            client2 = _get_client(current_user.id, db)
            if client2 is None:
                raise HTTPException(status_code=400, detail="禅道 token 刷新失败")
            try:
                result = client2.create_bug(payload.product_id, body)
            except ZentaoAPIError as exc2:
                raise HTTPException(status_code=exc2.status_code or 502, detail=f"禅道创建Bug失败: {exc2.message}")
        else:
            raise HTTPException(status_code=exc.status_code or 502, detail=f"禅道创建Bug失败: {exc.message}")

    # Extract returned bug ID. Write responses vary by Zentao version:
    #   {"id":N, "title":..., ...}         — 18.x (bug object at root)
    #   {"bug":{"id":N, ...}}              — some forks
    #   {"data":{"id":N}} / {"data":{...}} — IPD forks
    #   {"bugID":N}                        — legacy-ajax style
    #   {"message":"success"}              — empty body, already normalized
    #   {"error":"..."}                    — validation failure w/ HTTP 200
    if not isinstance(result, dict) or not result:
        logger.warning("create_zentao_bug: empty response from Zentao")
        raise HTTPException(status_code=502, detail="禅道返回了空响应，Bug可能未创建成功")
    if result.get("error"):
        err_msg = str(result.get("error")).strip() or "未知错误"
        logger.warning("create_zentao_bug: Zentao returned error: %s", err_msg)
        raise HTTPException(status_code=502, detail=f"禅道创建Bug失败: {err_msg}")

    bug_data: dict = {}
    for candidate in (
        result.get("bug") if isinstance(result.get("bug"), dict) else None,
        result.get("data") if isinstance(result.get("data"), dict) else None,
        result,
    ):
        if isinstance(candidate, dict) and (candidate.get("id") or candidate.get("bugID")):
            bug_data = candidate
            break

    binding = db.query(UserZentaoBinding).filter(UserZentaoBinding.user_id == current_user.id).first()
    zentao_bug_id = str(bug_data.get("id") or bug_data.get("bugID") or "").strip()
    if not zentao_bug_id:
        create_probe = client.probe_bug_create_access(payload.product_id, payload.execution_id or 0)
        create_message = _create_access_message(create_probe, binding=binding, product_id=payload.product_id)
        logger.warning("create_zentao_bug: cannot extract bug id from response: %s", result)
        raw_preview = json.dumps(result, ensure_ascii=False)[:300]
        raise HTTPException(status_code=502, detail=f"{create_message} 响应: {raw_preview}")

    # Get the Zentao base URL from the user's binding
    base_url = (binding.base_url or "").rstrip("/") if binding else ""

    # Write back to local BugTracking
    bug_id_str = f"b#{zentao_bug_id}"
    local_row = BugTracking(
        major_version_id=payload.major_version_id,
        requirement_id=payload.requirement_id,
        source_type=BugSourceType.MANUAL,
        bug_id=bug_id_str,
        found_minor_version_id=payload.found_minor_version_id,
        created_by_id=current_user.id,
        zentao_bug_id=zentao_bug_id,
        zentao_bug_title=payload.title,
        zentao_bug_url=f"{base_url}/bug-view-{zentao_bug_id}.html" if base_url else None,
        zentao_product_id=str(payload.product_id),
        zentao_project_id=str(payload.project_id) if payload.project_id else None,
        zentao_execution_id=str(payload.execution_id) if payload.execution_id else None,
        zentao_opened_build_ids=json.dumps(payload.opened_build or [], ensure_ascii=False),
        zentao_opened_by_account=(binding.zentao_account if binding and binding.zentao_account else None),
        zentao_opened_by_name=(current_user.display_name or current_user.username or (binding.zentao_account if binding else None)),
        zentao_creator_name=(current_user.display_name or current_user.username or (binding.zentao_account if binding else None)),
        zentao_live_status="active",
        zentao_assigned_to_account=payload.assigned_to or None,
        zentao_assigned_to_name=payload.assigned_to or None,
        zentao_sync_status="synced",
        zentao_sync_message="通过测试管理系统创建",
        last_zentao_synced_at=local_now(),
        last_zentao_checked_at=local_now(),
    )
    db.add(local_row)
    try:
        db.commit()
        db.refresh(local_row)
    except Exception as e:
        db.rollback()
        logger.warning("create_zentao_bug: local write failed: %s", e)

    audit(db, action="zentao_bug.create", target_type="bug", actor_id=current_user.id,
          target_id=zentao_bug_id, detail=f"title={payload.title}")
    return {
        "message": "禅道Bug创建成功",
        "zentao_bug_id": zentao_bug_id,
        "local_id": local_row.id if local_row.id else None,
        "bug_id": bug_id_str,
    }


@router.get("/bugs/{zt_id}/preview")
def get_bug_preview(
    zt_id: int,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    client = _get_client(current_user.id, db)
    if client is None:
        raise HTTPException(status_code=400, detail="当前用户未配置可用的禅道绑定")

    binding = db.query(UserZentaoBinding).filter(UserZentaoBinding.user_id == current_user.id).first()
    base_url = (binding.base_url or "").rstrip("/") if binding else ""

    # get_bug_with_fallback 内部已处理 v1 异常（PHP Fatal Error / 仅返回 BOM 的
    # 空响应 / 非 JSON 等）→ 回退页面 JSON（bug-view-{id}.json，带完整 bug +
    # actions + users）。只有 401 会抛出，交由这里刷新 token 后重试。
    raw = None
    try:
        raw = client.get_bug_with_fallback(zt_id)
    except ZentaoAPIError as exc:
        if exc.status_code == 401:
            invalidate_token(current_user.id, db)
            client = _get_client(current_user.id, db)
            if client is None:
                raise HTTPException(status_code=400, detail="禅道 token 刷新失败")
            try:
                raw = client.get_bug_with_fallback(zt_id)
            except ZentaoAPIError as exc2:
                raise HTTPException(status_code=exc2.status_code or 502, detail=f"禅道获取Bug详情失败: {exc2.message}")
        else:
            raise HTTPException(status_code=exc.status_code or 502, detail=f"禅道获取Bug详情失败: {exc.message}")

    if not raw:
        raise HTTPException(status_code=502, detail="禅道 Bug 详情获取失败（v1 API 异常，页面接口也无数据）")

    preview = normalize_bug_detail(raw, base_url=base_url)
    if not preview:
        raise HTTPException(status_code=502, detail="禅道 Bug 详情解析失败")

    # 将 steps / actions.comment / file url 里的 file-read/file-download 内联
    # 图片 URL 替换为 OmniQA 代理路径。兼容绝对路径 (http://...) 和相对路径
    # (/zentao/file-read-xxx.png)。
    _FILE_URL_RE = re.compile(r'(?:https?://)?[^"\'>\s]*/file-(?:read|download|preview)-(\d+)\.[a-zA-Z0-9]+')

    def _rewrite(html: str) -> str:
        return _FILE_URL_RE.sub(lambda m: f"/zentao/files/{m.group(1)}", html)

    if preview.get("steps"):
        preview["steps"] = _rewrite(preview["steps"])

    for act in preview.get("actions") or []:
        if act.get("comment"):
            act["comment"] = _rewrite(act["comment"])

    # 附件：直接用 file_id 构建代理 URL，比正则解析 URL 更可靠
    for f in preview.get("files") or []:
        fid = f.get("file_id")
        if fid:
            f["url"] = f"/zentao/files/{fid}"
        elif f.get("url"):
            f["url"] = _rewrite(f["url"])

    return preview


@router.get("/files/{file_id}")
def proxy_zentao_file(
    file_id: int,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Proxy a Zentao file/image through OmniQA using Token auth (v1 API)."""
    binding = db.query(UserZentaoBinding).filter(UserZentaoBinding.user_id == current_user.id).first()
    if not binding or not binding.base_url:
        raise HTTPException(status_code=400, detail="未配置禅道绑定")
    token = get_valid_token(current_user.id, db)
    if not token:
        raise HTTPException(status_code=400, detail="禅道 token 无效，请重新登录禅道")
    base_url = binding.base_url.rstrip("/")
    url = f"{base_url}/api.php/v1/files/{file_id}"
    try:
        resp = _httpx.get(url, headers={"Token": token}, timeout=15)
        if resp.status_code != 200:
            raise HTTPException(status_code=resp.status_code, detail="禅道文件获取失败")
        content_type = resp.headers.get("content-type", "application/octet-stream")
        return StreamingResponse(iter([resp.content]), media_type=content_type)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


# Directory used to persist inline images when the user's Zentao rejects our
# upload (extension whitelist, API mismatch, etc.). We fall back to serving
# them from OmniQA itself via the public route below, and the bug's steps
# HTML references the full URL so Zentao's viewer can still render them.
_INLINE_IMAGES_ROOT = Path(__file__).resolve().parents[3] / "uploads" / "inline_images"


def _save_local_inline_image(data: bytes, extension: str) -> Path:
    today = datetime.now().strftime("%Y%m%d")
    target_dir = _INLINE_IMAGES_ROOT / today
    target_dir.mkdir(parents=True, exist_ok=True)
    uid = uuid.uuid4().hex
    ext = (extension or "png").lstrip(".").lower() or "png"
    path = target_dir / f"{uid}.{ext}"
    path.write_bytes(data)
    return path


def _public_url_for(request: Request, relative_path: str) -> str:
    base = str(request.base_url).rstrip("/")
    return f"{base}{relative_path}"


@router.get("/inline-images/{date_dir}/{filename}", include_in_schema=False)
def serve_inline_image(date_dir: str, filename: str):
    """
    Publicly serve a locally-stored inline bug image.

    The images are referenced by absolute URL inside bug descriptions so
    Zentao's viewer (which cannot authenticate to OmniQA) can load them.
    Paths are constrained to the inline_images directory.
    """
    if not re.fullmatch(r"\d{8}", date_dir):
        raise HTTPException(status_code=404, detail="not found")
    if not re.fullmatch(r"[a-f0-9]{32}\.[a-zA-Z0-9]{1,8}", filename):
        raise HTTPException(status_code=404, detail="not found")
    path = _INLINE_IMAGES_ROOT / date_dir / filename
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="not found")
    media_type = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
    return FileResponse(str(path), media_type=media_type)


@router.post("/bugs/upload-inline-image")
async def upload_inline_bug_image(
    request: Request,
    file: UploadFile = File(...),
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Upload a pasted/attached inline image for a bug's reproduce-steps
    editor. We try uploading to Zentao first so the file is hosted there;
    if Zentao rejects the upload (extension whitelist, version mismatch),
    we transparently fall back to saving the image in OmniQA's local
    `uploads/inline_images/` directory and returning an absolute OmniQA
    URL. Either way the caller gets a usable `zentao_url` and `proxy_url`.
    """
    if not file or not file.filename:
        raise HTTPException(status_code=400, detail="未提供图片文件")
    content_type = (file.content_type or "").lower()
    if not content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="仅支持上传图片文件")

    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="图片内容为空")
    if len(data) > 10 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="图片过大（最大 10MB）")

    ext_from_mime = content_type.split("/")[-1].split(";")[0].strip() if content_type.startswith("image/") else ""
    if ext_from_mime == "jpeg":
        ext_from_mime = "jpg"
    derived_ext = ext_from_mime or (file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else "png")

    client = _get_client(current_user.id, db)
    if client is None:
        raise HTTPException(status_code=400, detail="当前用户未配置可用的禅道绑定")

    info: dict | None = None
    upload_error: Optional[str] = None
    try:
        info = client.upload_file(
            file_bytes=data,
            filename=file.filename,
            content_type=content_type or "application/octet-stream",
            object_type="bug",
            object_id=0,
        )
    except ZentaoAPIError as exc:
        if exc.status_code == 401:
            invalidate_token(current_user.id, db)
            client2 = _get_client(current_user.id, db)
            if client2 is not None:
                try:
                    info = client2.upload_file(
                        file_bytes=data,
                        filename=file.filename,
                        content_type=content_type or "application/octet-stream",
                        object_type="bug",
                        object_id=0,
                    )
                except ZentaoAPIError as exc2:
                    upload_error = exc2.message
            else:
                upload_error = "token 刷新失败"
        else:
            upload_error = exc.message

    if info and info.get("id"):
        binding = db.query(UserZentaoBinding).filter(UserZentaoBinding.user_id == current_user.id).first()
        base_url = (binding.base_url or "").rstrip("/") if binding else ""
        file_id = int(info.get("id"))
        extension = str(info.get("extension") or "").lstrip(".").lower() or derived_ext
        zentao_url = f"{base_url}/file-read-{file_id}.{extension}" if base_url else ""
        audit(db, action="zentao_bug.upload_inline_image", target_type="bug",
              actor_id=current_user.id, target_id=str(file_id),
              detail=f"filename={file.filename}, size={len(data)}, storage=zentao")
        return {
            "file_id": file_id,
            "extension": extension,
            "proxy_url": f"/zentao/files/{file_id}",
            "zentao_url": zentao_url,
            "title": info.get("title") or file.filename,
            "storage": "zentao",
        }

    # Fallback: Zentao rejected the upload. Persist locally and expose via
    # an absolute OmniQA URL so the image is still viewable from the Bug
    # in Zentao (Zentao's viewer just <img src="..."> loads it).
    logger.warning(
        "upload_inline_bug_image: Zentao upload failed, falling back to local storage: %s",
        upload_error,
    )
    path = _save_local_inline_image(data, derived_ext)
    relative_path = f"/zentao/inline-images/{path.parent.name}/{path.name}"
    absolute_url = _public_url_for(request, relative_path)
    audit(db, action="zentao_bug.upload_inline_image", target_type="bug",
          actor_id=current_user.id, target_id=path.name,
          detail=f"filename={file.filename}, size={len(data)}, storage=local, zentao_err={upload_error}")
    return {
        "file_id": 0,
        "extension": derived_ext,
        "proxy_url": absolute_url,
        "zentao_url": absolute_url,
        "title": file.filename,
        "storage": "local",
        "zentao_error": upload_error or "",
    }


@router.get("/bugs/{zt_id}/action-meta")
def get_bug_action_meta(
    zt_id: int,
    action: str = "activate",
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Return users + builds metadata from the Zentao page JSON for a given
    bug action page (activate / resolve / view / close).

    The `users` dict has the form {"account": "display_name"} and is suitable
    for building a searchable assignee dropdown.
    """
    client = _get_client(current_user.id, db)
    if client is None:
        return {"users": {}, "builds": {}}

    meta = client.get_bug_action_meta(zt_id, action)
    if meta is None:
        # Try fallback pages
        for fallback in ("view", "activate", "resolve"):
            if fallback == action:
                continue
            meta = client.get_bug_action_meta(zt_id, fallback)
            if meta:
                break

    if meta is None:
        return {"users": {}, "builds": {}}

    users_raw = meta.get("users") or {}
    builds_raw = meta.get("builds") or {}
    bug_raw = meta.get("bug") or {}

    # Normalize users: strip leading "X:" prefix from display names (activate page quirk)
    users: dict[str, str] = {}
    for account, display in users_raw.items():
        if isinstance(display, str) and len(display) > 2 and display[1] == ":":
            display = display[2:]
        users[account] = display

    # Normalize builds: Zentao returns dict or list; convert to {build_id_str: name}
    builds: dict[str, str] = {}
    if isinstance(builds_raw, dict):
        for bid, bval in builds_raw.items():
            name = bval if isinstance(bval, str) else (bval.get("name") or str(bid))
            builds[str(bid)] = name
    elif isinstance(builds_raw, list):
        for b in builds_raw:
            if isinstance(b, dict):
                builds[str(b.get("id", ""))] = b.get("name") or str(b.get("id", ""))

    current_assigned = ""
    bug_execution_id: Optional[int] = None
    if isinstance(bug_raw, dict):
        at = bug_raw.get("assignedTo")
        if isinstance(at, dict):
            current_assigned = at.get("account") or at.get("realname") or ""
        else:
            current_assigned = str(at or "")
        execution_raw = bug_raw.get("execution")
        if isinstance(execution_raw, dict):
            try:
                bug_execution_id = int(execution_raw.get("id")) if execution_raw.get("id") else None
            except (TypeError, ValueError):
                bug_execution_id = None
        elif execution_raw:
            try:
                bug_execution_id = int(execution_raw)
            except (TypeError, ValueError):
                bug_execution_id = None

    # Only show builds that belong to the bug's own execution. Fall back to
    # the full list if the execution has no bound builds (older Zentao
    # instances, or freshly created executions) so the user is never
    # stuck with an empty dropdown.
    if bug_execution_id and builds:
        execution_build_ids = set(client.get_execution_build_ids(bug_execution_id))
        if execution_build_ids:
            filtered = {bid: name for bid, name in builds.items() if bid in execution_build_ids}
            if filtered:
                builds = filtered

    return {
        "users": users,
        "builds": builds,
        "current_assigned": current_assigned,
        "execution_id": bug_execution_id,
    }


@router.put("/bugs/{zt_id}")
def edit_zentao_bug(
    zt_id: int,
    payload: EditBugPayload,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Edit a Zentao bug (currently: title only).

    Also syncs the updated title back to the local BugTracking row.
    """
    client = _get_client(current_user.id, db)
    if client is None:
        raise HTTPException(status_code=400, detail="当前用户未配置可用的禅道绑定")

    try:
        client.update_bug(zt_id, {"title": payload.title})
    except ZentaoAPIError as exc:
        if exc.status_code == 401:
            invalidate_token(current_user.id, db)
            client2 = _get_client(current_user.id, db)
            if client2 is None:
                raise HTTPException(status_code=400, detail="禅道 token 刷新失败")
            try:
                client2.update_bug(zt_id, {"title": payload.title})
            except ZentaoAPIError as exc2:
                raise HTTPException(status_code=exc2.status_code, detail=f"禅道更新失败: {exc2.message}")
        else:
            raise HTTPException(status_code=exc.status_code or 502, detail=f"禅道更新失败: {exc.message}")

    # Write back to local BugTracking rows matching this zentao_bug_id
    _sync_local_title(db, str(zt_id), payload.title)
    audit(db, action="zentao_bug.edit", target_type="bug", actor_id=current_user.id,
          target_id=str(zt_id), detail=f"title={payload.title}")
    return {"message": "禅道 Bug 标题已更新"}


@router.post("/bugs/{zt_id}/close")
def close_zentao_bug(
    zt_id: int,
    payload: ClosePayload,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Close a Zentao bug.

    State rules (enforced by reading live status first):
    - resolved → allowed to close
    - closed   → skip Zentao call (already closed), just return success
    - active   → rejected with 422: must be resolved before closing
    """
    client = _get_client(current_user.id, db)
    if client is None:
        raise HTTPException(status_code=400, detail="当前用户未配置可用的禅道绑定")

    # Validate live status
    live_status = _fetch_live_status(client, zt_id)
    if live_status == "active":
        raise HTTPException(
            status_code=422,
            detail="禅道中该 Bug 尚未解决 (active)，不能直接闭环关闭。请先在禅道中将 Bug 标记为已解决 (resolved)。",
        )

    already_closed = live_status == "closed"
    if not already_closed:
        try:
            client.close_bug(zt_id, payload.comment)
        except ZentaoAPIError as exc:
            if exc.status_code == 401:
                invalidate_token(current_user.id, db)
                client2 = _get_client(current_user.id, db)
                if client2 is None:
                    raise HTTPException(status_code=400, detail="禅道 token 刷新失败")
                try:
                    client2.close_bug(zt_id, payload.comment)
                except ZentaoAPIError as exc2:
                    raise HTTPException(status_code=exc2.status_code or 502, detail=f"禅道关闭失败: {exc2.message}")
            else:
                raise HTTPException(status_code=exc.status_code or 502, detail=f"禅道关闭失败: {exc.message}")

    _update_local_live_status(db, str(zt_id), "closed")
    _apply_local_close_side_effects(db, str(zt_id), current_user, comment=payload.comment)
    audit(db, action="zentao_bug.close", target_type="bug", actor_id=current_user.id,
          target_id=str(zt_id), detail=f"comment={payload.comment[:100]}")
    return {"message": "禅道 Bug 已关闭", "already_closed": already_closed}


@router.post("/bugs/{zt_id}/active")
def active_zentao_bug(
    zt_id: int,
    payload: ActivePayload,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Reactivate a closed Zentao bug.

    Also resets the local BugTracking `closed` flag to False so the row
    shows up again as pending in Overall Test.
    """
    client = _get_client(current_user.id, db)
    if client is None:
        raise HTTPException(status_code=400, detail="当前用户未配置可用的禅道绑定")

    try:
        client.active_bug(
            zt_id,
            assigned_to=payload.assigned_to,
            opened_build=payload.opened_build or [],
            comment=payload.comment,
        )
    except ZentaoAPIError as exc:
        if exc.status_code == 401:
            invalidate_token(current_user.id, db)
            client2 = _get_client(current_user.id, db)
            if client2 is None:
                raise HTTPException(status_code=400, detail="禅道 token 刷新失败")
            try:
                client2.active_bug(zt_id, assigned_to=payload.assigned_to,
                                   opened_build=payload.opened_build or [], comment=payload.comment)
            except ZentaoAPIError as exc2:
                raise HTTPException(status_code=exc2.status_code or 502, detail=f"禅道激活失败: {exc2.message}")
        else:
            raise HTTPException(status_code=exc.status_code or 502, detail=f"禅道激活失败: {exc.message}")

    # Reset local closed state and live status
    _update_local_live_status(db, str(zt_id), "active")
    _reset_local_closed(db, str(zt_id))
    audit(db, action="zentao_bug.active", target_type="bug", actor_id=current_user.id,
          target_id=str(zt_id), detail=f"assignedTo={payload.assigned_to}, comment={payload.comment[:100]}")
    return {"message": "禅道 Bug 已重新激活"}


@router.post("/bugs/{zt_id}/assign")
def assign_zentao_bug(
    zt_id: int,
    payload: AssignPayload,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Reassign a Zentao bug without changing its current status.
    """
    if not payload.assigned_to:
        raise HTTPException(status_code=400, detail="必须指定指派人 (assigned_to)")

    client = _get_client(current_user.id, db)
    if client is None:
        raise HTTPException(status_code=400, detail="当前用户未配置可用的禅道绑定")

    live_status = _fetch_live_status(client, zt_id).strip().lower()
    if live_status == "closed":
        raise HTTPException(status_code=422, detail="已关闭的禅道 Bug 不能再执行指派，请先重新激活。")

    try:
        client.assign_bug(zt_id, payload.assigned_to, payload.comment)
    except ZentaoAPIError as exc:
        if exc.status_code == 401:
            invalidate_token(current_user.id, db)
            client2 = _get_client(current_user.id, db)
            if client2 is None:
                raise HTTPException(status_code=400, detail="禅道 token 刷新失败")
            try:
                client2.assign_bug(zt_id, payload.assigned_to, payload.comment)
            except ZentaoAPIError as exc2:
                raise HTTPException(status_code=exc2.status_code or 502, detail=f"禅道指派失败: {exc2.message}")
        else:
            raise HTTPException(status_code=exc.status_code or 502, detail=f"禅道指派失败: {exc.message}")

    audit(db, action="zentao_bug.assign", target_type="bug", actor_id=current_user.id,
          target_id=str(zt_id), detail=f"assignedTo={payload.assigned_to}")
    return {"message": f"禅道 Bug 已指派给 {payload.assigned_to}"}


@router.delete("/bugs/{zt_id}")
def delete_zentao_bug(
    zt_id: int,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Delete a Zentao bug (soft-delete in Zentao).

    The local BugTracking row is marked as `zentao_deleted=True` and hidden
    from Overall Test. It is NOT hard-deleted from the local DB so audit
    records and historical verification data are preserved.
    """
    client = _get_client(current_user.id, db)
    if client is None:
        raise HTTPException(status_code=400, detail="当前用户未配置可用的禅道绑定")

    try:
        client.delete_bug(zt_id)
    except ZentaoAPIError as exc:
        if exc.status_code == 401:
            invalidate_token(current_user.id, db)
            client2 = _get_client(current_user.id, db)
            if client2 is None:
                raise HTTPException(status_code=400, detail="禅道 token 刷新失败")
            try:
                client2.delete_bug(zt_id)
            except ZentaoAPIError as exc2:
                raise HTTPException(status_code=exc2.status_code or 502, detail=f"禅道删除失败: {exc2.message}")
        else:
            raise HTTPException(status_code=exc.status_code or 502, detail=f"禅道删除失败: {exc.message}")

    # Soft-mark local row
    try:
        rows = db.query(BugTracking).filter(BugTracking.zentao_bug_id == str(zt_id)).all()
        for row in rows:
            row.zentao_deleted = True
            row.updated_at = local_now()
        db.commit()
    except Exception as e:
        logger.warning("delete_zentao_bug: local update failed: %s", e)
        db.rollback()

    audit(db, action="zentao_bug.delete", target_type="bug", actor_id=current_user.id,
          target_id=str(zt_id), detail="soft-deleted in zentao")
    return {"message": "禅道 Bug 已删除（软删除），本地记录已标记隐藏"}


# ---------------------------------------------------------------------------
# Internal helpers
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


def _fetch_live_status(client: ZentaoClient, zt_id: int) -> str:
    """Return the live Zentao bug status, or empty string if not fetchable."""
    try:
        raw = client.get_bug(zt_id)
        if raw:
            bug = raw.get("bug") or raw
            if isinstance(bug, dict):
                return str(bug.get("status") or "")
    except Exception:
        pass
    return ""


def _update_local_live_status(db: Session, zt_id_str: str, status: str) -> None:
    try:
        rows = db.query(BugTracking).filter(BugTracking.zentao_bug_id == zt_id_str).all()
        for row in rows:
            row.zentao_live_status = status
            if status != "closed":
                row.zentao_closed_by_account = None
                row.zentao_closed_by_name = ""
                row.zentao_close_date = None
                row.zentao_close_comment = ""
            row.updated_at = local_now()
        db.commit()
    except Exception as e:
        logger.warning("_update_local_live_status: %s", e)
        db.rollback()


def _reset_local_closed(db: Session, zt_id_str: str) -> None:
    """After reactivation, reset the local closed flag so the bug shows as pending."""
    try:
        rows = db.query(BugTracking).filter(BugTracking.zentao_bug_id == zt_id_str).all()
        for row in rows:
            row.closed = False
            row.closed_by_id = None
            row.zentao_closed_by_account = None
            row.zentao_closed_by_name = ""
            row.zentao_close_date = None
            row.zentao_close_comment = ""
            row.updated_at = local_now()
        db.commit()
    except Exception as e:
        logger.warning("_reset_local_closed: %s", e)
        db.rollback()


def _apply_local_close_side_effects(db: Session, zt_id_str: str, current_user, *, comment: str = "") -> None:
    """
    When a bug is closed via the single-action Zentao route, keep local state
    aligned so the report center does not need another full sync to reflect
    the closure:
    - BugTracking.closed = True
    - BugTracking.closed_by_id = current user
    - BugTracking.zentao_live_status already set to "closed" by caller
    - BugStage5Record auto-created (source="zentao_sync") so closed_bugs
      counters on the report summary tick up immediately.
    """
    from app.models.stage5 import BugStage5Record
    try:
        rows = db.query(BugTracking).filter(BugTracking.zentao_bug_id == zt_id_str).all()
        for row in rows:
            row.closed = True
            if row.closed_by_id is None:
                row.closed_by_id = current_user.id
            if not row.zentao_close_date:
                row.zentao_close_date = local_now()
            if comment and not row.zentao_close_comment:
                row.zentao_close_comment = comment
            row.updated_at = local_now()

            # Upsert Stage5 record for the closing user so reports count it.
            existing = (
                db.query(BugStage5Record)
                .filter(
                    BugStage5Record.bug_tracking_id == row.id,
                    BugStage5Record.user_id == current_user.id,
                )
                .first()
            )
            if existing:
                if not existing.test_done:
                    existing.test_done = True
                    existing.source = existing.source or "zentao_sync"
                    existing.resolution = existing.resolution or "fixed"
                    existing.updated_at = local_now()
                if comment and not existing.comment:
                    existing.comment = comment
            else:
                db.add(BugStage5Record(
                    bug_tracking_id=row.id,
                    user_id=current_user.id,
                    minor_version_id=row.found_minor_version_id,
                    test_done=True,
                    resolution="fixed",
                    source="zentao_sync",
                    comment=comment or "",
                ))
        db.commit()
    except Exception as e:
        logger.warning("_apply_local_close_side_effects: %s", e)
        db.rollback()


def _sync_local_title(db: Session, zt_id_str: str, title: str) -> None:
    try:
        rows = db.query(BugTracking).filter(BugTracking.zentao_bug_id == zt_id_str).all()
        for row in rows:
            row.zentao_bug_title = title
            row.updated_at = local_now()
        db.commit()
    except Exception as e:
        logger.warning("_sync_local_title: %s", e)
        db.rollback()


def _normalize_meta_options(raw: object) -> dict[str, str]:
    result: dict[str, str] = {}
    if isinstance(raw, dict):
        for key, value in raw.items():
            if key in (None, "", "0", 0):
                continue
            label = ""
            if isinstance(value, dict):
                label = (
                    value.get("name")
                    or value.get("title")
                    or value.get("pathName")
                    or value.get("text")
                    or ""
                )
            else:
                label = str(value or "")
            label = str(label).strip()
            if label:
                result[str(key)] = label
    elif isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict):
                continue
            key = item.get("id") or item.get("value")
            label = item.get("name") or item.get("title") or item.get("pathName") or item.get("text")
            if key and label:
                result[str(key)] = str(label).strip()
    return result


def _format_steps_html(text: str) -> str:
    content = (text or "").strip()
    if not content:
        return ""
    if any(tag in content.lower() for tag in ("<p", "<br", "<div", "<ol", "<ul", "<li", "<img")):
        return content
    # User already structured the text with the [步骤]/[结果]/[期望] markers
    # (e.g. kept our pre-filled template). Preserve the structure, just
    # convert newlines to <br> so it renders correctly in Zentao.
    if "[步骤]" in content or "[结果]" in content or "[期望]" in content:
        return content.replace("\r\n", "\n").replace("\n", "<br>")
    lines = [line.strip() for line in content.replace("\r\n", "\n").split("\n") if line.strip()]
    body = "<br>".join(lines)
    return f"<p>[步骤]</p><p>{body}</p><p>[结果]</p><p></p><p>[期望]</p><p></p>"
