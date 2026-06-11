"""
终端远程查看/操控 API。

REST：设备列表/发现/详情、操作权 acquire/release/heartbeat/preempt、stream-ticket、
设备登记/改名（admin）。
WS：/api/terminals/{id}/ws —— 校验一次性票据后，把画面/操作帧在浏览器与内网 ws-scrcpy
之间双向透传；view 模式只下行视频、丢弃上行输入帧（服务端兜底排他）。
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, WebSocket
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.core.config import settings
from app.models import TerminalControlMode, TerminalDevice, User
from app.services import device_registry_service, device_stream_service
from app.services.device_lock_service import DeviceConflict, DeviceLockService
from app.services.permission_service import ensure_admin, ensure_tab_access

router = APIRouter(prefix="/api/terminals", tags=["terminals"])
logger = logging.getLogger(__name__)


class DeviceUpdatePayload(BaseModel):
    name: Optional[str] = None
    enabled: Optional[bool] = None


class DeviceRegisterPayload(BaseModel):
    serial: str
    name: Optional[str] = None


class StreamTicketPayload(BaseModel):
    mode: str = TerminalControlMode.VIEW.value


def _ensure_enabled() -> None:
    if not settings.terminal_control_enabled:
        raise HTTPException(status_code=403, detail="终端远程控制功能未启用")


def _conflict_to_http(exc: DeviceConflict) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.detail)


# ---------------------------------------------------------------------------
# 查询 / 管理
# ---------------------------------------------------------------------------

@router.get("")
def list_terminals(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _ensure_enabled()
    ensure_tab_access(current_user, "terminal")
    return {
        "devices": DeviceLockService(db).list_devices(),
        "player_base_url": settings.terminal_player_url,
        "player_name": settings.terminal_player_name,
    }


@router.post("/discover")
def discover_terminals(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _ensure_enabled()
    ensure_tab_access(current_user, "terminal")
    stats = device_registry_service.refresh_devices(db)
    return {
        "ok": True,
        **stats,
        "devices": DeviceLockService(db).list_devices(),
        "player_base_url": settings.terminal_player_url,
        "player_name": settings.terminal_player_name,
    }


@router.get("/{device_id}")
def get_terminal(device_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _ensure_enabled()
    ensure_tab_access(current_user, "terminal")
    service = DeviceLockService(db)
    try:
        device = service.get_device_or_404(device_id)
    except DeviceConflict as exc:
        raise _conflict_to_http(exc) from exc
    return service.serialize_device(device)


@router.put("/{device_id}")
def update_terminal(
    device_id: int,
    payload: DeviceUpdatePayload,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_enabled()
    ensure_admin(current_user)
    device = db.query(TerminalDevice).filter(TerminalDevice.id == device_id).first()
    if not device:
        raise HTTPException(status_code=404, detail="设备不存在")
    if payload.name is not None:
        name = payload.name.strip()
        if not name:
            raise HTTPException(status_code=400, detail="设备名称不能为空")
        device.name = name
    if payload.enabled is not None:
        device.enabled = payload.enabled
    db.commit()
    return DeviceLockService(db).serialize_device(device)


# ---------------------------------------------------------------------------
# 操作权（锁）
# ---------------------------------------------------------------------------

@router.post("/{device_id}/control/acquire")
def acquire_control(device_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _ensure_enabled()
    ensure_tab_access(current_user, "terminal")
    try:
        return DeviceLockService(db).acquire_manual(device_id, current_user)
    except DeviceConflict as exc:
        raise _conflict_to_http(exc) from exc


@router.post("/{device_id}/control/heartbeat")
def heartbeat_control(device_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _ensure_enabled()
    ensure_tab_access(current_user, "terminal")
    try:
        return DeviceLockService(db).heartbeat_manual(device_id, current_user)
    except DeviceConflict as exc:
        raise _conflict_to_http(exc) from exc


@router.post("/{device_id}/control/release")
def release_control(device_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _ensure_enabled()
    ensure_tab_access(current_user, "terminal")
    try:
        return DeviceLockService(db).release_manual(device_id, current_user)
    except DeviceConflict as exc:
        raise _conflict_to_http(exc) from exc


@router.post("/{device_id}/control/preempt")
def preempt_control(device_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _ensure_enabled()
    ensure_tab_access(current_user, "terminal")
    ensure_admin(current_user)  # 抢占会中断自动化，限管理员
    try:
        return DeviceLockService(db).preempt(device_id, current_user)
    except DeviceConflict as exc:
        raise _conflict_to_http(exc) from exc


@router.post("/{device_id}/control/force-release")
def force_release_control(device_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _ensure_enabled()
    ensure_admin(current_user)
    try:
        return DeviceLockService(db).release_manual(device_id, current_user, force=True)
    except DeviceConflict as exc:
        raise _conflict_to_http(exc) from exc


# ---------------------------------------------------------------------------
# 流票据 + WS 代理
# ---------------------------------------------------------------------------

@router.post("/{device_id}/stream-ticket")
def stream_ticket(
    device_id: int,
    payload: StreamTicketPayload,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_enabled()
    ensure_tab_access(current_user, "terminal")
    service = DeviceLockService(db)
    try:
        service.get_device_or_404(device_id)
        return device_stream_service.issue_ticket(db, device_id=device_id, user=current_user, mode=payload.mode)
    except DeviceConflict as exc:
        raise _conflict_to_http(exc) from exc


@router.websocket("/{device_id}/ws")
async def terminal_ws(device_id: int, websocket: WebSocket, db: Session = Depends(get_db)):
    """画面/操作 WS 代理。用 query 票据鉴权后转发到内网 ws-scrcpy。"""
    await websocket.accept()
    token = websocket.query_params.get("ticket", "")
    if not settings.terminal_control_enabled:
        await websocket.close(code=1011, reason="terminal control disabled")
        return
    try:
        ticket = device_stream_service.validate_ticket(db, token=token, device_id=device_id)
    except DeviceConflict as exc:
        await websocket.close(code=1008, reason=str(exc.detail)[:100])
        return

    upstream_base = (settings.ws_scrcpy_url or "").rstrip("/")
    if not upstream_base:
        await websocket.close(code=1011, reason="ws-scrcpy 未配置")
        return

    device = db.query(TerminalDevice).filter(TerminalDevice.id == device_id).first()
    if not device:
        await websocket.close(code=1008, reason="device not found")
        return

    allow_input = ticket.mode == TerminalControlMode.CONTROL.value
    upstream_url = f"{upstream_base}/?action=stream&udid={device.serial}"

    try:
        import websockets  # provided by uvicorn[standard]
    except ImportError:  # pragma: no cover
        await websocket.close(code=1011, reason="websockets client unavailable")
        return

    try:
        async with websockets.connect(upstream_url, max_size=None) as upstream:
            await _relay(websocket, upstream, allow_input=allow_input)
    except Exception as exc:  # pragma: no cover - 取决于 ws-scrcpy 在线
        logger.warning("terminal_ws upstream error device=%s: %s", device.serial, exc)
        try:
            await websocket.close(code=1011, reason="upstream error")
        except Exception:
            pass


async def _relay(browser: WebSocket, upstream, *, allow_input: bool) -> None:
    """双向透传。view 模式丢弃浏览器→设备的输入帧。"""

    async def browser_to_upstream() -> None:
        while True:
            msg = await browser.receive()
            if msg.get("type") == "websocket.disconnect":
                break
            if not allow_input:
                continue  # 只读：丢弃上行输入
            data = msg.get("bytes")
            if data is not None:
                await upstream.send(data)
            elif msg.get("text") is not None:
                await upstream.send(msg["text"])

    async def upstream_to_browser() -> None:
        async for message in upstream:
            if isinstance(message, (bytes, bytearray)):
                await browser.send_bytes(message)
            else:
                await browser.send_text(message)

    task_a = asyncio.create_task(browser_to_upstream())
    task_b = asyncio.create_task(upstream_to_browser())
    done, pending = await asyncio.wait({task_a, task_b}, return_when=asyncio.FIRST_COMPLETED)
    for task in pending:
        task.cancel()
