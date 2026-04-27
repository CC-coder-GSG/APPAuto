from __future__ import annotations

import logging
import os
import socket
from contextlib import contextmanager
from datetime import timedelta

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.utils.time_utils import local_now

logger = logging.getLogger(__name__)


def _holder_id() -> str:
    return f"{socket.gethostname()}#{os.getpid()}"


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

        prev = existing[0]
        if prev and prev > cutoff:
            return False

        result = db.execute(
            text(
                "UPDATE sync_locks SET holder = :holder, acquired_at = :now, ttl_seconds = :ttl "
                "WHERE key = :key AND (acquired_at IS NULL OR acquired_at <= :cutoff)"
            ),
            {
                "key": key,
                "holder": holder,
                "now": now,
                "ttl": ttl_seconds,
                "cutoff": cutoff,
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
