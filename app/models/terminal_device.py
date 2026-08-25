from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.enums import TerminalDeviceStatus
from app.utils.time_utils import local_now


class TerminalDevice(Base):
    """
    一台连接在服务器上的被测终端（安卓真机）。

    以 adb serial / udid 作为稳定标识。status 由设备状态机
    （DeviceLockService）统一推导并写回，是「能否远程操作」的权威来源。
    platform 预留，当前仅 android 走 scrcpy。
    """

    __tablename__ = "terminal_devices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    serial: Mapped[str] = mapped_column(String(120), nullable=False, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    platform: Mapped[str] = mapped_column(String(20), nullable=False, default="android")

    # offline / idle / automation / manual —— 存枚举 value，逻辑层用 TerminalDeviceStatus
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=TerminalDeviceStatus.OFFLINE.value)
    connection: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)  # usb / tcp

    model: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    os_version: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)

    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_seen_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=local_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=local_now, onupdate=local_now, nullable=False)


__all__ = ["TerminalDevice"]
