from __future__ import annotations

import logging

from sqlalchemy.orm import Session
from sqlalchemy import text

from app.core.config import settings
from app.core.security import hash_password
from app.models import SoftwareProduct, User, UserRole, Version, VersionType

DEFAULT_ADMIN_USERNAME = "admin"
DEFAULT_ADMIN_PASSWORD = "admin"
DEFAULT_SOFTWARE_NAME = "Survey Master"
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


def ensure_software_schema_compat(db: Session) -> None:
    # 兼容历史 SQLite：为 versions 增加 software_id 列，避免要求手工迁移
    rows = db.execute(text("PRAGMA table_info(versions)")).fetchall()
    cols = {r[1] for r in rows}
    if "software_id" not in cols:
        db.execute(text("ALTER TABLE versions ADD COLUMN software_id INTEGER"))
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
    # 历史用户默认显示名回填为账号名
    db.execute(text("UPDATE users SET display_name = username WHERE display_name IS NULL OR TRIM(display_name) = ''"))
    db.commit()


def ensure_default_software_and_backfill(db: Session) -> None:
    software = db.query(SoftwareProduct).filter(SoftwareProduct.name == DEFAULT_SOFTWARE_NAME).first()
    if not software:
        software = SoftwareProduct(name=DEFAULT_SOFTWARE_NAME)
        db.add(software)
        db.commit()
        db.refresh(software)

    # 历史大版本默认归档到 Survey Master
    db.execute(
        text(
            "UPDATE versions SET software_id = :sid "
            "WHERE version_type = :major AND (software_id IS NULL OR software_id = 0)"
        ),
        {"sid": software.id, "major": VersionType.MAJOR.value},
    )
    # 子版本继承父大版本的软件归属
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
    兼容历史 SQLite：
    - 旧结构为 zentao_req_id 全局唯一
    - 新结构调整为 (major_version_id, zentao_req_id) 组合唯一
    该迁移为轻量自动迁移，启动时自动执行，无需手工改库。
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

    idx_rows = db.execute(text("PRAGMA index_list(requirements)")).fetchall()
    need_rebuild = False
    has_target_unique = False
    has_global_unique = False

    for r in idx_rows:
        # PRAGMA index_list: seq, name, unique, origin, partial
        idx_name = r[1]
        is_unique = bool(r[2])
        cols_rows = db.execute(text(f"PRAGMA index_info('{idx_name}')")).fetchall()
        cols = [c[2] for c in cols_rows]
        if is_unique and cols == ["major_version_id", "zentao_req_id"]:
            has_target_unique = True
        if is_unique and cols == ["zentao_req_id"]:
            has_global_unique = True

    if has_target_unique:
        return
    if has_global_unique:
        need_rebuild = True

    if not need_rebuild:
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
                    retested_by_id, retested_at, retest_minor_version_id, retest_passed, test_notes, test_notes_updated_at, test_notes_updated_by_id,
                    status, created_at, updated_at
                )
                SELECT
                    id, zentao_req_id, title, major_version_id, owner_id,
                    case_completed, test_completed, retest_completed,
                    retested_by_id, retested_at, retest_minor_version_id, retest_passed, test_notes, test_notes_updated_at, test_notes_updated_by_id,
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
    }
    for col, sql_type in column_defs.items():
        if col not in cols:
            db.execute(text(f"ALTER TABLE bug_tracking ADD COLUMN {col} {sql_type}"))
            db.commit()
    try:
        db.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_bug_tracking_zentao_bug_id ON bug_tracking (zentao_bug_id)"))
        db.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_bug_tracking_zentao_client_record_id ON bug_tracking (zentao_client_record_id)"))
        db.commit()
    except Exception:
        db.rollback()
        logger.warning("bug_tracking 禅道唯一索引创建失败，已跳过。请检查历史重复数据。", exc_info=True)

    # 回填：source_type=CASE 但 source_ref 为空的历史记录，通过关联的同步事件补全 source_ref
    result = db.execute(text("""
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
    """))
    if result.rowcount:
        db.commit()
        logger.info("bug_tracking source_ref 回填完成，修复记录数：%d", result.rowcount)


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
        logger.warning("test_cases 禅道唯一索引创建失败，已跳过。请检查历史重复数据。", exc_info=True)
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
