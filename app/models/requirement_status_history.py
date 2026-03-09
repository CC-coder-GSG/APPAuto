from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Enum as SAEnum, ForeignKey, Integer
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.enums import RequirementStatus


class RequirementStatusHistory(Base):
    __tablename__ = "requirement_status_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    requirement_id: Mapped[int] = mapped_column(ForeignKey("requirements.id", ondelete="CASCADE"), nullable=False, index=True)
    from_status: Mapped[Optional[RequirementStatus]] = mapped_column(SAEnum(RequirementStatus), nullable=True)
    to_status: Mapped[RequirementStatus] = mapped_column(SAEnum(RequirementStatus), nullable=False, index=True)
    changed_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    changed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False, index=True)

    requirement = relationship("Requirement")
    changed_by = relationship("User", foreign_keys=[changed_by_id])


__all__ = ["RequirementStatusHistory"]

