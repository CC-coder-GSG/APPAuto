from __future__ import annotations

import asyncio
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api.routes import sse
from app.db.base import Base
from app.models import User
from app.services.sse_service import SSEEnvelope, SSEEventBus


@pytest.fixture
def limited_pool(tmp_path):
    engine = create_engine('sqlite:///' + (tmp_path / 'sse.db').as_posix(),
        connect_args={'check_same_thread': False}, pool_size=1, max_overflow=0, pool_timeout=0.05)
    Base.metadata.create_all(engine, tables=[User.__table__])
    factory = sessionmaker(bind=engine)
    with factory() as db:
        user = User(username='sse-resource-test', password_hash='unused', session_token='original-session')
        db.add(user)
        db.commit()
        user_id = user.id
    yield engine, factory, user_id
    engine.dispose()


class ConnectedRequest:
    async def is_disconnected(self):
        return False


def _configure_events(monkeypatch):
    monkeypatch.setattr(sse, '_SESSION_CHECK_INTERVAL', 1)
    monkeypatch.setattr(sse, 'sse_pull_since', lambda cursor, **kwargs:
        [SSEEnvelope(id=cursor + 1, event='probe', data={}, channels=('global',))])


def test_open_stream_returns_its_authentication_connection(limited_pool):
    engine, factory, user_id = limited_pool

    async def check():
        with factory() as db:
            user = db.get(User, user_id)
            response = await sse.sse_stream(ConnectedRequest(), last_event_id=0, current_user=user, db=db)
            try:
                assert engine.pool.checkedout() == 0, 'An idle SSE connection must not retain its authentication DB connection'
            finally:
                await response.body_iterator.aclose()

    asyncio.run(check())


def test_session_rechecks_return_connections_before_streaming_again(limited_pool, monkeypatch):
    engine, factory, user_id = limited_pool
    _configure_events(monkeypatch)

    async def check():
        with factory() as db:
            response = await sse.sse_stream(ConnectedRequest(), last_event_id=0,
                current_user=db.get(User, user_id), db=db)
            # Older FastAPI also closes request dependencies before streaming.
            # A later session check must not reopen and retain that closed Session.
            db.close()
            try:
                for _ in range(4):
                    await anext(response.body_iterator)
                    assert engine.pool.checkedout() == 0, 'SSE session checks must release their short-lived DB connections'
                    with factory() as ordinary_request:
                        assert ordinary_request.get(User, user_id).id == user_id
            finally:
                await response.body_iterator.aclose()

    asyncio.run(check())


def test_stream_stops_after_session_is_replaced(limited_pool, monkeypatch):
    engine, factory, user_id = limited_pool
    _configure_events(monkeypatch)

    async def check():
        with factory() as db:
            response = await sse.sse_stream(ConnectedRequest(), last_event_id=0,
                current_user=db.get(User, user_id), db=db)
            db.close()
            try:
                await anext(response.body_iterator)
                with factory() as other_login:
                    other_login.get(User, user_id).session_token = 'replacement-session'
                    other_login.commit()
                with pytest.raises(StopAsyncIteration):
                    await anext(response.body_iterator)
                assert engine.pool.checkedout() == 0
            finally:
                await response.body_iterator.aclose()

    asyncio.run(check())


def test_idle_stream_does_not_make_ordinary_api_return_500(limited_pool, monkeypatch):
    _, factory, user_id = limited_pool
    _configure_events(monkeypatch)
    app = FastAPI()

    @app.get('/ordinary')
    def ordinary_page():
        with factory() as db:
            return {'id': db.get(User, user_id).id}

    async def check():
        with factory() as db:
            response = await sse.sse_stream(ConnectedRequest(), last_event_id=0,
                current_user=db.get(User, user_id), db=db)
            db.close()
            try:
                await anext(response.body_iterator)
                await anext(response.body_iterator)
                with TestClient(app, raise_server_exceptions=False) as client:
                    assert client.get('/ordinary').status_code == 200, 'A live SSE stream must leave database connections available to normal APIs'
            finally:
                await response.body_iterator.aclose()

    asyncio.run(check())


def test_idle_event_bus_timeouts_do_not_leave_pending_waiter_tasks():
    async def check():
        bus = SSEEventBus()
        before = set(asyncio.all_tasks())
        try:
            for _ in range(5):
                await bus.wait_for_events(timeout=0.001)
            await asyncio.sleep(0)
            leaked = [task for task in asyncio.all_tasks() - before
                if not task.done() and task.get_coro().__qualname__ == 'Event.wait']
            assert not leaked, f'{len(leaked)} orphaned Event.wait tasks remained after idle timeouts'
        finally:
            for task in asyncio.all_tasks() - before:
                task.cancel()

    asyncio.run(check())


def test_one_timed_out_client_does_not_cancel_another_clients_wait():
    async def check():
        bus = SSEEventBus()
        other_client = asyncio.create_task(bus.wait_for_events(timeout=1))
        try:
            await bus.wait_for_events(timeout=0.001)
            assert not other_client.done()
            bus.publish('probe', {'ok': True})
            await asyncio.wait_for(other_client, timeout=0.2)
        finally:
            other_client.cancel()

    asyncio.run(check())
