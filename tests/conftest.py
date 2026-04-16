from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
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
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()
