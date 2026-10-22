from datetime import timedelta

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.deps import get_current_user, get_db
from app.api.routes import zentao_binding, zentao_hydrate
from app.models import User
from app.models.user_zentao_binding import UserZentaoBinding
from app.services import zentao_auth_service as auth
from app.utils.time_utils import utc_now_naive


@pytest.fixture
def binding(db_session):
    user = User(username='token-refresh-test', password_hash='unused', zentao_account='alice')
    db_session.add(user)
    db_session.flush()
    row = UserZentaoBinding(user_id=user.id, base_url='https://zentao.invalid', zentao_account='alice',
        zentao_password_ciphertext=auth.encrypt_password('test-password'), token_value='expired-token',
        token_expires_at=utc_now_naive() - timedelta(minutes=5), last_refresh_status='ok_v2')
    db_session.add(row)
    db_session.commit()
    return row, user


@pytest.fixture
def client(db_session, binding):
    app = FastAPI()
    app.include_router(zentao_binding.router)
    app.include_router(zentao_hydrate.router)
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_current_user] = lambda: binding[1]
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


def test_manual_refresh_renews_expired_token_without_exposing_credentials(client, binding, monkeypatch):
    calls = []

    def login(url, **kwargs):
        calls.append(url)
        return httpx.Response(200, json={'status': 'success', 'token': 'fresh-token'})

    monkeypatch.setattr(httpx, 'post', login)
    response = client.post('/zentao-binding/me/refresh')
    assert response.status_code == 200
    data = response.json()
    assert data['ok'] is True and data['binding']['has_token'] is True
    assert binding[0].token_value == 'fresh-token'
    assert calls == ['https://zentao.invalid/api.php/v2/users/login']
    assert 'fresh-token' not in response.text and 'test-password' not in response.text


def test_upstream_login_500_returns_a_controlled_refresh_failure(client, monkeypatch):
    monkeypatch.setattr(httpx, 'post', lambda *args, **kwargs: httpx.Response(500, text='upstream error'))
    response = client.post('/zentao-binding/me/refresh')
    assert response.status_code == 200
    assert response.json()['ok'] is False
    assert '500' in response.json()['message'], 'Refresh failures should retain a safe, actionable reason'


@pytest.mark.parametrize('status, expected', [(401, '账号和密码'), (403, '权限'), (503, '503')])
def test_refresh_failure_reports_status_without_upstream_diagnostics(client, monkeypatch, status, expected):
    monkeypatch.setattr(httpx, 'post', lambda *args, **kwargs: httpx.Response(status, text='private-diagnostics'))
    response = client.post('/zentao-binding/me/refresh')
    assert response.status_code == 200 and response.json()['ok'] is False
    assert expected in response.json()['message']
    assert 'private-diagnostics' not in response.text


def test_login_timeout_is_reported_as_temporary_not_bad_credentials(client, monkeypatch):
    def login(*args, **kwargs):
        raise httpx.ReadTimeout('private-diagnostics')
    monkeypatch.setattr(httpx, 'post', login)
    response = client.post('/zentao-binding/me/refresh')
    assert response.status_code == 200 and response.json()['ok'] is False
    assert '超时' in response.json()['message']
    assert 'private-diagnostics' not in response.text


@pytest.mark.parametrize('kind', ['bugs', 'stories'])
def test_login_failure_is_retryable_instead_of_being_cached_as_missing(client, monkeypatch, kind):
    monkeypatch.setattr(httpx, 'post', lambda *args, **kwargs: httpx.Response(500, text='upstream error'))
    response = client.get(f'/zentao/hydrate/{kind}?ids=101,102')
    assert response.status_code == 502
    assert '500' in response.json()['detail']


@pytest.mark.parametrize('kind', ['bugs', 'stories'])
def test_expired_token_refresh_failure_keeps_all_ids_retryable(client, db_session, binding, monkeypatch, kind):
    row, _ = binding
    row.token_expires_at = utc_now_naive() + timedelta(hours=1)
    db_session.commit()
    monkeypatch.setattr(httpx, 'post', lambda *args, **kwargs: httpx.Response(500, text='upstream error'))
    if kind == 'bugs':
        monkeypatch.setattr(zentao_hydrate, '_fetch_bugs_concurrent', lambda *args: _expired_batch())
    else:
        monkeypatch.setattr(zentao_hydrate, '_fetch_stories_concurrent', lambda *args: _expired_batch())
    response = client.get(f'/zentao/hydrate/{kind}?ids=101,102')
    assert response.status_code == 502
    assert '500' in response.json()['detail']


async def _expired_batch():
    return {}, True, []


@pytest.mark.parametrize('kind', ['bugs', 'stories'])
def test_read_after_temporary_login_failure_can_refresh_and_recover(client, monkeypatch, kind):
    monkeypatch.setattr(httpx, 'post', lambda *args, **kwargs: httpx.Response(500, text='upstream error'))
    assert client.get(f'/zentao/hydrate/{kind}?ids=101').status_code == 502
    monkeypatch.setattr(httpx, 'post', lambda *args, **kwargs:
        httpx.Response(200, json={'status': 'success', 'token': 'fresh-token'}))

    async def fetch(client, *args):
        assert client.token == 'fresh-token'
        return {'101': {'id': 101, 'title': 'recovered'}}, False, []

    monkeypatch.setattr(zentao_hydrate, f'_fetch_{kind}_concurrent', fetch)
    response = client.get(f'/zentao/hydrate/{kind}?ids=101')
    assert response.status_code == 200 and response.json()['101']['title'] == 'recovered'


@pytest.mark.parametrize('kind', ['bugs', 'stories'])
def test_server_expired_token_is_refreshed_once_and_original_read_retried(client, db_session, binding, monkeypatch, kind):
    row, _ = binding
    row.token_expires_at = utc_now_naive() + timedelta(hours=1)
    db_session.commit()
    logins, tokens = [], []

    def login(*args, **kwargs):
        logins.append(True)
        return httpx.Response(200, json={'status': 'success', 'token': 'fresh-token'})

    async def fetch(client, *args):
        tokens.append(client.token)
        return ({}, True, []) if client.token == 'expired-token' else ({'101': {'id': 101}}, False, [])

    monkeypatch.setattr(httpx, 'post', login)
    monkeypatch.setattr(zentao_hydrate, f'_fetch_{kind}_concurrent', fetch)
    response = client.get(f'/zentao/hydrate/{kind}?ids=101')
    assert response.status_code == 200 and response.json()['101']['id'] == 101
    assert tokens == ['expired-token', 'fresh-token'] and logins == [True]
