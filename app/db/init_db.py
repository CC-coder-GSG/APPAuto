from __future__ import annotations

from app.db.base import Base
from app.db.seed import (
    ensure_browser_sync_schema_compat,
    ensure_bug_schema_compat,
    ensure_build_record_schema_compat,
    ensure_cad_schema_compat,
    ensure_default_admin,
    ensure_default_software_and_backfill,
    ensure_feature_tree_schema_compat,
    ensure_requirement_schema_compat,
    ensure_requirement_foreign_key_compat,
    repair_software_product_mappings_from_bug_history,
    ensure_software_schema_compat,
    ensure_stage5_schema_compat,
    ensure_sync_lock_schema_compat,
    ensure_testcase_schema_compat,
    ensure_user_schema_compat,
    ensure_version_schema_compat,
    ensure_zentao_task_mirror_schema_compat,
    ensure_zentao_testcase_mirror_schema_compat,
    ensure_zentao_sync_bot,
)
from app.db.session import SessionLocal, engine
from app.db.workbench_indexes import ensure_workbench_indexes
from app.models import (
    AuditLog,
    BrowserSyncEvent,
    BuildRecord,
    BugStage5Record,
    BugTracking,
    Assessment,
    AssessmentAnswer,
    AssessmentQuestion,
    AssessmentSubmission,
    FieldTestBugLink,
    FieldTestRecord,
    FinalTestRecord,
    LearningMaterial,
    LearningTopic,
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
    from app.db.bugly_upgrade import backup_before_bugly_upgrade
    backup_before_bugly_upgrade(engine)
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        from app.db.final_test_upgrade import ensure_final_test_schema
        ensure_final_test_schema(db)
        ensure_software_schema_compat(db)
        ensure_user_schema_compat(db)
        ensure_version_schema_compat(db)
        ensure_requirement_schema_compat(db)
        ensure_requirement_foreign_key_compat(db)
        ensure_stage5_schema_compat(db)
        ensure_bug_schema_compat(db)
        ensure_testcase_schema_compat(db)
        ensure_zentao_testcase_mirror_schema_compat(db)
        ensure_zentao_task_mirror_schema_compat(db)
        ensure_sync_lock_schema_compat(db)
        ensure_browser_sync_schema_compat(db)
        ensure_build_record_schema_compat(db)
        Base.metadata.create_all(bind=engine)
        ensure_workbench_indexes(db)
        ensure_feature_tree_schema_compat(db)
        ensure_cad_schema_compat(db)
        ensure_default_admin(db)
        ensure_zentao_sync_bot(db)
        ensure_default_software_and_backfill(db)
        repair_software_product_mappings_from_bug_history(db)
        # Backfill before serving read-only workbenches. Also repairs old rows
        # on startup after an interrupted import or an out-of-band DB change.
        from app.services.retest_aggregate_service import RetestAggregateService
        RetestAggregateService(db).reconcile_all(commit_batches=True)
    finally:
        db.close()
