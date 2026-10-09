"""Bugly capture data is independent of the existing defect workflow."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.utils.time_utils import utc_now_naive


class BuglyApplication(Base):
    __tablename__ = "bugly_applications"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    source_host: Mapped[str] = mapped_column(String(255), default="bugly.qq.com")
    bugly_app_id: Mapped[Optional[str]] = mapped_column(String(100))
    package_name: Mapped[Optional[str]] = mapped_column(String(255))
    platform: Mapped[str] = mapped_column(String(20), default="Android")
    source_timezone: Mapped[str] = mapped_column(String(64), default="Asia/Shanghai")
    software_id: Mapped[Optional[int]] = mapped_column(ForeignKey("software_products.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now_naive)


class BuglyIssue(Base):
    __tablename__ = "bugly_issues"
    __table_args__ = (UniqueConstraint("application_id", "source_issue_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    application_id: Mapped[int] = mapped_column(ForeignKey("bugly_applications.id"), index=True)
    source_issue_id: Mapped[Optional[str]] = mapped_column(String(100))
    title: Mapped[str] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(30), default="pending", index=True)
    exception_type: Mapped[str] = mapped_column(String(100), default="待确认")
    owner_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"))
    analysis: Mapped[str] = mapped_column(Text, default="")
    external_note: Mapped[str] = mapped_column(Text, default="")
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now_naive)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now_naive, onupdate=utc_now_naive)
    __mapper_args__ = {"version_id_col": version}


class BuglyReport(Base):
    __tablename__ = "bugly_reports"
    __table_args__ = (UniqueConstraint("issue_id", "source_report_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    issue_id: Mapped[int] = mapped_column(ForeignKey("bugly_issues.id"), index=True)
    source_report_id: Mapped[Optional[str]] = mapped_column(String(100))
    app_version: Mapped[str] = mapped_column(String(255), default="", index=True)
    device: Mapped[str] = mapped_column(String(255), default="", index=True)
    os_version: Mapped[str] = mapped_column(String(255), default="")
    occurred_at: Mapped[Optional[datetime]] = mapped_column(DateTime, index=True)
    occurred_at_raw: Mapped[str] = mapped_column(String(255), default="")
    collected_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    stack_text: Mapped[str] = mapped_column(Text, default="")
    stack_mode: Mapped[str] = mapped_column(String(30), default="manual")
    frame_count: Mapped[int] = mapped_column(Integer, default=0)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    content_hash: Mapped[str] = mapped_column(String(64))
    revision: Mapped[int] = mapped_column(Integer, default=1)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now_naive, onupdate=utc_now_naive)
    __mapper_args__ = {"version_id_col": revision}


class BuglyReportRevision(Base):
    __tablename__ = "bugly_report_revisions"
    id: Mapped[int] = mapped_column(primary_key=True)
    report_id: Mapped[int] = mapped_column(ForeignKey("bugly_reports.id"), index=True)
    revision: Mapped[int] = mapped_column(Integer)
    payload: Mapped[dict] = mapped_column(JSON)
    actor_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    batch_id: Mapped[Optional[str]] = mapped_column(String(36), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now_naive)


class BuglyImportBatch(Base):
    __tablename__ = "bugly_import_batches"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    application_id: Mapped[int] = mapped_column(ForeignKey("bugly_applications.id"), index=True)
    actor_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    filename: Mapped[str] = mapped_column(String(255))
    file_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20), default="preview")
    items: Mapped[list] = mapped_column(JSON, default=list)
    diagnostics: Mapped[dict] = mapped_column(JSON, default=dict)
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now_naive)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    confirmed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)


class BuglyGrant(Base):
    __tablename__ = "bugly_grants"
    __table_args__ = (UniqueConstraint("user_id", "application_id", "scope"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    application_id: Mapped[int] = mapped_column(ForeignKey("bugly_applications.id"), index=True)
    issue_id: Mapped[Optional[int]] = mapped_column(ForeignKey("bugly_issues.id"), index=True)
    scope: Mapped[str] = mapped_column(String(100), default="*")
    role: Mapped[str] = mapped_column(String(20), default="viewer")
    can_sensitive: Mapped[bool] = mapped_column(Boolean, default=False)
    can_publish: Mapped[bool] = mapped_column(Boolean, default=False)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    granted_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now_naive)


class BuglyPublication(Base):
    __tablename__ = "bugly_publications"
    issue_id: Mapped[int] = mapped_column(ForeignKey("bugly_issues.id"), primary_key=True)
    snapshot: Mapped[dict] = mapped_column(JSON)
    source_version: Mapped[int] = mapped_column(Integer)
    version: Mapped[int] = mapped_column(Integer, default=1)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    published_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    published_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now_naive)
