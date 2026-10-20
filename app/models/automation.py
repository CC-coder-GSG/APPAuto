"""Versioned Robot catalogue and immutable automation execution records."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.utils.time_utils import utc_now_naive


class AutomationIntegrationCredential(Base):
    __tablename__ = 'automation_integration_credentials'
    __table_args__ = (UniqueConstraint('owner_id', 'jenkins_base_url', name='uq_automation_credential_owner_server'),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey('users.id'), index=True)
    jenkins_base_url: Mapped[str] = mapped_column(String(500))
    key_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now_naive)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now_naive, onupdate=utc_now_naive)


class AutomationRepository(Base):
    __tablename__ = 'automation_repositories'

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey('users.id'), index=True)
    name: Mapped[str] = mapped_column(String(160))
    git_url: Mapped[str] = mapped_column(String(500))
    branch: Mapped[str] = mapped_column(String(160), default='main')
    jenkins_job: Mapped[str] = mapped_column(String(255))
    jenkins_base_url: Mapped[str] = mapped_column(String(500))
    zentao_base_url: Mapped[str] = mapped_column(String(500))
    product_id: Mapped[int] = mapped_column(Integer)
    execution_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    target_build_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    default_testtask_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    writeback_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    integration_key_hash: Mapped[str] = mapped_column(String(64))
    catalog_revision: Mapped[str | None] = mapped_column(String(64), nullable=True)
    catalog_json: Mapped[str] = mapped_column(Text, default='[]')
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now_naive)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now_naive, onupdate=utc_now_naive)


class AutomationRun(Base):
    __tablename__ = 'automation_runs'
    __table_args__ = (UniqueConstraint('repository_id', 'idempotency_key', name='uq_automation_run_request'),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    repository_id: Mapped[int] = mapped_column(ForeignKey('automation_repositories.id'), index=True)
    requested_by_id: Mapped[int] = mapped_column(ForeignKey('users.id'))
    idempotency_key: Mapped[str] = mapped_column(String(100))
    request_hash: Mapped[str] = mapped_column(String(64))
    source: Mapped[str] = mapped_column(String(20), default='platform')
    external_actor: Mapped[str | None] = mapped_column(String(120), nullable=True)
    dry_run: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(30), default='created', index=True)
    revision: Mapped[str] = mapped_column(String(64))
    branch: Mapped[str] = mapped_column(String(160))
    testtask_id: Mapped[int] = mapped_column(Integer)
    device_serial: Mapped[str] = mapped_column(String(160))
    apk_reference: Mapped[str] = mapped_column(String(500))
    plan_json: Mapped[str] = mapped_column(Text)
    parameters_json: Mapped[str] = mapped_column(Text, default='{}')
    queue_url: Mapped[str | None] = mapped_column(String(700), nullable=True)
    build_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    build_url: Mapped[str | None] = mapped_column(String(700), nullable=True)
    result_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    summary_json: Mapped[str] = mapped_column(Text, default='{}')
    message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now_naive, index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class AutomationCaseResult(Base):
    __tablename__ = 'automation_case_results'
    __table_args__ = (UniqueConstraint('run_id', 'case_id', name='uq_automation_result_case'),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey('automation_runs.id'), index=True)
    case_id: Mapped[int] = mapped_column(Integer)
    case_version: Mapped[int] = mapped_column(Integer)
    test_name: Mapped[str] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(20))
    duration_seconds: Mapped[str] = mapped_column(String(40))
    message: Mapped[str] = mapped_column(Text, default='')
    evidence_json: Mapped[str] = mapped_column(Text, default='{}')
    sync_status: Mapped[str] = mapped_column(String(20), default='pending', index=True)
    sync_message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    zentao_result_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    synced_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
