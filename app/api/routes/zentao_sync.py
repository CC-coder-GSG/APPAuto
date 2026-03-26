from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.core.config import settings
from app.models import User
from app.schemas.zentao_sync import ZentaoBrowserSyncPayload
from app.services.permission_service import ensure_admin
from app.services.zentao_sync_service import ZentaoSyncService

router = APIRouter()
logger = logging.getLogger(__name__)


class BrowserSyncMapPayload(BaseModel):
    requirement_id: Optional[int] = None
    minor_version_id: Optional[int] = None
    source_type: Optional[str] = None
    source_ref: Optional[str] = None
    display_bucket: Optional[str] = None
    linked_case_id: Optional[str] = None
    note: Optional[str] = None


class BrowserSyncBatchApplyPayload(BaseModel):
    limit: int = 100


def verify_zentao_sync_api_key(
    x_zentao_sync_key: Optional[str] = Header(default=None, alias="X-Zentao-Sync-Key"),
) -> None:
    if not settings.zentao_sync_enabled:
        raise HTTPException(status_code=403, detail="禅道同步功能未启用")

    server_key = (settings.zentao_sync_api_key or "").strip()
    if not server_key:
        raise HTTPException(status_code=401, detail="服务端未配置禅道同步 API Key")

    if (x_zentao_sync_key or "").strip() != server_key:
        raise HTTPException(status_code=401, detail="X-Zentao-Sync-Key 无效")


def _admin_guard(current_user: User = Depends(get_current_user)) -> User:
    ensure_admin(current_user)
    return current_user


@router.options("/api/integrations/zentao/browser-events")
@router.options("/api/zentao/browser-sync")
def zentao_sync_options() -> JSONResponse:
    return JSONResponse({"ok": True, "message": "ok"})


@router.post("/api/integrations/zentao/browser-events")
def receive_browser_event(
    payload: ZentaoBrowserSyncPayload,
    _: None = Depends(verify_zentao_sync_api_key),
    db: Session = Depends(get_db),
):
    try:
        return ZentaoSyncService(db).receive_event(payload)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("处理禅道浏览器事件失败")
        raise HTTPException(status_code=500, detail="接收浏览器事件失败") from exc


@router.post("/api/zentao/browser-sync")
def legacy_browser_sync(
    payload: ZentaoBrowserSyncPayload,
    _: None = Depends(verify_zentao_sync_api_key),
    db: Session = Depends(get_db),
):
    try:
        return ZentaoSyncService(db).legacy_sync(payload)
    except HTTPException as exc:
        return JSONResponse(status_code=exc.status_code, content={"success": False, "message": str(exc.detail)})
    except Exception:
        logger.exception("处理 legacy browser-sync 失败")
        return JSONResponse(status_code=500, content={"success": False, "message": "服务端处理同步请求失败"})


@router.get("/api/integrations/zentao/browser-events")
def list_browser_events(
    entity_type: Optional[str] = None,
    status: Optional[str] = None,
    display_bucket: Optional[str] = None,
    source_type: Optional[str] = None,
    keyword: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    only_unapplied: bool = False,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    _: User = Depends(_admin_guard),
    db: Session = Depends(get_db),
):
    return ZentaoSyncService(db).list_events(
        entity_type=entity_type,
        status=status,
        display_bucket=display_bucket,
        source_type=source_type,
        keyword=keyword,
        date_from=date_from,
        date_to=date_to,
        only_unapplied=only_unapplied,
        page=page,
        page_size=page_size,
    )


@router.get("/api/integrations/zentao/browser-events/{event_id}")
def browser_event_detail(event_id: int, _: User = Depends(_admin_guard), db: Session = Depends(get_db)):
    return ZentaoSyncService(db).get_event_detail(event_id)


@router.post("/api/integrations/zentao/browser-events/{event_id}/map")
def map_browser_event(
    event_id: int,
    payload: BrowserSyncMapPayload,
    current_user: User = Depends(_admin_guard),
    db: Session = Depends(get_db),
):
    return ZentaoSyncService(db).map_event(
        event_id,
        requirement_id=payload.requirement_id,
        minor_version_id=payload.minor_version_id,
        source_type=payload.source_type,
        source_ref=payload.source_ref,
        display_bucket=payload.display_bucket,
        linked_case_id=payload.linked_case_id,
        note=payload.note,
        actor_id=current_user.id,
    )


@router.post("/api/integrations/zentao/browser-events/{event_id}/apply")
def apply_browser_event(event_id: int, current_user: User = Depends(_admin_guard), db: Session = Depends(get_db)):
    return ZentaoSyncService(db).apply_event(event_id, actor_id=current_user.id)


@router.delete("/api/integrations/zentao/browser-events/{event_id}")
def delete_browser_event(event_id: int, current_user: User = Depends(_admin_guard), db: Session = Depends(get_db)):
    return ZentaoSyncService(db).delete_event(event_id, actor_id=current_user.id)


@router.post("/api/integrations/zentao/browser-events/apply-batch")
def apply_browser_events_batch(
    payload: BrowserSyncBatchApplyPayload,
    current_user: User = Depends(_admin_guard),
    db: Session = Depends(get_db),
):
    return ZentaoSyncService(db).apply_batch(actor_id=current_user.id, limit=payload.limit)
