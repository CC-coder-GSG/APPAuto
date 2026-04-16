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

import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.models.bug import BugTracking
from app.models.user_zentao_binding import UserZentaoBinding
from app.services.audit_service import audit
from app.services.zentao_auth_service import get_valid_token, invalidate_token
from app.services.zentao_client_service import ZentaoClient, ZentaoAPIError

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


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

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
            row.updated_at = datetime.utcnow()
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
            row.updated_at = datetime.utcnow()
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
            row.updated_at = datetime.utcnow()
        db.commit()
    except Exception as e:
        logger.warning("_reset_local_closed: %s", e)
        db.rollback()


def _sync_local_title(db: Session, zt_id_str: str, title: str) -> None:
    try:
        rows = db.query(BugTracking).filter(BugTracking.zentao_bug_id == zt_id_str).all()
        for row in rows:
            row.zentao_bug_title = title
            row.updated_at = datetime.utcnow()
        db.commit()
    except Exception as e:
        logger.warning("_sync_local_title: %s", e)
        db.rollback()
