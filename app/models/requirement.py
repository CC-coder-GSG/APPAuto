from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, Enum as SAEnum, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.enums import RequirementStatus
from app.utils.time_utils import local_now


class Requirement(Base):
    __tablename__ = "requirements"
    __table_args__ = (UniqueConstraint("major_version_id", "zentao_req_id", name="uq_requirements_major_reqid"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    zentao_req_id: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)

    major_version_id: Mapped[int] = mapped_column(ForeignKey("versions.id", ondelete="CASCADE"), nullable=False)
    owner_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)

    case_completed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    test_completed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    test_completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, index=True)
    retest_completed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    retested_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    retested_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    retest_minor_version_id: Mapped[Optional[int]] = mapped_column(ForeignKey("versions.id"), nullable=True)
    retest_passed: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    test_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # 富文本版测试要点（HTML）。test_notes 保留为纯文本降级：移动端编辑、
    # 数据管理台/报表预览仍读纯文本；网页端保存时两者同时写入。
    test_notes_html: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    test_notes_updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    test_notes_updated_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    status: Mapped[RequirementStatus] = mapped_column(SAEnum(RequirementStatus), default=RequirementStatus.PENDING, nullable=False)

    # Zentao anchor fields
    zentao_story_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    zentao_plan_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    zentao_plan_title_cache: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    # 禅道任务联动（2026-06-29 需求）
    # 预计测试用时（小时），开始任务时默认取此值；默认 4。
    estimated_test_hours: Mapped[float] = mapped_column(Float, default=4.0, nullable=False)
    # 本需求对应的禅道「子任务」id；非空即表示"已建过任务/已分配过"。
    zentao_task_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    # 子任务所属父任务 id，便于看板分组/追溯。
    zentao_parent_task_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # 本地记录的开始/完成时刻（上海本地 naive），用于工时回算与展示。
    task_started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    task_finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    # 暂停时结算的已耗工时累计（小时）。暂停期不计工时：pause 时把「本段开始→暂停」
    # 结算进本字段并清空 task_started_at；完成时总工时 = 本字段 + 最后一段。
    task_consumed_accum: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    # 禅道子任务状态缓存（wait/doing/done…），供测试台标签直接展示，免每次查禅道。
    zentao_task_status_cache: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    # 禅道子任务当前指派人账号缓存；用于「仅指派人本人可开始/完成禅道任务」的权限判定。
    zentao_task_assigned_to: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=local_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=local_now, onupdate=local_now, nullable=False)

    major_version = relationship("Version", back_populates="requirements", foreign_keys=[major_version_id])
    retest_minor_version = relationship("Version", foreign_keys=[retest_minor_version_id])
    owner = relationship("User", back_populates="assigned_requirements", foreign_keys=[owner_id])
    retester = relationship("User", back_populates="retested_requirements", foreign_keys=[retested_by_id])
    test_notes_updated_by = relationship("User", foreign_keys=[test_notes_updated_by_id])
    test_cases = relationship("TestCase", back_populates="requirement", cascade="all, delete-orphan")
    test_executions = relationship("TestExecution", back_populates="requirement", cascade="all, delete-orphan")
    bug_tracks = relationship("BugTracking", back_populates="requirement", cascade="all, delete-orphan")


__all__ = ["Requirement"]
