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
    """Keep historical ``versions`` tables compatible with the current model."""
    rows = db.execute(text("PRAGMA table_info(versions)")).fetchall()
    cols = {r[1] for r in rows}
    if "final_test_enabled" not in cols:
        db.execute(text("ALTER TABLE versions ADD COLUMN final_test_enabled BOOLEAN NOT NULL DEFAULT 0"))
        db.commit()
    if "final_test_started_at" not in cols:
        db.execute(text("ALTER TABLE versions ADD COLUMN final_test_started_at DATETIME"))
        db.commit()
    _ensure_version_unique_per_software(db)


def _ensure_version_unique_per_software(db: Session) -> None:
    """
    Replace the historical global ``(version_no, version_type)`` constraint.

    SQLite cannot drop a table-level UNIQUE constraint in place, so preserve
    the complete current table definition and data while rebuilding the table
    with ``software_id`` included in the constraint.  Foreign keys are
    disabled only on this dedicated raw connection for the duration of the
    atomic rebuild.
    """
    table_sql_row = db.execute(
        text("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'versions'")
    ).fetchone()
    if not table_sql_row or not table_sql_row[0]:
        return
    table_sql = str(table_sql_row[0])
    if "uq_version_software_no_type" in table_sql:
        return
    legacy_constraint = "CONSTRAINT uq_version_no_type UNIQUE (version_no, version_type)"
    if legacy_constraint not in table_sql:
        logger.warning("versions table has an unknown UNIQUE layout; skipping automatic rebuild")
        return

    index_rows = db.execute(
        text(
            "SELECT sql FROM sqlite_master "
            "WHERE type = 'index' AND tbl_name = 'versions' AND sql IS NOT NULL"
        )
    ).fetchall()
    index_sql = [str(row[0]) for row in index_rows if row[0]]
    temp_table = "versions__software_unique"
    create_sql = table_sql.replace(
        "CREATE TABLE versions",
        f"CREATE TABLE {temp_table}",
        1,
    ).replace(
        legacy_constraint,
        "CONSTRAINT uq_version_software_no_type "
        "UNIQUE (software_id, version_no, version_type)",
        1,
    )

    db.commit()
    raw = db.get_bind().raw_connection()
    try:
        cursor = raw.cursor()
        cursor.execute("PRAGMA foreign_keys = OFF")
        cursor.execute(f"DROP TABLE IF EXISTS {temp_table}")
        cursor.execute(create_sql)
        cursor.execute(f"INSERT INTO {temp_table} SELECT * FROM versions")
        cursor.execute("DROP TABLE versions")
        cursor.execute(f"ALTER TABLE {temp_table} RENAME TO versions")
        for statement in index_sql:
            cursor.execute(statement)
        raw.commit()
        cursor.execute("PRAGMA foreign_keys = ON")
    except Exception:
        raw.rollback()
        raise
    finally:
        try:
            raw.cursor().execute("PRAGMA foreign_keys = ON")
        except Exception:
            logger.warning("Failed to restore SQLite foreign_keys after versions migration")
        raw.close()
    db.expire_all()
    logger.info("Migrated versions UNIQUE constraint to software_id + version_no + version_type")


def repair_software_product_mappings_from_bug_history(db: Session) -> int:
    """
    Repair project IDs accidentally stored as software-level product IDs.

    Older ``/softwares/from-zentao`` code saved ``zentao_project_id`` into the
    ``zentao_product_id`` field.  When a software's historical bugs identify
    exactly one real Zentao product, that product is an unambiguous and safer
    source of truth.
    """
    if not db.execute(text("PRAGMA table_info(bug_tracking)")).fetchall():
        return 0
    rows = db.execute(
        text(
            "SELECT v.software_id, b.zentao_product_id, "
            "       MAX(NULLIF(TRIM(b.zentao_product_name), '')) AS product_name "
            "FROM bug_tracking b "
            "JOIN versions v ON v.id = b.major_version_id "
            "WHERE v.software_id IS NOT NULL "
            "  AND b.zentao_product_id IS NOT NULL "
            "  AND TRIM(CAST(b.zentao_product_id AS TEXT)) <> '' "
            "GROUP BY v.software_id, b.zentao_product_id"
        )
    ).fetchall()

    by_software: dict[int, list[tuple[int, str | None]]] = {}
    for software_id, raw_product_id, product_name in rows:
        try:
            product_id = int(raw_product_id)
        except (TypeError, ValueError):
            continue
        by_software.setdefault(int(software_id), []).append((product_id, product_name))

    repaired = 0
    for software_id, candidates in by_software.items():
        distinct = {product_id for product_id, _ in candidates}
        if len(distinct) != 1:
            continue
        product_id = next(iter(distinct))
        product_name = next(
            (name for candidate_id, name in candidates if candidate_id == product_id and name),
            None,
        )
        result = db.execute(
            text(
                "UPDATE software_products "
                "SET zentao_product_id = :product_id, "
                "    zentao_product_name_cache = COALESCE(:product_name, zentao_product_name_cache) "
                "WHERE id = :software_id "
                "  AND (zentao_product_id IS NULL OR zentao_product_id <> :product_id)"
            ),
            {
                "software_id": software_id,
                "product_id": product_id,
                "product_name": product_name,
            },
        )
        repaired += int(result.rowcount or 0)
    if repaired:
        db.commit()
        logger.info("Repaired %s software Zentao product mapping(s) from bug history", repaired)
    return repaired


def ensure_requirement_foreign_key_compat(db: Session) -> int:
    """
    Repair foreign keys left pointing at the removed ``requirements_old``.

    A historical SQLite migration renamed ``requirements`` before recreating
    it.  Modern SQLite correctly propagated that rename into child-table
    definitions, but the migration then dropped ``requirements_old``.  The
    resulting child tables could still be read, while every new insert with
    foreign-key enforcement enabled failed with "no such table:
    main.requirements_old".
    """
    affected = db.execute(
        text(
            "SELECT name, sql FROM sqlite_master "
            "WHERE type = 'table' AND sql LIKE '%requirements_old%'"
        )
    ).fetchall()
    if not affected:
        return 0

    rebuilds: list[tuple[str, str, list[str]]] = []
    for table_name, table_sql in affected:
        name = str(table_name)
        sql = str(table_sql)
        temp_name = f"{name}__requirements_fk"
        create_sql = sql.replace(
            f"CREATE TABLE {name}",
            f"CREATE TABLE {temp_name}",
            1,
        )
        if create_sql == sql:
            create_sql = sql.replace(
                f'CREATE TABLE "{name}"',
                f'CREATE TABLE "{temp_name}"',
                1,
            )
        create_sql = create_sql.replace(
            "REFERENCES requirements_old",
            "REFERENCES requirements",
        ).replace(
            'REFERENCES "requirements_old"',
            'REFERENCES "requirements"',
        )
        object_rows = db.execute(
            text(
                "SELECT sql FROM sqlite_master "
                "WHERE tbl_name = :table_name "
                "  AND type IN ('index', 'trigger') AND sql IS NOT NULL"
            ),
            {"table_name": name},
        ).fetchall()
        rebuilds.append(
            (name, create_sql, [str(row[0]) for row in object_rows if row[0]])
        )

    db.commit()
    raw = db.get_bind().raw_connection()
    try:
        cursor = raw.cursor()
        cursor.execute("PRAGMA foreign_keys = OFF")
        for table_name, create_sql, object_sql in rebuilds:
            temp_name = f"{table_name}__requirements_fk"
            cursor.execute(f'DROP TABLE IF EXISTS "{temp_name}"')
            cursor.execute(create_sql)
            cursor.execute(
                f'INSERT INTO "{temp_name}" SELECT * FROM "{table_name}"'
            )
            cursor.execute(f'DROP TABLE "{table_name}"')
            cursor.execute(
                f'ALTER TABLE "{temp_name}" RENAME TO "{table_name}"'
            )
            for statement in object_sql:
                cursor.execute(statement)
        raw.commit()
    except Exception:
        raw.rollback()
        raise
    finally:
        try:
            raw.cursor().execute("PRAGMA foreign_keys = ON")
        except Exception:
            logger.warning("Failed to restore SQLite foreign_keys after FK repair")
        raw.close()
    db.expire_all()
    logger.info(
        "Repaired requirements_old foreign keys in %s table(s)",
        len(rebuilds),
    )
    return len(rebuilds)


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
    if "test_notes_html" not in req_cols:
        db.execute(text("ALTER TABLE requirements ADD COLUMN test_notes_html TEXT"))
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
    if "task_consumed_accum" not in req_cols:
        db.execute(text("ALTER TABLE requirements ADD COLUMN task_consumed_accum FLOAT NOT NULL DEFAULT 0"))
        db.commit()
    if "task_efforts_submitted" not in req_cols:
        db.execute(text("ALTER TABLE requirements ADD COLUMN task_efforts_submitted FLOAT NOT NULL DEFAULT 0"))
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
        # 复测问题留痕（2026-07-16 复测结论改版）
        "retest_activated": "BOOLEAN NOT NULL DEFAULT 0",
        "retest_activated_by_id": "INTEGER",
        "retest_activated_at": "DATETIME",
        "retest_activated_req_id": "INTEGER",
        "retest_dismissed": "BOOLEAN NOT NULL DEFAULT 0",
        "retest_dismissed_by_id": "INTEGER",
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
    # 平台侧工时结算字段（暂停期不计工时，2026-07-09）
    if "local_started_at" not in cols:
        db.execute(text("ALTER TABLE zentao_task_mirror ADD COLUMN local_started_at DATETIME"))
        db.commit()
    if "consumed_accum" not in cols:
        db.execute(text("ALTER TABLE zentao_task_mirror ADD COLUMN consumed_accum FLOAT NOT NULL DEFAULT 0"))
        db.commit()
    # 分段提交禅道工时记录的累计（工时按天分布，2026-07-17）
    if "efforts_submitted" not in cols:
        db.execute(text("ALTER TABLE zentao_task_mirror ADD COLUMN efforts_submitted FLOAT NOT NULL DEFAULT 0"))
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
