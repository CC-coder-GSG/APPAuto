from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, Enum as SAEnum, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.enums import RequirementStatus
from app.utils.time_utils import local_now


class Requirement(Base):
    __tablename__ = "requirements"
    __table_args__ = (UniqueConstraint("major_version_id", "zentao_req_id", name="uq_requirements_major_reqid"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    zentao_req_id: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)

    major_version_id: Mapped[int] = mapped_column(ForeignKey("versions.id", ondelete="CASCADE"), nullable=False)
    owner_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)

    case_completed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    test_completed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    retest_completed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    retested_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    retested_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    retest_minor_version_id: Mapped[Optional[int]] = mapped_column(ForeignKey("versions.id"), nullable=True)
    retest_passed: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    test_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    test_notes_updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    test_notes_updated_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    status: Mapped[RequirementStatus] = mapped_column(SAEnum(RequirementStatus), default=RequirementStatus.PENDING, nullable=False)

    # Zentao anchor fields
    zentao_story_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    zentao_plan_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    zentao_plan_title_cache: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=local_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=local_now, onupdate=local_now, nullable=False)

    major_version = relationship("Version", back_populates="requirements", foreign_keys=[major_version_id])
    retest_minor_version = relationship("Version", foreign_keys=[retest_minor_version_id])
    owner = relationship("User", back_populates="assigned_requirements", foreign_keys=[owner_id])
    retester = relationship("User", back_populates="retested_requirements", foreign_keys=[retested_by_id])
    test_notes_updated_by = relationship("User", foreign_keys=[test_notes_updated_by_id])
    test_cases = relationship("TestCase", back_populates="requirement", cascade="all, delete-orphan")
    test_executions = relationship("TestExecution", back_populates="requirement", cascade="all, delete-orphan")
    bug_tracks = relationship("BugTracking", back_populates="requirement", cascade="all, delete-orphan")


__all__ = ["Requirement"]
