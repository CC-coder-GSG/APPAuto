from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.utils.time_utils import local_now


class CompetitorAnalysisReport(Base):
    """Shared, versioned document used by the competitor analysis workbench."""

    __tablename__ = "competitor_analysis_reports"

    report_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    data_json: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    updated_by_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    updated_by_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=local_now, nullable=False)


__all__ = ["CompetitorAnalysisReport"]
