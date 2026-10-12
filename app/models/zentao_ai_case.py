from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.utils.time_utils import local_now


class AICaseWorkspace(Base):
    __tablename__ = "zentao_ai_case_workspaces"
    __table_args__ = (UniqueConstraint("user_id", "result_id", "base_url"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    result_id: Mapped[int] = mapped_column(ForeignKey("story_ai_results.id"), index=True)
    base_url: Mapped[str] = mapped_column(String(255))
    product_id: Mapped[int] = mapped_column(Integer)
    story_id: Mapped[int] = mapped_column(Integer)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    context_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=local_now)


class AICaseDraft(Base):
    __tablename__ = "zentao_ai_case_drafts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("zentao_ai_case_workspaces.id"), index=True)
    position: Mapped[int] = mapped_column(Integer, default=0)
    content_json: Mapped[str] = mapped_column(Text)
    sources_json: Mapped[str] = mapped_column(Text)
    parents_json: Mapped[str] = mapped_column(Text, default="[]")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    submit_status: Mapped[str] = mapped_column(String(20), default="draft", index=True)
    push_item_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    remote_case_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    warning: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=local_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=local_now, onupdate=local_now)


class AICasePushBatch(Base):
    __tablename__ = "zentao_ai_case_push_batches"
    __table_args__ = (UniqueConstraint("user_id", "request_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("zentao_ai_case_workspaces.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    request_id: Mapped[str] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=local_now)


class AICasePushItem(Base):
    __tablename__ = "zentao_ai_case_push_items"
    __table_args__ = (UniqueConstraint("batch_id", "draft_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    batch_id: Mapped[str] = mapped_column(ForeignKey("zentao_ai_case_push_batches.id"), index=True)
    draft_id: Mapped[str] = mapped_column(ForeignKey("zentao_ai_case_drafts.id"), index=True)
    position: Mapped[int] = mapped_column(Integer, default=0)
    snapshot_json: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    remote_case_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    warning: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=local_now, onupdate=local_now)
