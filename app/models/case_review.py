from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.utils.time_utils import local_now


class TestCaseReview(Base):
    """用例审查记录（审查工作台）。

    以归一化用例号（裸数字，同 workbench_link_service._case_key）为关联键，
    同时覆盖本地 test_cases 与禅道镜像自动归集的用例（后者无本地行）。

    记录按人独立：每个审查人对同一用例只有一条 active 记录（重复审查会
    顶替自己旧记录）。「修改完成」把该用例全部 active 记录归档并追加一条
    fixed 记录 → 用例回到未审查状态，历史仍可查看。
    """

    __tablename__ = "test_case_reviews"
    __table_args__ = (
        Index("ix_test_case_reviews_case_active", "case_key", "active"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    # 归一化用例号（'u#19712' → '19712'）
    case_key: Mapped[str] = mapped_column(String(60), nullable=False, index=True)
    requirement_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("requirements.id", ondelete="SET NULL"), nullable=True, index=True
    )
    reviewer_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    # passed / failed / fixed（fixed=修改完成记录，恒为非 active）
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    # failed→审查意见（必填）；fixed→修改内容说明
    opinion: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=local_now, nullable=False)

    reviewer = relationship("User", foreign_keys=[reviewer_id])
    requirement = relationship("Requirement", foreign_keys=[requirement_id])


__all__ = ["TestCaseReview"]
