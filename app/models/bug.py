from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, Enum as SAEnum, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.enums import BugSourceType


class BugTracking(Base):
    __tablename__ = "bug_tracking"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    major_version_id: Mapped[int] = mapped_column(ForeignKey("versions.id", ondelete="CASCADE"), nullable=False)
    requirement_id: Mapped[Optional[int]] = mapped_column(ForeignKey("requirements.id", ondelete="CASCADE"), nullable=True)
    source_type: Mapped[BugSourceType] = mapped_column(SAEnum(BugSourceType), default=BugSourceType.REQUIREMENT, nullable=False)
    source_ref: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)

    bug_id: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    found_minor_version_id: Mapped[Optional[int]] = mapped_column(ForeignKey("versions.id"), nullable=True)
    fixed_minor_version_id: Mapped[Optional[int]] = mapped_column(ForeignKey("versions.id"), nullable=True)
    test_done: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    newly_found_bug_id: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    resolution: Mapped[str] = mapped_column(String, default="fixed", nullable=False)
    closed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    created_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    dispatched_to_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    closed_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    is_retest_failed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    requirement = relationship("Requirement", back_populates="bug_tracks")
    major_version = relationship("Version", back_populates="bugs", foreign_keys=[major_version_id])
    dispatched_to = relationship("User", foreign_keys=[dispatched_to_id])
    stage5_records = relationship("BugStage5Record", back_populates="bug", cascade="all, delete-orphan")


__all__ = ["BugTracking"]
