from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.utils.time_utils import local_now


class BrowserSyncEvent(Base):
    __tablename__ = "browser_sync_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    client_record_id: Mapped[str] = mapped_column(String(120), nullable=False, unique=True, index=True)
    entity_type: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    action: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    source: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    captured_at: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    received_at: Mapped[datetime] = mapped_column(DateTime, default=local_now, nullable=False)

    top_href: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    page_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    page_type: Mapped[Optional[str]] = mapped_column(String(60), nullable=True)
    script_version: Mapped[Optional[str]] = mapped_column(String(60), nullable=True)

    draft_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    result_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    raw_payload_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    push_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    creator_name: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    operator_name: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    zentao_bug_id: Mapped[Optional[str]] = mapped_column(String(40), nullable=True, index=True)
    zentao_case_id: Mapped[Optional[str]] = mapped_column(String(40), nullable=True, index=True)
    zentao_req_id: Mapped[Optional[str]] = mapped_column(String(40), nullable=True, index=True)
    zentao_requirement_name: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    zentao_product_id: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    zentao_product_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    zentao_project_id: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    zentao_project_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    zentao_execution_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    zentao_affected_version: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    zentao_case_title: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    zentao_bug_title: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    source_type: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    linked_case_id: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    linked_case_label: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    linked_case_href: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    display_bucket: Mapped[str] = mapped_column(String(20), nullable=False, default="overall", index=True)

    status: Mapped[str] = mapped_column(String(30), nullable=False, default="received", index=True)
    failure_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    mapped_requirement_id: Mapped[Optional[int]] = mapped_column(ForeignKey("requirements.id"), nullable=True)
    mapped_major_version_id: Mapped[Optional[int]] = mapped_column(ForeignKey("versions.id"), nullable=True)
    mapped_minor_version_id: Mapped[Optional[int]] = mapped_column(ForeignKey("versions.id"), nullable=True)
    mapped_source_type: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    mapped_source_ref: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    mapped_test_case_id: Mapped[Optional[int]] = mapped_column(ForeignKey("test_cases.id"), nullable=True)

    applied_case_id: Mapped[Optional[int]] = mapped_column(ForeignKey("test_cases.id"), nullable=True)
    applied_bug_tracking_id: Mapped[Optional[int]] = mapped_column(ForeignKey("bug_tracking.id"), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=local_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=local_now, onupdate=local_now, nullable=False)


__all__ = ["BrowserSyncEvent"]
