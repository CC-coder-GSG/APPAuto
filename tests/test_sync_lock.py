"""sync_lock 回收逻辑回归（2026-07-07 服务器增量同步断流根因）。

SQLite 原生 SQL 读出的 acquired_at 是字符串，与 datetime cutoff 比较抛
TypeError 被吞 → 残留锁永久死锁。服务器上 bug_recent:1 自 6/15、
testcase_full:1 自 5/13 起一直无法回收，增量同步全部 409。
"""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import text

from app.services.sync_lock_service import acquire_sync_lock, release_sync_lock
from app.utils.time_utils import local_now


def _insert_lock_row(db, key: str, acquired_at, ttl: int = 600):
    # 模拟服务器崩溃残留：直接写库（SQLite 落成字符串）
    db.execute(
        text("INSERT INTO sync_locks (key, holder, acquired_at, ttl_seconds) VALUES (:k, 'dead#1', :t, :ttl)"),
        {"k": key, "t": acquired_at, "ttl": ttl},
    )
    db.commit()


def test_stale_string_lock_is_reclaimed(db_session):
    # 三周前残留的锁（远超 600s TTL）→ 必须能被重新获取
    _insert_lock_row(db_session, "bug_recent:99", local_now() - timedelta(days=21))
    assert acquire_sync_lock(db_session, "bug_recent:99", ttl_seconds=600) is True
    release_sync_lock(db_session, "bug_recent:99")


def test_fresh_lock_still_blocks(db_session):
    _insert_lock_row(db_session, "bug_recent:98", local_now() - timedelta(seconds=30))
    assert acquire_sync_lock(db_session, "bug_recent:98", ttl_seconds=600) is False


def test_acquire_release_roundtrip(db_session):
    assert acquire_sync_lock(db_session, "bug_recent:97", ttl_seconds=600) is True
    # 自己持有中：他人（同进程模拟）在 TTL 内拿不到
    assert acquire_sync_lock(db_session, "bug_recent:97", ttl_seconds=600) is False
    release_sync_lock(db_session, "bug_recent:97")
    assert acquire_sync_lock(db_session, "bug_recent:97", ttl_seconds=600) is True
    release_sync_lock(db_session, "bug_recent:97")


def test_garbage_acquired_at_treated_as_expired(db_session):
    _insert_lock_row(db_session, "bug_recent:96", "not-a-date")
    assert acquire_sync_lock(db_session, "bug_recent:96", ttl_seconds=600) is True
    release_sync_lock(db_session, "bug_recent:96")
