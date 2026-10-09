"""Back up an existing SQLite database before the additive Bugly upgrade."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import text


def backup_before_bugly_upgrade(engine):
    if engine.dialect.name != "sqlite" or not engine.url.database or engine.url.database == ":memory:":
        return None
    source = Path(engine.url.database).resolve()
    if not source.is_file():
        return None
    with engine.connect() as connection:
        columns = {row[1] for row in connection.execute(text("PRAGMA table_info(users)"))}
        if not columns or {"account_type", "is_active", "expires_at"}.issubset(columns):
            return None
    directory = source.parent / "bugly-backups"
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{source.stem}-before-bugly-{datetime.now(timezone.utc):%Y%m%dT%H%M%S%fZ}.db"
    # SQLite backup API includes committed WAL contents and avoids an inconsistent file copy.
    with sqlite3.connect(str(source)) as original, sqlite3.connect(str(target)) as backup:
        original.backup(backup)
    return target
