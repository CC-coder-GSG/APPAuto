"""
Migration: Zentao Phase 2 — Bug actions + live status cache
============================================================

Adds:
  bug_tracking.zentao_live_status  VARCHAR(40)  — cached live status from Zentao
  bug_tracking.zentao_deleted      INTEGER      — soft-delete flag (0/1)

Run once:
    python scripts/migrate_zentao_phase2.py

The script is idempotent — safe to re-run.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "app_auto.db"


def get_existing_columns(cursor: sqlite3.Cursor, table: str) -> set[str]:
    cursor.execute(f"PRAGMA table_info({table})")
    return {row[1] for row in cursor.fetchall()}


def add_column_if_missing(cursor: sqlite3.Cursor, table: str, column: str, col_type: str, existing: set[str]) -> bool:
    if column in existing:
        return False
    cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
    print(f"  + {table}.{column} ({col_type})")
    return True


def run_migration(db_path: Path) -> None:
    print(f"Connecting to: {db_path}")
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()

    cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = {row[0] for row in cur.fetchall()}

    if "bug_tracking" in tables:
        cols = get_existing_columns(cur, "bug_tracking")
        add_column_if_missing(cur, "bug_tracking", "zentao_live_status", "VARCHAR(40)", cols)
        add_column_if_missing(cur, "bug_tracking", "zentao_deleted", "INTEGER NOT NULL DEFAULT 0", cols)
    else:
        print("  [SKIP] table bug_tracking not found")

    conn.commit()
    conn.close()
    print("\nPhase 2 migration complete.")


if __name__ == "__main__":
    if not DB_PATH.exists():
        print(f"ERROR: Database not found at {DB_PATH}", file=sys.stderr)
        sys.exit(1)
    run_migration(DB_PATH)
