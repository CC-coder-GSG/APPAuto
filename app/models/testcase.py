from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class TestCase(Base):
    __tablename__ = "test_cases"
    __table_args__ = (UniqueConstraint("zentao_client_record_id", name="uq_test_cases_zentao_client_record_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    requirement_id: Mapped[int] = mapped_column(ForeignKey("requirements.id", ondelete="CASCADE"), nullable=False)
    zentao_case_id: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    zentao_case_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    zentao_client_record_id: Mapped[Optional[str]] = mapped_column(String(120), nullable=True, index=True)
    zentao_source: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    zentao_captured_at: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    zentao_top_href: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    zentao_product_id: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    zentao_product_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    zentao_case_title: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    zentao_requirement_id: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    zentao_requirement_name: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    zentao_creator_name: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    zentao_sync_status: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    zentao_sync_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    zentao_raw_payload: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Zentao anchor: numeric case id for direct API access (e.g. /v1/testcases/{id})
    zentao_case_numeric_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    creator_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    requirement = relationship("Requirement", back_populates="test_cases")


__all__ = ["TestCase"]
