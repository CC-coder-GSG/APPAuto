from __future__ import annotations

import pytest
import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.core.retest_aggregate_events import AppSession
from app.models import AuditLog, BugStage5Record, BugTracking, Requirement, TestCase, TestExecution, User, Version


@pytest.fixture(autouse=True)
def _clear_stage5_sync_cache():
    """Reset the in-memory sync cache before each test to ensure test isolation."""
    import app.services.stage5_service as svc
    svc._sync_cache.clear()
    yield
    svc._sync_cache.clear()


@pytest.fixture()
def db_session():
    postgres_url = os.environ.get('APP_TEST_DATABASE_URL')
    if postgres_url:
        from sqlalchemy.engine import make_url
        from app.db.engine import build_engine
        from app.db.schema_version import require_schema
        url = make_url(postgres_url)
        if url.get_backend_name() != 'postgresql' or not (url.database or '').endswith('_test'):
            raise RuntimeError('APP_TEST_DATABASE_URL must name an isolated PostgreSQL database ending in _test')
        engine = build_engine(postgres_url)
        require_schema(engine)
        connection = engine.connect()
        transaction = connection.begin()
        session = AppSession(bind=connection, autoflush=False, join_transaction_mode='create_savepoint')
        try:
            from app.db.seed import ensure_zentao_sync_bot
            ensure_zentao_sync_bot(session)
            yield session
        finally:
            session.close()
            transaction.rollback()
            connection.close()
            engine.dispose()
        return
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(class_=AppSession, autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)
    session = TestingSessionLocal()
    try:
        from app.db.seed import ensure_zentao_sync_bot
        ensure_zentao_sync_bot(session)
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()
