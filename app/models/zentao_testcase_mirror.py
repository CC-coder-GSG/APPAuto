from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.utils.time_utils import local_now


class ZentaoTestCaseMirror(Base):
    __tablename__ = "zentao_testcase_mirror"
    __table_args__ = (
        UniqueConstraint("zentao_case_numeric_id", name="uq_zentao_testcase_mirror_case_numeric_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    zentao_case_id: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    zentao_case_numeric_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)

    zentao_product_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    zentao_product_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    zentao_module_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    zentao_module_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    zentao_story_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    zentao_execution_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)

    title: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    case_type: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    stage: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    status: Mapped[Optional[str]] = mapped_column(String(40), nullable=True, index=True)
    pri: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    precondition: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    steps_digest: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    last_runner_account: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    last_runner_name: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    last_run_date: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_run_result: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    bugs_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    zentao_case_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    deleted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    remote_opened_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    remote_updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, index=True)
    last_zentao_synced_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, index=True)
    sync_source: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    raw_payload: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=local_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=local_now, onupdate=local_now, nullable=False)


__all__ = ["ZentaoTestCaseMirror"]
