from __future__ import annotations

from app.db.base import Base
from app.db.seed import ensure_default_admin
from app.db.session import SessionLocal, engine
from app.models import AuditLog, BugStage5Record, BugTracking, Requirement, TestCase, TestExecution, User, Version


def init_db() -> None:
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        ensure_default_admin(db)
    finally:
        db.close()
