from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, Enum as SAEnum, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.enums import FeedbackStatus


class FeedbackRecord(Base):
    __tablename__ = "feedback_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    feedback_no: Mapped[Optional[str]] = mapped_column(String(30), nullable=True, unique=True, index=True)
    major_version_id: Mapped[int] = mapped_column(ForeignKey("versions.id"), nullable=False, index=True)
    minor_version_id: Mapped[int] = mapped_column(ForeignKey("versions.id"), nullable=False, index=True)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[FeedbackStatus] = mapped_column(SAEnum(FeedbackStatus), default=FeedbackStatus.PENDING, nullable=False, index=True)

    creator_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    assignee_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)

    handling_result: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    handled_major_version_id: Mapped[Optional[int]] = mapped_column(ForeignKey("versions.id"), nullable=True)
    handled_minor_version_id: Mapped[Optional[int]] = mapped_column(ForeignKey("versions.id"), nullable=True)
    handled_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    handled_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    major_version = relationship("Version", foreign_keys=[major_version_id])
    minor_version = relationship("Version", foreign_keys=[minor_version_id])
    handled_major_version = relationship("Version", foreign_keys=[handled_major_version_id])
    handled_minor_version = relationship("Version", foreign_keys=[handled_minor_version_id])

    creator = relationship("User", foreign_keys=[creator_id])
    assignee = relationship("User", foreign_keys=[assignee_id])
    handler = relationship("User", foreign_keys=[handled_by_id])

    attachments = relationship("FeedbackAttachment", back_populates="feedback", cascade="all, delete-orphan")
    bug_links = relationship("FeedbackBugLink", back_populates="feedback", cascade="all, delete-orphan")


class FeedbackAttachment(Base):
    __tablename__ = "feedback_attachments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    feedback_id: Mapped[int] = mapped_column(ForeignKey("feedback_records.id", ondelete="CASCADE"), nullable=False, index=True)
    original_name: Mapped[str] = mapped_column(String(255), nullable=False)
    stored_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    file_path: Mapped[str] = mapped_column(String(500), nullable=False)
    file_type: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    file_ext: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    file_size: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_image: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    uploaded_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    feedback = relationship("FeedbackRecord", back_populates="attachments")
    uploader = relationship("User", foreign_keys=[uploaded_by_id])


class FeedbackBugLink(Base):
    __tablename__ = "feedback_bug_links"
    __table_args__ = (UniqueConstraint("feedback_id", "bug_id", name="uq_feedback_bug_link"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    feedback_id: Mapped[int] = mapped_column(ForeignKey("feedback_records.id", ondelete="CASCADE"), nullable=False, index=True)
    bug_id: Mapped[int] = mapped_column(ForeignKey("bug_tracking.id", ondelete="CASCADE"), nullable=False, index=True)
    created_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    feedback = relationship("FeedbackRecord", back_populates="bug_links")
    bug = relationship("BugTracking")
    creator = relationship("User", foreign_keys=[created_by_id])


__all__ = ["FeedbackRecord", "FeedbackAttachment", "FeedbackBugLink"]
