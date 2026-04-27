from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.utils.time_utils import local_now


class SyncLock(Base):
    """
    Cross-process advisory lock for sync jobs.

    A row exists per logical lock key (e.g. "bug_recent:42",
    "testcase_full:42"). `acquired_at` lets a stale lock be reclaimed if a
    worker died mid-run; `holder` is just for diagnostics.
    """

    __tablename__ = "sync_locks"

    key: Mapped[str] = mapped_column(String(120), primary_key=True)
    holder: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    acquired_at: Mapped[datetime] = mapped_column(DateTime, default=local_now, nullable=False)
    ttl_seconds: Mapped[int] = mapped_column(Integer, default=600, nullable=False)


__all__ = ["SyncLock"]
