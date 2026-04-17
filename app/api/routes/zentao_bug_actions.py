"""
Zentao Bug Action endpoints.

Provides Stage5 with the ability to close, reactivate, reassign, edit and
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
import re
from typing import Optional

import httpx as _httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
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


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/bugs/create-meta")
def get_create_bug_meta(
    major_version_id: Optional[int] = None,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Return metadata needed for the Create Bug form:
    - product_ids, execution_id (derived from the major version if given)
    - users dict (from create-bug page JSON)
    - builds dict
    """
    client = _get_client(current_user.id, db)
    if client is None:
        return {"product_ids": [], "execution_id": None, "users": {}, "builds": {}}

    # Resolve execution from major version
    execution_id: Optional[int] = None
    product_ids: list[int] = []
    if major_version_id:
        major = db.query(Version).filter(
            Version.id == major_version_id,
            Version.version_type == VersionType.MAJOR,
        ).first()
        if major and major.zentao_execution_id:
            execution_id = major.zentao_execution_id
            ctx = client.get_execution_context(execution_id)
            product_ids = ctx.get("product_ids") or []

    # If we have no product yet, try to infer from an existing synced bug
    if not product_ids and major_version_id:
        sample = db.query(BugTracking).filter(
            BugTracking.major_version_id == major_version_id,
            BugTracking.zentao_product_id.isnot(None),
        ).first()
        if sample and sample.zentao_product_id:
            try:
                product_ids = [int(sample.zentao_product_id)]
            except (TypeError, ValueError):
                pass

    # Fetch user list from create-bug page JSON for the first product
    users: dict[str, str] = {}
    builds: dict[str, str] = {}
    modules: dict[str, str] = {}
    stories: dict[str, str] = {}
    bug_types: dict[str, str] = {}
    if product_ids:
        meta = client.get_create_bug_meta(product_ids[0], execution_id or 0)
        if meta:
            users_raw = meta.get("users") or {}
            for account, display in (users_raw.items() if isinstance(users_raw, dict) else []):
                if isinstance(display, str) and len(display) > 2 and display[1] == ":":
                    display = display[2:]
                users[account] = display
            builds = _normalize_meta_options(meta.get("builds"))
            modules = _normalize_meta_options(meta.get("moduleOptionMenu") or meta.get("modules") or meta.get("module"))
            stories = _normalize_meta_options(meta.get("stories") or meta.get("story"))
            bug_types = _normalize_meta_options(meta.get("typeList") or meta.get("type"))

    return {
        "product_ids": product_ids,
        "execution_id": execution_id,
        "users": users,
        "builds": builds,
        "modules": modules,
        "stories": stories,
        "bug_types": bug_types,
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
        "product": payload.product_id,
        "severity": payload.severity,
        "pri": payload.pri,
        "type": payload.bug_type,
    }
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
        result = client.create_bug(body)
    except ZentaoAPIError as exc:
        if exc.status_code == 401:
            invalidate_token(current_user.id, db)
            client2 = _get_client(current_user.id, db)
            if client2 is None:
                raise HTTPException(status_code=400, detail="禅道 token 刷新失败")
            try:
                result = client2.create_bug(body)
            except ZentaoAPIError as exc2:
                raise HTTPException(status_code=exc2.status_code or 502, detail=f"禅道创建Bug失败: {exc2.message}")
        else:
            raise HTTPException(status_code=exc.status_code or 502, detail=f"禅道创建Bug失败: {exc.message}")

    # Extract returned bug ID
    if not result:
        raise HTTPException(status_code=502, detail="禅道返回了空响应，Bug可能未创建成功")
    bug_data = result.get("bug") or result
    zentao_bug_id = str(bug_data.get("id") or "")
    if not zentao_bug_id:
        raise HTTPException(status_code=502, detail="禅道未返回 Bug ID，请检查禅道后台")

    # Get the Zentao base URL from the user's binding
    binding = db.query(UserZentaoBinding).filter(UserZentaoBinding.user_id == current_user.id).first()
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
        zentao_execution_id=str(payload.execution_id) if payload.execution_id else None,
        zentao_opened_build_ids=json.dumps(payload.opened_build or [], ensure_ascii=False),
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

    raw = None
    try:
        raw = client.get_bug(zt_id)
    except ZentaoAPIError as exc:
        if exc.status_code == 401:
            invalidate_token(current_user.id, db)
            client = _get_client(current_user.id, db)
            if client is None:
                raise HTTPException(status_code=400, detail="禅道 token 刷新失败")
            try:
                raw = client.get_bug(zt_id)
            except ZentaoAPIError as exc2:
                if exc2.status_code != 500:
                    raise HTTPException(status_code=exc2.status_code or 502, detail=f"禅道获取Bug详情失败: {exc2.message}")
        elif exc.status_code != 500:
            raise HTTPException(status_code=exc.status_code or 502, detail=f"禅道获取Bug详情失败: {exc.message}")

    # v1 API 返回 PHP Fatal Error（如操作记录含附件时的禅道 IPD 扩展 bug）时
    # fallback 到页面 JSON 接口
    if raw is None:
        page_data = client.get_page(f"bug-view-{zt_id}.json")
        if isinstance(page_data, dict):
            raw = page_data.get("bug") or page_data
            # 页面 JSON 的 actions 在顶层，合并进 bug 给 normalizer 使用
            if isinstance(raw, dict) and "actions" not in raw and "actions" in page_data:
                raw["actions"] = page_data["actions"]
        if not raw:
            raise HTTPException(status_code=502, detail="禅道 Bug 详情获取失败（v1 API 异常，页面接口也无数据）")

    preview = normalize_bug_detail(raw, base_url=base_url)
    if not preview:
        raise HTTPException(status_code=502, detail="禅道 Bug 详情解析失败")

    # 将 steps 里的 file-read/file-download 内联图片 URL 替换为 OmniQA 代理路径
    # 兼容绝对路径 (http://...) 和相对路径 (/zentao/file-read-xxx.png)
    _FILE_URL_RE = re.compile(r'(?:https?://)?[^"\'>\s]*/file-(?:read|download)-(\d+)\.[a-zA-Z0-9]+')
    if preview.get("steps"):
        preview["steps"] = _FILE_URL_RE.sub(
            lambda m: f"/zentao/files/{m.group(1)}",
            preview["steps"],
        )

    # 附件：直接用 file_id 构建代理 URL，比正则解析 URL 更可靠
    for f in preview.get("files") or []:
        fid = f.get("file_id")
        if fid:
            f["url"] = f"/zentao/files/{fid}"
        elif f.get("url"):
            f["url"] = _FILE_URL_RE.sub(
                lambda m: f"/zentao/files/{m.group(1)}",
                f["url"],
            )

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
    if isinstance(bug_raw, dict):
        at = bug_raw.get("assignedTo")
        if isinstance(at, dict):
            current_assigned = at.get("account") or at.get("realname") or ""
        else:
            current_assigned = str(at or "")

    return {
        "users": users,
        "builds": builds,
        "current_assigned": current_assigned,
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
    shows up again as pending in Stage5.
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
    from Stage5 — it is NOT hard-deleted from the local DB so that audit
    records and stage5 history are preserved.
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
            row.updated_at = local_now()
        db.commit()
    except Exception as e:
        logger.warning("_reset_local_closed: %s", e)
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
    if any(tag in content.lower() for tag in ("<p", "<br", "<div", "<ol", "<ul", "<li")):
        return content
    lines = [line.strip() for line in content.replace("\r\n", "\n").split("\n") if line.strip()]
    body = "<br>".join(lines)
    return f"<p>[步骤]</p><p>{body}</p><p>[结果]</p><p></p><p>[期望]</p><p></p>"
