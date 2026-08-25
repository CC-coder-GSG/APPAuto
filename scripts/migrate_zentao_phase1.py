"""
Migration: Zentao Phase 1 — Database Schema Extensions
=======================================================

Run this script once against the existing SQLite database to add:

1. software_products: zentao_product_id, zentao_product_name_cache
2. versions: zentao_project_id/name, zentao_execution_id/name,
             zentao_build_id/name, zentao_testtask_id/name,
             zentao_release_id/name
3. requirements: zentao_story_id, zentao_plan_id, zentao_plan_title_cache
4. test_cases: zentao_case_numeric_id
5. bug_tracking: zentao_story_id, zentao_build_id, zentao_release_id,
                 zentao_testtask_id, last_zentao_synced_at
6. build_records: auto_archive_status, auto_archive_minor_version_id,
                  auto_archive_message
7. NEW TABLE: user_zentao_bindings

Usage:
    python scripts/migrate_zentao_phase1.py

The script is idempotent — it checks whether each column/table already exists
before attempting to add it.  Re-running is safe.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

# Locate the project root (one level above this script)
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "app_auto.db"


def get_existing_columns(cursor: sqlite3.Cursor, table: str) -> set[str]:
    cursor.execute(f"PRAGMA table_info({table})")
    return {row[1] for row in cursor.fetchall()}


def get_existing_tables(cursor: sqlite3.Cursor) -> set[str]:
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
    return {row[0] for row in cursor.fetchall()}


def add_column_if_missing(
    cursor: sqlite3.Cursor,
    table: str,
    column: str,
    col_type: str,
    existing: set[str],
) -> bool:
    if column in existing:
        return False
    cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
    print(f"  + {table}.{column} ({col_type})")
    return True


def run_migration(db_path: Path) -> None:
    print(f"Connecting to: {db_path}")
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()

    tables = get_existing_tables(cur)

    # -----------------------------------------------------------------------
    # 1. software_products
    # -----------------------------------------------------------------------
    if "software_products" in tables:
        cols = get_existing_columns(cur, "software_products")
        add_column_if_missing(cur, "software_products", "zentao_product_id", "INTEGER", cols)
        add_column_if_missing(cur, "software_products", "zentao_product_name_cache", "VARCHAR(255)", cols)
    else:
        print("  [SKIP] table software_products not found — skipping")

    # -----------------------------------------------------------------------
    # 2. versions
    # -----------------------------------------------------------------------
    if "versions" in tables:
        cols = get_existing_columns(cur, "versions")
        version_new_cols = [
            ("zentao_project_id", "INTEGER"),
            ("zentao_project_name_cache", "VARCHAR(255)"),
            ("zentao_execution_id", "INTEGER"),
            ("zentao_execution_name_cache", "VARCHAR(255)"),
            ("zentao_build_id", "INTEGER"),
            ("zentao_build_name_cache", "VARCHAR(255)"),
            ("zentao_testtask_id", "INTEGER"),
            ("zentao_testtask_name_cache", "VARCHAR(255)"),
            ("zentao_release_id", "INTEGER"),
            ("zentao_release_name_cache", "VARCHAR(255)"),
        ]
        for col, col_type in version_new_cols:
            add_column_if_missing(cur, "versions", col, col_type, cols)
    else:
        print("  [SKIP] table versions not found")

    # -----------------------------------------------------------------------
    # 3. requirements
    # -----------------------------------------------------------------------
    if "requirements" in tables:
        cols = get_existing_columns(cur, "requirements")
        req_new_cols = [
            ("zentao_story_id", "INTEGER"),
            ("zentao_plan_id", "INTEGER"),
            ("zentao_plan_title_cache", "VARCHAR(255)"),
        ]
        for col, col_type in req_new_cols:
            add_column_if_missing(cur, "requirements", col, col_type, cols)
    else:
        print("  [SKIP] table requirements not found")

    # -----------------------------------------------------------------------
    # 4. test_cases
    # -----------------------------------------------------------------------
    if "test_cases" in tables:
        cols = get_existing_columns(cur, "test_cases")
        add_column_if_missing(cur, "test_cases", "zentao_case_numeric_id", "INTEGER", cols)
    else:
        print("  [SKIP] table test_cases not found")

    # -----------------------------------------------------------------------
    # 5. bug_tracking
    # -----------------------------------------------------------------------
    if "bug_tracking" in tables:
        cols = get_existing_columns(cur, "bug_tracking")
        bug_new_cols = [
            ("zentao_story_id", "INTEGER"),
            ("zentao_build_id", "INTEGER"),
            ("zentao_release_id", "INTEGER"),
            ("zentao_testtask_id", "INTEGER"),
            ("last_zentao_synced_at", "DATETIME"),
        ]
        for col, col_type in bug_new_cols:
            add_column_if_missing(cur, "bug_tracking", col, col_type, cols)
    else:
        print("  [SKIP] table bug_tracking not found")

    # -----------------------------------------------------------------------
    # 6. build_records
    # -----------------------------------------------------------------------
    if "build_records" in tables:
        cols = get_existing_columns(cur, "build_records")
        build_new_cols = [
            ("auto_archive_status", "VARCHAR(30)"),
            ("auto_archive_minor_version_id", "INTEGER"),
            ("auto_archive_message", "VARCHAR(500)"),
        ]
        for col, col_type in build_new_cols:
            add_column_if_missing(cur, "build_records", col, col_type, cols)
    else:
        print("  [SKIP] table build_records not found")

    # -----------------------------------------------------------------------
    # 7. user_zentao_bindings (new table)
    # -----------------------------------------------------------------------
    if "user_zentao_bindings" not in tables:
        cur.execute("""
            CREATE TABLE user_zentao_bindings (
                id                          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id                     INTEGER NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
                base_url                    VARCHAR(500) NOT NULL,
                zentao_account              VARCHAR(120) NOT NULL,
                zentao_password_ciphertext  TEXT,
                zentao_password_iv          VARCHAR(64),
                token_value                 TEXT,
                token_expires_at            DATETIME,
                last_refresh_at             DATETIME,
                last_refresh_status         VARCHAR(20),
                last_error_message          TEXT,
                created_at                  DATETIME NOT NULL DEFAULT (datetime('now')),
                updated_at                  DATETIME NOT NULL DEFAULT (datetime('now'))
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS ix_user_zentao_bindings_user_id ON user_zentao_bindings(user_id)")
        print("  + TABLE user_zentao_bindings (created)")
    else:
        print("  [OK] TABLE user_zentao_bindings already exists")

    conn.commit()
    conn.close()
    print("\nMigration complete.")


def backfill_zentao_story_ids(db_path: Path) -> None:
    """
    One-time backfill: populate requirements.zentao_story_id from zentao_req_id.

    For example: zentao_req_id = 'r#5604' -> zentao_story_id = 5604
    Only fills rows where zentao_story_id IS NULL and zentao_req_id matches 'r#<digits>'.
    """
    import re
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()

    cur.execute("SELECT id, zentao_req_id FROM requirements WHERE zentao_story_id IS NULL")
    rows = cur.fetchall()
    updated = 0
    pattern = re.compile(r'^r#?(\d+)$', re.IGNORECASE)
    for req_id, zentao_req_id in rows:
        m = pattern.match((zentao_req_id or '').strip())
        if m:
            story_id = int(m.group(1))
            cur.execute("UPDATE requirements SET zentao_story_id = ? WHERE id = ?", (story_id, req_id))
            updated += 1

    conn.commit()
    conn.close()
    if updated:
        print(f"Backfilled zentao_story_id for {updated} requirement(s).")
    else:
        print("No requirements needed zentao_story_id backfill.")


if __name__ == "__main__":
    if not DB_PATH.exists():
        print(f"ERROR: Database not found at {DB_PATH}", file=sys.stderr)
        sys.exit(1)

    run_migration(DB_PATH)
    backfill_zentao_story_ids(DB_PATH)
