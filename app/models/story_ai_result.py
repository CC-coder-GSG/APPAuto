from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.utils.time_utils import local_now


class StoryAIResult(Base):
    __tablename__ = "story_ai_results"
    __table_args__ = (
        Index("ix_story_ai_results_batch", "batch_id"),
        Index("ix_story_ai_results_story_created", "story_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    batch_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    story_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    execution_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    execution_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    title: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    briefing: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    module_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    scene_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    stage_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    case_type: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    priority: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    precondition: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # steps: [{"step": "...", "expected": "..."}, ...]
    steps_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    keywords: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    risk_points_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    questions_to_confirm_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    testcase_template: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Full raw JSON object from n8n for this story (or non-JSON raw when parsing failed)
    raw_ai_result_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # pending | success | failed
    ai_status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending", index=True)
    ai_error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_by: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=local_now, nullable=False, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=local_now, onupdate=local_now, nullable=False)


__all__ = ["StoryAIResult"]
