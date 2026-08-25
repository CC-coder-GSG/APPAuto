from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.utils.time_utils import local_now


class FinalTestRecord(Base):
    """
    Per-user × per-requirement checkbox state for the「最终测试 / final test」phase.

    Kept fully independent from `Requirement.case_completed` / `test_completed`
    (which belong to the single assigned owner). When a major version enters
    final-test mode every user re-tests every requirement and ticks their own
    boxes here. Records are preserved when final test is closed, so re-opening
    continues from the last state.
    """

    __tablename__ = "final_test_records"
    __table_args__ = (
        UniqueConstraint("requirement_id", "user_id", name="uq_final_test_records_req_user"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    requirement_id: Mapped[int] = mapped_column(
        ForeignKey("requirements.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)

    case_completed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    test_completed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Anchors the report's "executed requirement" time-bucketing for final test.
    test_completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, index=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=local_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=local_now, onupdate=local_now, nullable=False)

    requirement = relationship("Requirement")
    user = relationship("User")


__all__ = ["FinalTestRecord"]
