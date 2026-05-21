from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.enums import TaskBoardPriority, TaskBoardStatus
from app.utils.time_utils import local_now


class TaskBoardTask(Base):
    """
    Daily task board entry.

    Independent of `Requirement` lifecycle so it can carry arbitrary daily
    work items, and so the all-users-visible board does not interfere with
    per-owner requirement workflows.
    """

    __tablename__ = "task_board_tasks"
    __table_args__ = (
        Index("ix_task_board_tasks_board_date_status", "board_date", "status", "sort_order"),
        Index("ix_task_board_tasks_assignee_date", "assignee_id", "board_date"),
        Index("ix_task_board_tasks_software_date", "software_id", "board_date"),
        Index("ix_task_board_tasks_major_date", "major_version_id", "board_date"),
        Index("ix_task_board_tasks_target", "target_type", "target_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    board_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    status: Mapped[TaskBoardStatus] = mapped_column(
        SAEnum(TaskBoardStatus), default=TaskBoardStatus.TODO, nullable=False, index=True
    )
    priority: Mapped[TaskBoardPriority] = mapped_column(
        SAEnum(TaskBoardPriority), default=TaskBoardPriority.NORMAL, nullable=False, index=True
    )

    assignee_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    assigner_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    updated_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)

    software_id: Mapped[Optional[int]] = mapped_column(ForeignKey("software_products.id"), nullable=True, index=True)
    major_version_id: Mapped[Optional[int]] = mapped_column(ForeignKey("versions.id"), nullable=True, index=True)

    target_type: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    target_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    target_label_cache: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    due_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    deferred_from_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)

    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    archived: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=local_now, nullable=False, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=local_now, onupdate=local_now, nullable=False)

    assignee = relationship("User", foreign_keys=[assignee_id])
    assigner = relationship("User", foreign_keys=[assigner_id])
    created_by = relationship("User", foreign_keys=[created_by_id])
    updated_by = relationship("User", foreign_keys=[updated_by_id])
    software = relationship("SoftwareProduct", foreign_keys=[software_id])
    major_version = relationship("Version", foreign_keys=[major_version_id])

    updates = relationship(
        "TaskBoardUpdate",
        back_populates="task",
        cascade="all, delete-orphan",
        order_by="TaskBoardUpdate.created_at.desc()",
    )


class TaskBoardUpdate(Base):
    """Free-text progress note attached to a task; separate from audit log."""

    __tablename__ = "task_board_updates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    task_id: Mapped[int] = mapped_column(
        ForeignKey("task_board_tasks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    author_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    status_snapshot: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=local_now, nullable=False, index=True)

    task = relationship("TaskBoardTask", back_populates="updates")
    author = relationship("User", foreign_keys=[author_id])


__all__ = ["TaskBoardTask", "TaskBoardUpdate"]
