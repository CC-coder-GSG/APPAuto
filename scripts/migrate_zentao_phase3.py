"""
Phase 3 DB migration — Zentao close cache + stage5 record source/comment

Adds to bug_tracking:
  - last_zentao_checked_at DATETIME
  - zentao_closed_by_account VARCHAR(100)
  - zentao_closed_by_name    VARCHAR(100)
  - zentao_close_date        DATETIME
  - zentao_close_comment     TEXT
  - zentao_assigned_to_account VARCHAR(100)
  - zentao_assigned_to_name    VARCHAR(100)
  - zentao_remote_updated_at   DATETIME

Adds to bug_stage5_records:
  - source  VARCHAR(20) NOT NULL DEFAULT 'manual'
  - comment TEXT

Run once:
    python scripts/migrate_zentao_phase3.py
"""
import sys
from pathlib import Path

# Ensure project root is on sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

import sqlite3
from app.core.config import settings

db_path = settings.database_url.replace("sqlite:///", "").replace("sqlite://", "")
print(f"[migrate_zentao_phase3] Connecting to: {db_path}")

conn = sqlite3.connect(db_path)
cur = conn.cursor()

BUG_TRACKING_COLUMNS = [
    ("last_zentao_checked_at",     "DATETIME"),
    ("zentao_closed_by_account",   "VARCHAR(100)"),
    ("zentao_closed_by_name",      "VARCHAR(100)"),
    ("zentao_close_date",          "DATETIME"),
    ("zentao_close_comment",       "TEXT"),
    ("zentao_assigned_to_account", "VARCHAR(100)"),
    ("zentao_assigned_to_name",    "VARCHAR(100)"),
    ("zentao_remote_updated_at",   "DATETIME"),
]

STAGE5_COLUMNS = [
    ("source",  "VARCHAR(20) NOT NULL DEFAULT 'manual'"),
    ("comment", "TEXT"),
]


def add_column_if_missing(table: str, col: str, col_type: str) -> None:
    cur.execute(f"PRAGMA table_info({table})")
    existing = {row[1] for row in cur.fetchall()}
    if col not in existing:
        cur.execute(f"ALTER TABLE {table} ADD COLUMN {col} {col_type}")
        print(f"  [+] {table}.{col} {col_type}")
    else:
        print(f"  [=] {table}.{col} already exists, skipped")


print("\n[bug_tracking]")
for col, col_type in BUG_TRACKING_COLUMNS:
    add_column_if_missing("bug_tracking", col, col_type)

print("\n[bug_stage5_records]")
for col, col_type in STAGE5_COLUMNS:
    add_column_if_missing("bug_stage5_records", col, col_type)

conn.commit()
conn.close()
print("\n[migrate_zentao_phase3] Done.")
