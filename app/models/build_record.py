from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.utils.time_utils import local_now


class BuildRecord(Base):
    __tablename__ = "build_records"
    __table_args__ = (
        UniqueConstraint("job_name", "build_number", name="uq_build_records_job_build"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    job_name: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    build_number: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    build_status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    version_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    branch: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    build_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    change_log: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=local_now, nullable=False, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=local_now, onupdate=local_now, nullable=False, index=True)

    # Auto-archive status: set after upsert_report() attempts to create a minor version
    # Values: "ok" | "skipped" | "no_parent" | "empty_version_name" | "not_success" | "error"
    auto_archive_status: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    # The minor version ID that was created or found during auto-archive
    auto_archive_minor_version_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    auto_archive_message: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    # 禅道写回状态：Jenkins 推送后尝试在禅道对应执行下重命名占位/创建新 build
    # Values: "ok" | "no_execution" | "no_binding" | "not_success" | "empty_version_name" | "error"
    zentao_push_status: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    zentao_push_message: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    zentao_pushed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


__all__ = ["BuildRecord"]
