from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.utils.time_utils import local_now


class TerminalDeviceLock(Base):
    """
    设备占用记录（排他锁）。

    同一设备同时最多只有一个「活动锁」（released_at IS NULL），由部分唯一索引保证。
    - lock_type=automation：Jenkins/Appium 自动化占用，期间只允许只读观看。
    - lock_type=manual：某用户持有手动操作权。

    automation 锁靠 Jenkins 的 unlock 回调释放，另有兜底 TTL；manual 锁靠用户释放
    或心跳超时（heartbeat_at + TTL）自动释放。
    """

    __tablename__ = "terminal_device_locks"
    __table_args__ = (
        # 一设备同一时刻只能有一个未释放的锁
        Index(
            "uq_terminal_active_lock",
            "device_id",
            unique=True,
            sqlite_where=text("released_at IS NULL"),
        ),
        Index("ix_terminal_locks_device_active", "device_id", "released_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    device_id: Mapped[int] = mapped_column(
        ForeignKey("terminal_devices.id", ondelete="CASCADE"), nullable=False, index=True
    )

    lock_type: Mapped[str] = mapped_column(String(20), nullable=False)   # automation | manual
    holder_kind: Mapped[str] = mapped_column(String(20), nullable=False)  # jenkins | user
    holder_user_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )

    jenkins_job: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    jenkins_build: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    acquired_at: Mapped[datetime] = mapped_column(DateTime, default=local_now, nullable=False)
    heartbeat_at: Mapped[datetime] = mapped_column(DateTime, default=local_now, nullable=False)
    released_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    release_reason: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)


__all__ = ["TerminalDeviceLock"]
