from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.utils.time_utils import local_now


class RequirementRetestRecord(Base):
    """
    Per-user retest record for a requirement (交叉复测).

    复测结论改为按"复测人"各自记录，而不是写在 Requirement 行上的共享字段：
    - 每个复测人对一条需求各有一条记录（unique requirement_id + user_id）。
    - 复测台据此显示"我自己"的复测状态，并用标签列出所有已复测的人。

    Requirement 行上的 retest_* 共享字段仍保留为"聚合/最新结论"，供需求状态机、
    报表、看板沿用，避免破坏既有逻辑。
    """

    __tablename__ = "requirement_retest_records"
    __table_args__ = (
        UniqueConstraint("requirement_id", "user_id", name="uq_req_retest_user"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    requirement_id: Mapped[int] = mapped_column(
        ForeignKey("requirements.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)

    passed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    minor_version_id: Mapped[Optional[int]] = mapped_column(ForeignKey("versions.id"), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=local_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=local_now, onupdate=local_now, nullable=False)

    requirement = relationship("Requirement")
    user = relationship("User")
    minor_version = relationship("Version", foreign_keys=[minor_version_id])


__all__ = ["RequirementRetestRecord"]
