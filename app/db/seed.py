from __future__ import annotations

import logging

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import hash_password
from app.models import SoftwareProduct, User, UserRole, Version, VersionType

DEFAULT_ADMIN_USERNAME = "admin"
DEFAULT_ADMIN_PASSWORD = "admin"
DEFAULT_SOFTWARE_NAME = "Survey Master"
ZENTAO_SYNC_BOT_USERNAME = "zentao_sync_bot"
ZENTAO_SYNC_BOT_DISPLAY_NAME = "禅道同步"
UNCLASSIFIED_MAJOR_VERSION_NO = "V0.0.0-unclassified"
logger = logging.getLogger(__name__)


def ensure_default_admin(db: Session) -> None:
    admin_user = db.query(User).filter(User.username == DEFAULT_ADMIN_USERNAME).first()
    if admin_user or not settings.allow_default_admin_seed:
        return

    db.add(
        User(
            username=DEFAULT_ADMIN_USERNAME,
            password_hash=hash_password(DEFAULT_ADMIN_PASSWORD),
            role=UserRole.ADMIN,
        )
    )
    db.commit()


def ensure_zentao_sync_bot(db: Session) -> User:
    """
    Make sure a dedicated system user exists for bugs synced from Zentao whose
    opener cannot be mapped to a real local account.
    """
    bot = db.query(User).filter(User.username == ZENTAO_SYNC_BOT_USERNAME).first()
    if bot:
        return bot

    from app.core.security import hash_password as _hash

    bot = User(
        username=ZENTAO_SYNC_BOT_USERNAME,
        password_hash=_hash("!locked-no-login!"),
        role=UserRole.USER,
        display_name=ZENTAO_SYNC_BOT_DISPLAY_NAME,
        is_team_member=False,
    )
    db.add(bot)
    db.commit()
    db.refresh(bot)
    logger.info("Seeded zentao_sync_bot user id=%s", bot.id)
    return bot


def ensure_software_schema_compat(db: Session) -> None:
    rows = db.execute(text("PRAGMA table_info(versions)")).fetchall()
    cols = {r[1] for r in rows}
    if "software_id" not in cols:
        db.execute(text("ALTER TABLE versions ADD COLUMN software_id INTEGER"))
        db.commit()


def ensure_version_schema_compat(db: Session) -> None:
    """Add final-test phase columns to historical `versions` tables."""
    rows = db.execute(text("PRAGMA table_info(versions)")).fetchall()
    cols = {r[1] for r in rows}
    if "final_test_enabled" not in cols:
        db.execute(text("ALTER TABLE versions ADD COLUMN final_test_enabled BOOLEAN NOT NULL DEFAULT 0"))
        db.commit()
    if "final_test_started_at" not in cols:
        db.execute(text("ALTER TABLE versions ADD COLUMN final_test_started_at DATETIME"))
        db.commit()


def ensure_feature_tree_schema_compat(db: Session) -> None:
    """给历史 feature_tree_marks 表补 is_auto 列（自动汇总标记标识）。"""
    rows = db.execute(text("PRAGMA table_info(feature_tree_marks)")).fetchall()
    if not rows:
        return  # 表尚未建立（全新库由 create_all 直接建出含该列）
    cols = {r[1] for r in rows}
    if "is_auto" not in cols:
        db.execute(text("ALTER TABLE feature_tree_marks ADD COLUMN is_auto BOOLEAN NOT NULL DEFAULT 0"))
        db.commit()


def ensure_user_schema_compat(db: Session) -> None:
    rows = db.execute(text("PRAGMA table_info(users)")).fetchall()
    cols = {r[1] for r in rows}
    if "display_name" not in cols:
        db.execute(text("ALTER TABLE users ADD COLUMN display_name VARCHAR(80)"))
        db.commit()
    if "tab_permissions" not in cols:
        db.execute(text("ALTER TABLE users ADD COLUMN tab_permissions TEXT"))
        db.commit()
    if "session_token_mobile" not in cols:
        db.execute(text("ALTER TABLE users ADD COLUMN session_token_mobile VARCHAR(36)"))
        db.commit()
    if "zentao_account" not in cols:
        db.execute(text("ALTER TABLE users ADD COLUMN zentao_account VARCHAR(120)"))
        db.commit()
    db.execute(text("UPDATE users SET display_name = username WHERE display_name IS NULL OR TRIM(display_name) = ''"))
    db.commit()


def ensure_default_software_and_backfill(db: Session) -> None:
    software = db.query(SoftwareProduct).filter(SoftwareProduct.name == DEFAULT_SOFTWARE_NAME).first()
    if not software:
        software = SoftwareProduct(name=DEFAULT_SOFTWARE_NAME)
        db.add(software)
        db.commit()
        db.refresh(software)

    db.execute(
        text(
            "UPDATE versions SET software_id = :sid "
            "WHERE version_type = :major AND (software_id IS NULL OR software_id = 0)"
        ),
        {"sid": software.id, "major": VersionType.MAJOR.value},
    )
    db.execute(
        text(
            "UPDATE versions SET software_id = ("
            "  SELECT p.software_id FROM versions p WHERE p.id = versions.parent_id"
            ") "
            "WHERE version_type = :minor AND (software_id IS NULL OR software_id = 0)"
        ),
        {"minor": VersionType.MINOR.value},
    )
    db.commit()


def ensure_requirement_schema_compat(db: Session) -> None:
    """
    Keep historical SQLite databases compatible:
    - add incremental columns
    - rebuild the table only when the old global unique constraint on
      zentao_req_id still exists
    """
    req_cols_rows = db.execute(text("PRAGMA table_info(requirements)")).fetchall()
    req_cols = {r[1] for r in req_cols_rows}
    if "test_notes" not in req_cols:
        db.execute(text("ALTER TABLE requirements ADD COLUMN test_notes TEXT"))
        db.commit()
    if "test_notes_updated_at" not in req_cols:
        db.execute(text("ALTER TABLE requirements ADD COLUMN test_notes_updated_at DATETIME"))
        db.commit()
    if "test_notes_updated_by_id" not in req_cols:
        db.execute(text("ALTER TABLE requirements ADD COLUMN test_notes_updated_by_id INTEGER"))
        db.commit()
    if "zentao_story_id" not in req_cols:
        db.execute(text("ALTER TABLE requirements ADD COLUMN zentao_story_id INTEGER"))
        db.commit()
    if "zentao_plan_id" not in req_cols:
        db.execute(text("ALTER TABLE requirements ADD COLUMN zentao_plan_id INTEGER"))
        db.commit()
    if "zentao_plan_title_cache" not in req_cols:
        db.execute(text("ALTER TABLE requirements ADD COLUMN zentao_plan_title_cache VARCHAR(255)"))
        db.commit()
    # 禅道任务联动（2026-06-29）
    if "estimated_test_hours" not in req_cols:
        db.execute(text("ALTER TABLE requirements ADD COLUMN estimated_test_hours FLOAT NOT NULL DEFAULT 4.0"))
        db.commit()
    if "zentao_task_id" not in req_cols:
        db.execute(text("ALTER TABLE requirements ADD COLUMN zentao_task_id INTEGER"))
        db.commit()
    if "zentao_parent_task_id" not in req_cols:
        db.execute(text("ALTER TABLE requirements ADD COLUMN zentao_parent_task_id INTEGER"))
        db.commit()
    if "task_started_at" not in req_cols:
        db.execute(text("ALTER TABLE requirements ADD COLUMN task_started_at DATETIME"))
        db.commit()
    if "task_finished_at" not in req_cols:
        db.execute(text("ALTER TABLE requirements ADD COLUMN task_finished_at DATETIME"))
        db.commit()
    if "zentao_task_status_cache" not in req_cols:
        db.execute(text("ALTER TABLE requirements ADD COLUMN zentao_task_status_cache VARCHAR(20)"))
        db.commit()
    if "zentao_task_assigned_to" not in req_cols:
        db.execute(text("ALTER TABLE requirements ADD COLUMN zentao_task_assigned_to VARCHAR(120)"))
        db.commit()
    if "test_completed_at" not in req_cols:
        db.execute(text("ALTER TABLE requirements ADD COLUMN test_completed_at DATETIME"))
        # Backfill existing test_completed=True rows with their updated_at as a
        # best-effort cutoff; the retest evidence collector tolerates a missing
        # value but a coarse anchor is much better than nothing.
        db.execute(text("UPDATE requirements SET test_completed_at = updated_at WHERE test_completed = 1 AND test_completed_at IS NULL"))
        db.commit()

    idx_rows = db.execute(text("PRAGMA index_list(requirements)")).fetchall()
    has_target_unique = False
    has_global_unique = False
    for r in idx_rows:
        idx_name = r[1]
        is_unique = bool(r[2])
        cols_rows = db.execute(text(f"PRAGMA index_info('{idx_name}')")).fetchall()
        cols = [c[2] for c in cols_rows]
        if is_unique and cols == ["major_version_id", "zentao_req_id"]:
            has_target_unique = True
        if is_unique and cols == ["zentao_req_id"]:
            has_global_unique = True

    if has_target_unique or not has_global_unique:
        return

    db.execute(text("PRAGMA foreign_keys=OFF"))
    try:
        db.execute(text("ALTER TABLE requirements RENAME TO requirements_old"))
        db.execute(
            text(
                """
                CREATE TABLE requirements (
                    id INTEGER NOT NULL PRIMARY KEY,
                    zentao_req_id VARCHAR(20) NOT NULL,
                    title VARCHAR(255) NOT NULL,
                    major_version_id INTEGER NOT NULL,
                    owner_id INTEGER,
                    case_completed BOOLEAN NOT NULL DEFAULT 0,
                    test_completed BOOLEAN NOT NULL DEFAULT 0,
                    retest_completed BOOLEAN NOT NULL DEFAULT 0,
                    retested_by_id INTEGER,
                    retested_at DATETIME,
                    retest_minor_version_id INTEGER,
                    retest_passed BOOLEAN,
                    test_notes TEXT,
                    test_notes_updated_at DATETIME,
                    test_notes_updated_by_id INTEGER,
                    status VARCHAR(50) NOT NULL DEFAULT 'pending',
                    created_at DATETIME NOT NULL,
                    updated_at DATETIME NOT NULL,
                    CONSTRAINT uq_requirements_major_reqid UNIQUE (major_version_id, zentao_req_id),
                    FOREIGN KEY(major_version_id) REFERENCES versions (id) ON DELETE CASCADE,
                    FOREIGN KEY(owner_id) REFERENCES users (id),
                    FOREIGN KEY(retested_by_id) REFERENCES users (id),
                    FOREIGN KEY(test_notes_updated_by_id) REFERENCES users (id),
                    FOREIGN KEY(retest_minor_version_id) REFERENCES versions (id)
                )
                """
            )
        )
        db.execute(
            text(
                """
                INSERT INTO requirements (
                    id, zentao_req_id, title, major_version_id, owner_id,
                    case_completed, test_completed, retest_completed,
                    retested_by_id, retested_at, retest_minor_version_id, retest_passed,
                    test_notes, test_notes_updated_at, test_notes_updated_by_id,
                    status, created_at, updated_at
                )
                SELECT
                    id, zentao_req_id, title, major_version_id, owner_id,
                    case_completed, test_completed, retest_completed,
                    retested_by_id, retested_at, retest_minor_version_id, retest_passed,
                    test_notes, test_notes_updated_at, test_notes_updated_by_id,
                    status, created_at, updated_at
                FROM requirements_old
                """
            )
        )
        db.execute(text("DROP TABLE requirements_old"))
        db.execute(text("CREATE INDEX IF NOT EXISTS ix_requirements_zentao_req_id ON requirements (zentao_req_id)"))
        db.execute(text("CREATE INDEX IF NOT EXISTS ix_requirements_id ON requirements (id)"))
        db.commit()
    finally:
        db.execute(text("PRAGMA foreign_keys=ON"))
        db.commit()


def ensure_stage5_schema_compat(db: Session) -> None:
    rows = db.execute(text("PRAGMA table_info(bug_stage5_records)")).fetchall()
    cols = {r[1] for r in rows}
    if "source" not in cols:
        db.execute(text("ALTER TABLE bug_stage5_records ADD COLUMN source VARCHAR(20) NOT NULL DEFAULT 'manual'"))
        db.commit()
    if "comment" not in cols:
        db.execute(text("ALTER TABLE bug_stage5_records ADD COLUMN comment VARCHAR"))
        db.commit()


def ensure_bug_schema_compat(db: Session) -> None:
    rows = db.execute(text("PRAGMA table_info(bug_tracking)")).fetchall()
    cols = {r[1] for r in rows}
    column_defs = {
        "zentao_bug_id": "VARCHAR(40)",
        "zentao_bug_url": "TEXT",
        "zentao_client_record_id": "VARCHAR(120)",
        "zentao_source": "VARCHAR(40)",
        "zentao_captured_at": "INTEGER",
        "zentao_top_href": "TEXT",
        "zentao_product_id": "VARCHAR(40)",
        "zentao_product_name": "VARCHAR(255)",
        "zentao_project_id": "VARCHAR(40)",
        "zentao_project_name": "VARCHAR(255)",
        "zentao_opened_build_ids": "TEXT",
        "zentao_affected_version": "VARCHAR(255)",
        "zentao_bug_title": "TEXT",
        "zentao_source_type": "VARCHAR(30)",
        "zentao_linked_case_id": "VARCHAR(40)",
        "zentao_linked_case_label": "VARCHAR(120)",
        "zentao_linked_case_href": "TEXT",
        "zentao_display_bucket": "VARCHAR(20)",
        "zentao_execution_id": "VARCHAR(80)",
        "zentao_execution_name": "VARCHAR(255)",
        "zentao_requirement_id": "VARCHAR(40)",
        "zentao_requirement_name": "TEXT",
        "zentao_creator_name": "VARCHAR(100)",
        "zentao_sync_status": "VARCHAR(40)",
        "zentao_sync_message": "TEXT",
        "zentao_sync_source": "VARCHAR(40)",
        "zentao_raw_payload": "TEXT",
        "last_zentao_synced_at": "DATETIME",
        "zentao_live_status": "VARCHAR(40)",
        "zentao_deleted": "BOOLEAN NOT NULL DEFAULT 0",
        "zentao_closed_by_account": "VARCHAR(100)",
        "zentao_closed_by_name": "VARCHAR(100)",
        "zentao_close_date": "DATETIME",
        "zentao_close_comment": "TEXT",
        "zentao_assigned_to_account": "VARCHAR(100)",
        "zentao_assigned_to_name": "VARCHAR(100)",
        "zentao_remote_updated_at": "DATETIME",
        "zentao_opened_at": "DATETIME",
        "zentao_opened_by_account": "VARCHAR(100)",
        "zentao_opened_by_name": "VARCHAR(100)",
    }
    for col, sql_type in column_defs.items():
        if col not in cols:
            db.execute(text(f"ALTER TABLE bug_tracking ADD COLUMN {col} {sql_type}"))
            db.commit()

    try:
        db.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_bug_tracking_zentao_bug_id ON bug_tracking (zentao_bug_id)"))
        db.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_bug_tracking_zentao_client_record_id ON bug_tracking (zentao_client_record_id)"))
        db.execute(text("CREATE INDEX IF NOT EXISTS ix_bug_tracking_zentao_opened_at ON bug_tracking (zentao_opened_at)"))
        db.commit()
    except Exception:
        db.rollback()
        logger.warning("bug_tracking unique index creation failed; skipped.", exc_info=True)

    result = db.execute(
        text(
            """
            UPDATE bug_tracking
            SET source_ref = CAST((
                SELECT bse.mapped_test_case_id
                FROM browser_sync_events bse
                WHERE bse.client_record_id = bug_tracking.zentao_client_record_id
                  AND bse.mapped_test_case_id IS NOT NULL
            ) AS TEXT)
            WHERE source_type = 'CASE'
              AND (source_ref IS NULL OR source_ref = '')
              AND EXISTS (
                  SELECT 1 FROM browser_sync_events bse
                  WHERE bse.client_record_id = bug_tracking.zentao_client_record_id
                    AND bse.mapped_test_case_id IS NOT NULL
              )
            """
        )
    )
    if result.rowcount:
        db.commit()
        logger.info("Backfilled bug_tracking.source_ref rows=%s", result.rowcount)


def ensure_testcase_schema_compat(db: Session) -> None:
    rows = db.execute(text("PRAGMA table_info(test_cases)")).fetchall()
    cols = {r[1] for r in rows}
    column_defs = {
        "zentao_case_url": "TEXT",
        "zentao_client_record_id": "VARCHAR(120)",
        "zentao_source": "VARCHAR(40)",
        "zentao_captured_at": "INTEGER",
        "zentao_top_href": "TEXT",
        "zentao_product_id": "VARCHAR(40)",
        "zentao_product_name": "VARCHAR(255)",
        "zentao_case_title": "TEXT",
        "zentao_requirement_id": "VARCHAR(40)",
        "zentao_requirement_name": "TEXT",
        "zentao_creator_name": "VARCHAR(100)",
        "zentao_sync_status": "VARCHAR(40)",
        "zentao_sync_message": "TEXT",
        "zentao_raw_payload": "TEXT",
    }
    for col, sql_type in column_defs.items():
        if col not in cols:
            db.execute(text(f"ALTER TABLE test_cases ADD COLUMN {col} {sql_type}"))
            db.commit()

    try:
        db.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_test_cases_zentao_client_record_id ON test_cases (zentao_client_record_id)"))
        db.commit()
    except Exception:
        db.rollback()
        logger.warning("test_cases unique index creation failed; skipped.", exc_info=True)


def ensure_zentao_task_mirror_schema_compat(db: Session) -> None:
    """给历史 zentao_task_mirror 表补完成者列（周报展示真正做完任务的人）。"""
    rows = db.execute(text("PRAGMA table_info(zentao_task_mirror)")).fetchall()
    cols = {r[1] for r in rows}
    if not cols:
        return  # 表尚未建立（全新库由 create_all 直接建出含该列）
    for col in ("finished_by", "finished_by_realname"):
        if col not in cols:
            db.execute(text(f"ALTER TABLE zentao_task_mirror ADD COLUMN {col} VARCHAR(120)"))
            db.commit()


def ensure_zentao_testcase_mirror_schema_compat(db: Session) -> None:
    rows = db.execute(text("PRAGMA table_info(zentao_testcase_mirror)")).fetchall()
    cols = {r[1] for r in rows}
    if not cols:
        return

    column_defs = {
        "zentao_case_id": "VARCHAR(30)",
        "zentao_case_numeric_id": "INTEGER",
        "zentao_product_id": "INTEGER",
        "zentao_product_name": "VARCHAR(255)",
        "zentao_module_id": "INTEGER",
        "zentao_module_name": "VARCHAR(255)",
        "zentao_story_id": "INTEGER",
        "zentao_execution_id": "INTEGER",
        "title": "TEXT",
        "case_type": "VARCHAR(40)",
        "stage": "VARCHAR(40)",
        "status": "VARCHAR(40)",
        "pri": "INTEGER",
        "precondition": "TEXT",
        "steps_digest": "TEXT",
        "last_runner_account": "VARCHAR(100)",
        "last_runner_name": "VARCHAR(100)",
        "last_run_date": "DATETIME",
        "last_run_result": "VARCHAR(40)",
        "bugs_count": "INTEGER",
        "zentao_case_url": "TEXT",
        "deleted": "BOOLEAN NOT NULL DEFAULT 0",
        "remote_opened_at": "DATETIME",
        "remote_updated_at": "DATETIME",
        "last_zentao_synced_at": "DATETIME",
        "sync_source": "VARCHAR(40)",
        "raw_payload": "TEXT",
        "created_at": "DATETIME",
        "updated_at": "DATETIME",
    }
    for col, sql_type in column_defs.items():
        if col not in cols:
            db.execute(text(f"ALTER TABLE zentao_testcase_mirror ADD COLUMN {col} {sql_type}"))
            db.commit()

    try:
        db.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_zentao_testcase_mirror_case_numeric_id ON zentao_testcase_mirror (zentao_case_numeric_id)"))
        db.execute(text("CREATE INDEX IF NOT EXISTS ix_zentao_testcase_mirror_product_id ON zentao_testcase_mirror (zentao_product_id)"))
        db.execute(text("CREATE INDEX IF NOT EXISTS ix_zentao_testcase_mirror_story_id ON zentao_testcase_mirror (zentao_story_id)"))
        db.execute(text("CREATE INDEX IF NOT EXISTS ix_zentao_testcase_mirror_status ON zentao_testcase_mirror (status)"))
        db.execute(text("CREATE INDEX IF NOT EXISTS ix_zentao_testcase_mirror_remote_updated_at ON zentao_testcase_mirror (remote_updated_at)"))
        db.execute(text("CREATE INDEX IF NOT EXISTS ix_zentao_testcase_mirror_last_synced_at ON zentao_testcase_mirror (last_zentao_synced_at)"))
        db.commit()
    except Exception:
        db.rollback()
        logger.warning("zentao_testcase_mirror index creation failed; skipped.", exc_info=True)


def ensure_sync_lock_schema_compat(db: Session) -> None:
    """
    Make sure the sync_locks table exists. Creating it via Base.metadata is
    enough on a fresh DB; this helper just adds a safety net for older
    databases that booted before this table was introduced.
    """
    rows = db.execute(text("PRAGMA table_info(sync_locks)")).fetchall()
    cols = {r[1] for r in rows}
    if cols:
        return
    try:
        db.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS sync_locks (
                    key VARCHAR(120) NOT NULL PRIMARY KEY,
                    holder VARCHAR(120),
                    acquired_at DATETIME NOT NULL,
                    ttl_seconds INTEGER NOT NULL DEFAULT 600
                )
                """
            )
        )
        db.commit()
    except Exception:
        db.rollback()
        logger.warning("sync_locks table creation failed; skipped.", exc_info=True)


def ensure_browser_sync_schema_compat(db: Session) -> None:
    rows = db.execute(text("PRAGMA table_info(browser_sync_events)")).fetchall()
    cols = {r[1] for r in rows}
    if not cols:
        return

    column_defs = {
        "page_type": "VARCHAR(60)",
        "zentao_requirement_name": "TEXT",
        "zentao_product_name": "VARCHAR(255)",
        "zentao_project_name": "VARCHAR(255)",
        "zentao_execution_name": "VARCHAR(255)",
        "zentao_affected_version": "VARCHAR(255)",
        "zentao_case_title": "TEXT",
        "zentao_bug_title": "TEXT",
        "source_type": "VARCHAR(30)",
        "linked_case_id": "VARCHAR(40)",
        "linked_case_label": "VARCHAR(120)",
        "linked_case_href": "TEXT",
        "display_bucket": "VARCHAR(20) DEFAULT 'overall'",
        "mapped_source_type": "VARCHAR(30)",
        "mapped_source_ref": "VARCHAR(80)",
        "mapped_test_case_id": "INTEGER",
    }
    for col, sql_type in column_defs.items():
        if col not in cols:
            db.execute(text(f"ALTER TABLE browser_sync_events ADD COLUMN {col} {sql_type}"))
            db.commit()

    db.execute(text("CREATE INDEX IF NOT EXISTS ix_browser_sync_events_display_bucket ON browser_sync_events (display_bucket)"))
    db.commit()


def ensure_build_record_schema_compat(db: Session) -> None:
    rows = db.execute(text("PRAGMA table_info(build_records)")).fetchall()
    cols = {r[1] for r in rows}
    if not cols:
        return
    column_defs = {
        "zentao_push_status": "VARCHAR(30)",
        "zentao_push_message": "VARCHAR(500)",
        "zentao_pushed_at": "DATETIME",
    }
    for col, sql_type in column_defs.items():
        if col not in cols:
            db.execute(text(f"ALTER TABLE build_records ADD COLUMN {col} {sql_type}"))
            db.commit()


def ensure_cad_schema_compat(db: Session) -> None:
    """为已存在的 cad_item_files 表补齐「文件夹归档」相关列（folder_id / rel_path）。
    cad_item_folders 新表由 create_all 自动建立，此处仅处理旧表的列增量。"""
    rows = db.execute(text("PRAGMA table_info(cad_item_files)")).fetchall()
    cols = {r[1] for r in rows}
    if not cols:
        return
    column_defs = {
        "folder_id": "INTEGER",
        "rel_path": "TEXT",
    }
    for col, sql_type in column_defs.items():
        if col not in cols:
            db.execute(text(f"ALTER TABLE cad_item_files ADD COLUMN {col} {sql_type}"))
            db.commit()
    db.execute(text("CREATE INDEX IF NOT EXISTS ix_cad_item_files_folder_id ON cad_item_files (folder_id)"))
    db.commit()
