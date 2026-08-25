"""
自动化侧（Jenkins / Appium 流水线）回调：占用/释放终端。

用共享密钥头 X-Device-Sync-Key 鉴权（仿照禅道浏览器同步）。Jenkinsfile 在测试开始
前 POST /lock，结束后（post.always）POST /unlock，OmniQA 据此把设备标记为
automation 状态，期间禁止人工操作、只允许观看。
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.core.config import settings
from app.services.device_lock_service import DeviceConflict, DeviceLockService

router = APIRouter(prefix="/api/integrations/devices", tags=["device_integrations"])
logger = logging.getLogger(__name__)


class DeviceLockPayload(BaseModel):
    serial: str
    jenkins_job: Optional[str] = None
    jenkins_build: Optional[str] = None


class DeviceUnlockPayload(BaseModel):
    serial: str
    jenkins_build: Optional[str] = None


class DeviceHeartbeatPayload(BaseModel):
    serial: str


def verify_device_sync_key(
    x_device_sync_key: Optional[str] = Header(default=None, alias="X-Device-Sync-Key"),
) -> None:
    if not settings.terminal_control_enabled:
        raise HTTPException(status_code=403, detail="终端远程控制功能未启用")
    server_key = (settings.device_sync_api_key or "").strip()
    if not server_key:
        raise HTTPException(status_code=401, detail="服务端未配置设备同步 API Key")
    if (x_device_sync_key or "").strip() != server_key:
        raise HTTPException(status_code=401, detail="X-Device-Sync-Key 无效")


@router.post("/lock")
def device_lock(payload: DeviceLockPayload, _: None = Depends(verify_device_sync_key), db: Session = Depends(get_db)):
    try:
        return DeviceLockService(db).automation_lock(
            payload.serial, jenkins_job=payload.jenkins_job, jenkins_build=payload.jenkins_build
        )
    except DeviceConflict as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.post("/unlock")
def device_unlock(payload: DeviceUnlockPayload, _: None = Depends(verify_device_sync_key), db: Session = Depends(get_db)):
    try:
        return DeviceLockService(db).automation_unlock(payload.serial, jenkins_build=payload.jenkins_build)
    except DeviceConflict as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.post("/heartbeat")
def device_heartbeat(payload: DeviceHeartbeatPayload, _: None = Depends(verify_device_sync_key), db: Session = Depends(get_db)):
    return DeviceLockService(db).automation_heartbeat(payload.serial)
