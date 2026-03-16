from __future__ import annotations

from app.db.base import Base
from app.db.seed import (
    ensure_default_admin,
    ensure_default_software_and_backfill,
    ensure_requirement_schema_compat,
    ensure_software_schema_compat,
    ensure_user_schema_compat,
)
from app.db.session import SessionLocal, engine
from app.models import (
    AuditLog,
    BuildRecord,
    BugStage5Record,
    BugTracking,
    FieldTestBugLink,
    FieldTestRecord,
    FeedbackAttachment,
    FeedbackBugLink,
    FeedbackRecord,
    Requirement,
    RequirementStatusHistory,
    SoftwareProduct,
    TestCase,
    TestExecution,
    User,
    Version,
)


def init_db() -> None:
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        ensure_software_schema_compat(db)
        ensure_user_schema_compat(db)
        ensure_requirement_schema_compat(db)
        Base.metadata.create_all(bind=engine)
        ensure_default_admin(db)
        ensure_default_software_and_backfill(db)
    finally:
        db.close()
