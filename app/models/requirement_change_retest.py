from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.utils.time_utils import local_now


class RequirementChangeRetest(Base):
    """A durable dispatch receipt, including the assignee's previous final-test state."""

    __tablename__ = "requirement_change_retests"
    __table_args__ = (UniqueConstraint("requirement_id", "source_task_id", "story_version",
                                      name="uq_requirement_change_retest_source_version"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    requirement_id: Mapped[int] = mapped_column(ForeignKey("requirements.id", ondelete="CASCADE"), index=True)
    source_task_id: Mapped[int] = mapped_column(Integer)
    task_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    story_version: Mapped[int] = mapped_column(Integer)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    actor_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    # prepared/unknown receipts block completion until remote state is verified.
    status: Mapped[str] = mapped_column(String(24), default="prepared")
    previous_state: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=local_now)
