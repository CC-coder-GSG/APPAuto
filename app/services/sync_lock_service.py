from __future__ import annotations

import logging
import os
import socket
from contextlib import contextmanager
from datetime import datetime, timedelta

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.utils.time_utils import local_now

logger = logging.getLogger(__name__)


def _holder_id() -> str:
    return f"{socket.gethostname()}#{os.getpid()}"


def _coerce_dt(value) -> datetime | None:
    """SQLite 原生 SQL 读出的 acquired_at 是字符串——必须转回 datetime 再比较。

    2026-07-07 修复：此前字符串与 datetime 直接比较抛 TypeError，被外层
    except 吞掉后 return False（当作锁被占用）→ 一旦某次同步崩溃残留锁行，
    该锁 key 永久死锁，TTL 回收永远执行不到（服务器上 Bug/用例增量同步
    因此分别断了数周）。
    """
    if value is None or isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        logger.warning("sync_lock acquired_at 无法解析：%r（按已过期处理）", value)
        return None


def acquire_sync_lock(db: Session, key: str, *, ttl_seconds: int = 600) -> bool:
    """
    Try to claim a named sync lock. Returns True iff the caller now owns it.

    Locks older than their declared TTL are considered abandoned and reclaimed
    automatically — this protects against a worker that died mid-sync without
    releasing its lock.
    """
    now = local_now()
    cutoff = now - timedelta(seconds=ttl_seconds)
    holder = _holder_id()
    try:
        existing = db.execute(
            text("SELECT acquired_at FROM sync_locks WHERE key = :key"),
            {"key": key},
        ).fetchone()
        if existing is None:
            try:
                db.execute(
                    text(
                        "INSERT INTO sync_locks (key, holder, acquired_at, ttl_seconds) "
                        "VALUES (:key, :holder, :now, :ttl)"
                    ),
                    {"key": key, "holder": holder, "now": now, "ttl": ttl_seconds},
                )
                db.commit()
                return True
            except IntegrityError:
                db.rollback()
                return False

        prev_raw = existing[0]
        prev = _coerce_dt(prev_raw)
        if prev and prev > cutoff:
            return False

        # 回收条件：过期（<= cutoff）或值未变（CAS，覆盖 NULL/无法解析的脏值）
        result = db.execute(
            text(
                "UPDATE sync_locks SET holder = :holder, acquired_at = :now, ttl_seconds = :ttl "
                "WHERE key = :key AND (acquired_at IS NULL OR acquired_at <= :cutoff OR acquired_at = :prev_raw)"
            ),
            {
                "key": key,
                "holder": holder,
                "now": now,
                "ttl": ttl_seconds,
                "cutoff": cutoff,
                "prev_raw": prev_raw,
            },
        )
        db.commit()
        return result.rowcount > 0
    except Exception:
        db.rollback()
        logger.warning("acquire_sync_lock failed for key=%s", key, exc_info=True)
        return False


def release_sync_lock(db: Session, key: str) -> None:
    try:
        # A sync failure (for example an IntegrityError during commit) leaves
        # SQLAlchemy in "pending rollback" state.  DELETE cannot run in that
        # state, which used to leave a lock row behind and turn the original
        # error into repeated HTTP 409 "sync in progress" responses.
        if not db.is_active:
            db.rollback()
        db.execute(text("DELETE FROM sync_locks WHERE key = :key"), {"key": key})
        db.commit()
    except Exception:
        db.rollback()
        logger.warning("release_sync_lock failed for key=%s", key, exc_info=True)


@contextmanager
def sync_lock(db: Session, key: str, *, ttl_seconds: int = 600):
    """
    Context-manager flavor: yields True if the lock was acquired, False
    otherwise. Always releases on the way out so a long-running job that
    finished can be re-run immediately.
    """
    acquired = acquire_sync_lock(db, key, ttl_seconds=ttl_seconds)
    try:
        yield acquired
    finally:
        if acquired:
            release_sync_lock(db, key)
