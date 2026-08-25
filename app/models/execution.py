from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Enum as SAEnum, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.enums import TestResultStatus
from app.utils.time_utils import local_now


class TestExecution(Base):
    __tablename__ = "test_executions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    requirement_id: Mapped[int] = mapped_column(ForeignKey("requirements.id", ondelete="CASCADE"), nullable=False)
    minor_version_id: Mapped[int] = mapped_column(ForeignKey("versions.id", ondelete="CASCADE"), nullable=False)

    bug_id: Mapped[Optional[str]] = mapped_column(String(30), nullable=True, index=True)
    source_case_id: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    result_status: Mapped[TestResultStatus] = mapped_column(SAEnum(TestResultStatus), default=TestResultStatus.UNTESTED, nullable=False)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    executed_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    executed_at: Mapped[datetime] = mapped_column(DateTime, default=local_now, nullable=False)

    requirement = relationship("Requirement", back_populates="test_executions")
    minor_version = relationship("Version", back_populates="executions")


__all__ = ["TestExecution"]
