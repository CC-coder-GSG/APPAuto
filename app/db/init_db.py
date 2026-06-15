from __future__ import annotations

from app.db.base import Base
from app.db.seed import (
    ensure_browser_sync_schema_compat,
    ensure_bug_schema_compat,
    ensure_build_record_schema_compat,
    ensure_default_admin,
    ensure_default_software_and_backfill,
    ensure_requirement_schema_compat,
    ensure_software_schema_compat,
    ensure_stage5_schema_compat,
    ensure_sync_lock_schema_compat,
    ensure_testcase_schema_compat,
    ensure_user_schema_compat,
    ensure_version_schema_compat,
    ensure_zentao_testcase_mirror_schema_compat,
    ensure_zentao_sync_bot,
)
from app.db.session import SessionLocal, engine
from app.models import (
    AuditLog,
    BrowserSyncEvent,
    BuildRecord,
    BugStage5Record,
    BugTracking,
    FieldTestBugLink,
    FieldTestRecord,
    FinalTestRecord,
    FeedbackAttachment,
    FeedbackBugLink,
    FeedbackRecord,
    Requirement,
    RequirementStatusHistory,
    SoftwareProduct,
    TaskBoardTask,
    TaskBoardUpdate,
    TestCase,
    TestExecution,
    User,
    Version,
    ZentaoTestCaseMirror,
)


def init_db() -> None:
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        ensure_software_schema_compat(db)
        ensure_user_schema_compat(db)
        ensure_version_schema_compat(db)
        ensure_requirement_schema_compat(db)
        ensure_stage5_schema_compat(db)
        ensure_bug_schema_compat(db)
        ensure_testcase_schema_compat(db)
        ensure_zentao_testcase_mirror_schema_compat(db)
        ensure_sync_lock_schema_compat(db)
        ensure_browser_sync_schema_compat(db)
        ensure_build_record_schema_compat(db)
        Base.metadata.create_all(bind=engine)
        ensure_default_admin(db)
        ensure_zentao_sync_bot(db)
        ensure_default_software_and_backfill(db)
    finally:
        db.close()
