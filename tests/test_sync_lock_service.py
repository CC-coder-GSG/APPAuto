from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.models.sync_lock import SyncLock
from app.services.sync_lock_service import acquire_sync_lock, release_sync_lock
from app.utils.time_utils import local_now


def test_release_sync_lock_recovers_pending_rollback_session(db_session):
    assert acquire_sync_lock(db_session, "bug_recent:77", ttl_seconds=600)

    db_session.add(
        SyncLock(
            key="bug_recent:77",
            holder="duplicate",
            acquired_at=local_now(),
            ttl_seconds=600,
        )
    )
    with pytest.raises(IntegrityError):
        db_session.commit()

    assert db_session.is_active is False
    release_sync_lock(db_session, "bug_recent:77")

    remaining = db_session.execute(
        text("SELECT COUNT(*) FROM sync_locks WHERE key = 'bug_recent:77'")
    ).scalar_one()
    assert remaining == 0
