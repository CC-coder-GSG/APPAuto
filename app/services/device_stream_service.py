"""
终端画面/操作 WebSocket 的一次性票据服务。

issue_ticket：校验权限后签发短期票据，返回连 WS 用的 ws_url（指向 FastAPI 自身的
代理端点，由后端再转发到内网 ws-scrcpy）。
validate_ticket：WS 握手时校验票据（未用、未过期、设备匹配、mode 匹配），用完即焚。
"""
from __future__ import annotations

import secrets
from datetime import timedelta

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import (
    TerminalControlMode,
    TerminalDevice,
    TerminalDeviceLock,
    TerminalLockType,
    TerminalStreamTicket,
    User,
)
from app.services.device_lock_service import DeviceConflict
from app.utils.time_utils import utc_now_naive


def issue_ticket(db: Session, *, device_id: int, user: User, mode: str) -> dict:
    mode = (mode or "").strip().lower()
    if mode not in (TerminalControlMode.VIEW.value, TerminalControlMode.CONTROL.value):
        raise DeviceConflict(400, "mode 必须是 view 或 control")

    # control 票据要求调用者当前持有该设备的人工操作锁
    if mode == TerminalControlMode.CONTROL.value:
        active = (
            db.query(TerminalDeviceLock)
            .filter(
                TerminalDeviceLock.device_id == device_id,
                TerminalDeviceLock.released_at.is_(None),
            )
            .first()
        )
        if not active or active.lock_type != TerminalLockType.MANUAL.value or active.holder_user_id != user.id:
            raise DeviceConflict(403, {"reason": "not_holder", "message": "需先获取操作权才能操作终端"})

    token = secrets.token_urlsafe(32)
    expires_at = utc_now_naive() + timedelta(seconds=settings.terminal_stream_ticket_ttl_seconds)
    ticket = TerminalStreamTicket(
        token=token,
        device_id=device_id,
        user_id=user.id,
        mode=mode,
        expires_at=expires_at,
    )
    db.add(ticket)
    db.commit()

    device = db.query(TerminalDevice).filter(TerminalDevice.id == device_id).first()
    return {
        "token": token,
        "mode": mode,
        "serial": device.serial if device else None,
        "ws_url": f"/api/terminals/{device_id}/ws?ticket={token}",
        "expires_at": expires_at.isoformat() + "Z",
    }


def validate_ticket(db: Session, *, token: str, device_id: int) -> TerminalStreamTicket:
    ticket = (
        db.query(TerminalStreamTicket)
        .filter(TerminalStreamTicket.token == token)
        .first()
    )
    if not ticket:
        raise DeviceConflict(401, "票据无效")
    if ticket.device_id != device_id:
        raise DeviceConflict(401, "票据与设备不匹配")
    if ticket.used_at is not None:
        raise DeviceConflict(401, "票据已被使用")
    if ticket.expires_at < utc_now_naive():
        raise DeviceConflict(401, "票据已过期")
    ticket.used_at = utc_now_naive()
    db.commit()
    return ticket


__all__ = ["issue_ticket", "validate_ticket"]
