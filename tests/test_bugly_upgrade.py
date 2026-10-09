import sqlite3

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.db.bugly_upgrade import backup_before_bugly_upgrade
from app.db.seed import ensure_user_schema_compat


def test_additive_upgrade_backs_up_existing_users_and_runs_twice(tmp_path):
    path = tmp_path / "old-platform.db"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, username VARCHAR(50), password_hash VARCHAR(255), role VARCHAR(20), is_team_member BOOLEAN, session_token VARCHAR(36), created_at DATETIME)")
        db.execute("INSERT INTO users (id,username,role,is_team_member) VALUES (7,'existing-user','user',1)")
    engine = create_engine("sqlite:///" + path.as_posix())
    backup = backup_before_bugly_upgrade(engine)
    assert backup is not None and backup.is_file()
    with sqlite3.connect(backup) as db:
        assert db.execute("SELECT username FROM users WHERE id=7").fetchone()[0] == "existing-user"
        assert "account_type" not in {r[1] for r in db.execute("PRAGMA table_info(users)")}
    with Session(engine) as db:
        ensure_user_schema_compat(db)
        ensure_user_schema_compat(db)
        row = db.execute(text("SELECT account_type,is_active,expires_at FROM users WHERE id=7")).one()
        assert row == ("internal", 1, None)
    assert backup_before_bugly_upgrade(engine) is None
    engine.dispose()
