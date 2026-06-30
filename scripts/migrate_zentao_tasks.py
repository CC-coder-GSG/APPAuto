"""禅道任务联动 DB 迁移（2026-06-29 需求）

新增列：
  users.zentao_account                       VARCHAR(120)
  requirements.estimated_test_hours          FLOAT NOT NULL DEFAULT 4.0
  requirements.zentao_task_id                INTEGER
  requirements.zentao_parent_task_id         INTEGER
  requirements.task_started_at               DATETIME
  requirements.task_finished_at              DATETIME
  requirements.zentao_task_status_cache      VARCHAR(20)

新增表：zentao_task_mirror、holidays（由 SQLAlchemy create_all 建，此处兜底确保）。

幂等，可重复运行：
    python scripts/migrate_zentao_tasks.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import sqlite3
from app.core.config import settings

db_path = settings.database_url.replace("sqlite:///", "").replace("sqlite://", "")
print(f"[migrate_zentao_tasks] Connecting to: {db_path}")

conn = sqlite3.connect(db_path)
cur = conn.cursor()

COLUMNS = {
    "users": [
        ("zentao_account", "VARCHAR(120)"),
    ],
    "requirements": [
        ("estimated_test_hours", "FLOAT NOT NULL DEFAULT 4.0"),
        ("zentao_task_id", "INTEGER"),
        ("zentao_parent_task_id", "INTEGER"),
        ("task_started_at", "DATETIME"),
        ("task_finished_at", "DATETIME"),
        ("zentao_task_status_cache", "VARCHAR(20)"),
    ],
}


def add_column_if_missing(table: str, col: str, col_type: str) -> None:
    cur.execute(f"PRAGMA table_info({table})")
    existing = {row[1] for row in cur.fetchall()}
    if col not in existing:
        cur.execute(f"ALTER TABLE {table} ADD COLUMN {col} {col_type}")
        print(f"  [+] {table}.{col} {col_type}")
    else:
        print(f"  [=] {table}.{col} already exists, skipped")


for table, cols in COLUMNS.items():
    print(f"\n[{table}]")
    for col, col_type in cols:
        add_column_if_missing(table, col, col_type)

conn.commit()
conn.close()
print("\n[migrate_zentao_tasks] Columns done. 新表 zentao_task_mirror/holidays 由 init_db/create_all 创建。")
print("提示：迁移后请运行  python -m app.init_db  以创建新表并跑 schema-compat。")
