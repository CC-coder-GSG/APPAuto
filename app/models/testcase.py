from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class TestCase(Base):
    __tablename__ = "test_cases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    requirement_id: Mapped[int] = mapped_column(ForeignKey("requirements.id", ondelete="CASCADE"), nullable=False)
    zentao_case_id: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    creator_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    requirement = relationship("Requirement", back_populates="test_cases")


__all__ = ["TestCase"]
