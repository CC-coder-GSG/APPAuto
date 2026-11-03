"""Explicit boundaries between local reads and slow external work."""
import logging
from functools import wraps
from threading import BoundedSemaphore, local

from fastapi import HTTPException

logger = logging.getLogger(__name__)
_sync_slots = BoundedSemaphore(1)
_sync_context = local()


def release_read_transaction(db):
    """Return a clean read connection without expiring already loaded values.

    Never commit a caller's pending changes as a side effect of a remote read.
    Callers with writes must explicitly finish their transaction first.
    """
    if db.new or db.dirty or db.deleted:
        return False
    if db.in_transaction():
        expire = db.expire_on_commit
        try:
            db.expire_on_commit = False
            db.commit()
        finally:
            db.expire_on_commit = expire
    return True


def bounded_sync(func):
    """One bulk sync per process; nested sync calls share their parent's slot.

    Reject excess work immediately instead of waiting with a DB connection.
    Existing database resource locks still protect against other processes.
    """
    @wraps(func)
    def wrapped(self, *args, **kwargs):
        if getattr(_sync_context, 'active', False):
            return func(self, *args, **kwargs)
        if not _sync_slots.acquire(blocking=False):
            release_read_transaction(self.db)
            raise HTTPException(409, '后台同步正在执行，请稍后刷新；当前数据仍可使用')
        _sync_context.active = True
        try:
            return func(self, *args, **kwargs)
        except Exception:
            self.db.rollback()
            raise
        finally:
            _sync_context.active = False
            _sync_slots.release()
    return wrapped
