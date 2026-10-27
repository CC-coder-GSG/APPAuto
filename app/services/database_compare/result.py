from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path

from .values import dumps


class ResultStore:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            raise ValueError("结果文件已存在，请使用新的输出位置")
        self.db = sqlite3.connect(path)
        self.db.execute("PRAGMA temp_store=FILE")
        self.db.executescript("""
            CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE differences (
                id INTEGER PRIMARY KEY, database_name TEXT NOT NULL,
                table_name TEXT NOT NULL, scope TEXT NOT NULL, kind TEXT NOT NULL,
                details_json TEXT NOT NULL
            );
            CREATE INDEX diff_filter ON differences(database_name,table_name,scope,kind,id);
            CREATE TABLE table_results (
                database_name TEXT NOT NULL, table_name TEXT NOT NULL, data_json TEXT NOT NULL,
                PRIMARY KEY(database_name,table_name)
            );
            CREATE TEMP TABLE staged (
                side INTEGER NOT NULL, key TEXT NOT NULL, data TEXT NOT NULL, n INTEGER NOT NULL,
                PRIMARY KEY(side,key)
            ) WITHOUT ROWID;
        """)
        self.db.execute("PRAGMA cache_size=-8192")

    def diff(self, database: str, table: str, scope: str, kind: str, **details):
        self.db.execute("INSERT INTO differences(database_name,table_name,scope,kind,details_json) VALUES(?,?,?,?,?)",
                        (database, table, scope, kind, dumps(details)))

    def table(self, database: str, table: str, data: dict):
        self.db.execute("INSERT OR REPLACE INTO table_results VALUES(?,?,?)", (database, table, dumps(data)))

    def meta(self, key: str, data):
        self.db.execute("INSERT OR REPLACE INTO metadata VALUES(?,?)", (key, dumps(data)))

    def close(self):
        self.db.commit()
        self.db.close()


def _preview(value, truncated):
    if isinstance(value, str) and len(value) > 2000:
        truncated[0] = True
        return value[:2000] + f"…[节选，原长度 {len(value)}；完整内容见 JSON 导出]"
    if isinstance(value, dict):
        return {k: _preview(v, truncated) for k, v in value.items()}
    if isinstance(value, list):
        return [_preview(v, truncated) for v in value]
    return value


def read_result(path: Path, *, database: str = "", table: str = "", scope: str = "", kind: str = "",
                page: int = 1, page_size: int = 50, preview: bool = False) -> dict:
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        clauses, params = [], []
        for field, value in (("database_name", database), ("table_name", table), ("scope", scope), ("kind", kind)):
            if value:
                clauses.append(field + "=?")
                params.append(value)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        count = connection.execute("SELECT COUNT(*) FROM differences" + where, params).fetchone()[0]
        rows = connection.execute("SELECT id,database_name,table_name,scope,kind,details_json FROM differences" + where +
                                  " ORDER BY id LIMIT ? OFFSET ?", [*params, page_size, (page - 1) * page_size])
        items = []
        for r in rows:
            truncated = [False]
            details = json.loads(r[5])
            if preview:
                details = _preview(details, truncated)
            items.append({"id": r[0], "database": r[1], "table": r[2], "scope": r[3], "kind": r[4],
                          "details": details, "preview_only": truncated[0]})
        return {"total": count, "page": page, "page_size": page_size, "items": items}
    finally:
        connection.close()


def read_tables(path: Path) -> list[dict]:
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as connection:
        return [{"database": d, "table": t, **json.loads(data)} for d, t, data in connection.execute(
            "SELECT database_name,table_name,data_json FROM table_results ORDER BY database_name,table_name")]
