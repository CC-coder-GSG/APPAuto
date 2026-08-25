from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from sqlalchemy import DateTime, Enum as SAEnum, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.utils.time_utils import local_now


class FieldTestPurposeType(str, Enum):
    REQUIREMENT = "requirement"
    FEATURE = "feature"


class FieldTestResultStatus(str, Enum):
    PASSED = "passed"
    FAILED = "failed"


class FieldTestRecord(Base):
    __tablename__ = "field_test_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    major_version_id: Mapped[int] = mapped_column(ForeignKey("versions.id"), nullable=False, index=True)
    minor_version_id: Mapped[int] = mapped_column(ForeignKey("versions.id"), nullable=False, index=True)

    purpose_type: Mapped[FieldTestPurposeType] = mapped_column(SAEnum(FieldTestPurposeType), nullable=False, index=True)
    requirement_id: Mapped[Optional[int]] = mapped_column(ForeignKey("requirements.id"), nullable=True, index=True)
    test_content: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    start_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    end_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    duration_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    result_status: Mapped[FieldTestResultStatus] = mapped_column(SAEnum(FieldTestResultStatus), nullable=False, index=True)

    tester_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=local_now, nullable=False, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=local_now, onupdate=local_now, nullable=False)

    major_version = relationship("Version", foreign_keys=[major_version_id])
    minor_version = relationship("Version", foreign_keys=[minor_version_id])
    requirement = relationship("Requirement", foreign_keys=[requirement_id])
    tester = relationship("User", foreign_keys=[tester_id])
    bug_links = relationship("FieldTestBugLink", back_populates="field_test", cascade="all, delete-orphan")


class FieldTestBugLink(Base):
    __tablename__ = "field_test_bug_links"
    __table_args__ = (UniqueConstraint("field_test_record_id", "bug_tracking_id", name="uq_field_test_bug_link"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    field_test_record_id: Mapped[int] = mapped_column(ForeignKey("field_test_records.id", ondelete="CASCADE"), nullable=False, index=True)
    bug_tracking_id: Mapped[int] = mapped_column(ForeignKey("bug_tracking.id", ondelete="CASCADE"), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=local_now, nullable=False)

    field_test = relationship("FieldTestRecord", back_populates="bug_links")
    bug = relationship("BugTracking")


__all__ = [
    "FieldTestPurposeType",
    "FieldTestResultStatus",
    "FieldTestRecord",
    "FieldTestBugLink",
]
