import asyncio
import threading
from types import SimpleNamespace

import pytest

from app.api.routes import zentao_hydrate as hydrate
from app.services.zentao_client_service import ZentaoAPIError


def test_slow_story_returns_partial_success_before_worker_finishes(monkeypatch):
    release = threading.Event()
    finished = threading.Event()
    monkeypatch.setattr(hydrate, '_BATCH_TIMEOUT', 0.05)

    def story(story_id):
        if story_id == 2:
            try:
                release.wait(2)
            finally:
                finished.set()
        return {'id': story_id, 'status': 'active', 'assignedTo': 'wangjie'}

    try:
        data, auth, errors = asyncio.run(hydrate._fetch_stories_concurrent(
            SimpleNamespace(get_story=story), 'https://zentao.invalid', [1, 2]))
        assert data['1']['assigned_to'] == 'wangjie'
        assert errors == ['2'] and not auth
        assert not finished.is_set(), 'Slow worker must not delay partial response'
    finally:
        release.set()
        assert finished.wait(2)


def test_transient_failure_is_distinct_from_confirmed_missing():
    def story(story_id):
        if story_id == 2:
            raise ZentaoAPIError(503, 'unavailable')
        return {'id': story_id, 'status': 'active'} if story_id == 1 else None

    data, auth, errors = asyncio.run(hydrate._fetch_stories_concurrent(
        SimpleNamespace(get_story=story), 'https://zentao.invalid', [1, 2, 3]))
    assert set(data) == {'1'} and errors == ['2'] and not auth


def test_cancelled_http_worker_keeps_capacity_until_it_really_finishes(monkeypatch):
    slots = threading.BoundedSemaphore(1)
    monkeypatch.setattr(hydrate, '_HYDRATION_SLOTS', slots)
    started, release = threading.Event(), threading.Event()

    def slow():
        started.set()
        release.wait(2)

    async def scenario():
        task = asyncio.create_task(hydrate._upstream_call(slow))
        await asyncio.to_thread(started.wait, 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        with pytest.raises(ZentaoAPIError) as exc:
            await hydrate._upstream_call(lambda: None)
        assert exc.value.status_code == 503

    try:
        asyncio.run(scenario())
    finally:
        release.set()
    assert slots.acquire(timeout=2)
    slots.release()


def test_bug_label_does_not_fetch_action_history():
    def history(*args):
        pytest.fail('Labels must not request action history')

    client = SimpleNamespace(get_bug=lambda i: {'id': i, 'status': 'active'},
                             get_bug_with_fallback=history)
    data, auth, errors = asyncio.run(hydrate._fetch_bugs_concurrent(client, 'https://zentao.invalid', [1]))
    assert data['1']['status'] == 'active' and not errors and not auth


def test_slow_optional_build_keeps_successful_bug_label(monkeypatch):
    release = threading.Event()
    monkeypatch.setattr(hydrate, '_BATCH_TIMEOUT', 0.05)

    def build(i):
        release.wait(2)
        return {'id': i, 'name': 'build'}

    client = SimpleNamespace(get_bug=lambda i: {'id': i, 'status': 'closed', 'resolvedBuild': 12}, get_build=build)
    try:
        data, auth, errors = asyncio.run(hydrate._fetch_bugs_concurrent(client, 'https://zentao.invalid', [1]))
        assert data['1']['status'] == 'closed' and data['1']['resolved_build'] == '12'
        assert not errors and not auth
    finally:
        release.set()
