"""预览提速（2026-11-24）：附件复用网页会话、Bug 详情并行拉取、附件可被浏览器缓存。"""
from __future__ import annotations

import threading

import httpx
import pytest

from app.api.routes import zentao_bug_actions
from app.services import zentao_web_session as web
from app.services.zentao_client_service import ZentaoClient
from app.services.zentao_v2_protocol import ZentaoAPIError
from tests.test_zentao_bug_actions import _bind_user, _create_user, _make_client


@pytest.fixture(autouse=True)
def _clean_file_sessions():
    web._FILE_SESSIONS.clear()
    yield
    web._FILE_SESSIONS.clear()


class _FileClient:
    def __init__(self, pages):
        self.pages = list(pages)  # 依次返回的 content-type（'image/png' / 'text/html'）
        self.urls = []

    def get(self, url, **kwargs):
        self.urls.append(url)
        ctype = self.pages.pop(0) if self.pages else 'image/png'
        return httpx.Response(200, content=b'img', headers={'content-type': ctype})

    def close(self):
        pass


def test_file_reads_reuse_one_web_login(monkeypatch):
    logins = []
    client = _FileClient([])
    monkeypatch.setattr(web, '_open_web_session', lambda login: logins.append(login.account) or client)
    login = web.ZentaoWebLogin('https://z.invalid/zentao', 'alice', 'pw')
    for file_id in (1, 2, 3):
        assert web.fetch_file_via_web(login, file_id).content == b'img'
    assert logins == ['alice']
    assert client.urls == [f'https://z.invalid/zentao/file-read-{i}.html' for i in (1, 2, 3)]


def test_concurrent_file_reads_share_a_single_login(monkeypatch):
    logins = []
    gate = threading.Event()

    def open_session(login):
        logins.append(login.account)
        gate.wait(1)
        return _FileClient([])

    monkeypatch.setattr(web, '_open_web_session', open_session)
    login = web.ZentaoWebLogin('https://z.invalid/zentao', 'alice', 'pw')
    threads = [threading.Thread(target=web.fetch_file_via_web, args=(login, i)) for i in range(1, 6)]
    for t in threads:
        t.start()
    gate.set()
    for t in threads:
        t.join(5)
    assert logins == ['alice']


def test_expired_cached_session_relogs_once(monkeypatch):
    expired = _FileClient(['text/html'])
    fresh = _FileClient(['image/png'])
    sessions = [expired, fresh]
    monkeypatch.setattr(web, '_open_web_session', lambda login: sessions.pop(0))
    monkeypatch.setattr(web, '_retire_file_session', lambda *a: None)
    login = web.ZentaoWebLogin('https://z.invalid/zentao', 'alice', 'pw')
    assert web.fetch_file_via_web(login, 7).content == b'img'
    assert expired.urls and fresh.urls
    # 第二次直接复用新会话，不再登录
    assert web.fetch_file_via_web(login, 8).content == b'img'
    assert sessions == []


def test_file_returning_page_after_relogin_still_reports_permission(monkeypatch):
    sessions = [_FileClient(['text/html']), _FileClient(['text/html'])]
    monkeypatch.setattr(web, '_open_web_session', lambda login: sessions.pop(0))
    monkeypatch.setattr(web, '_retire_file_session', lambda *a: None)
    with pytest.raises(ZentaoAPIError) as error:
        web.fetch_file_via_web(web.ZentaoWebLogin('https://z.invalid/zentao', 'alice', 'pw'), 7)
    assert error.value.status_code == 502


def test_bug_detail_and_history_are_fetched_in_parallel(monkeypatch):
    cli = ZentaoClient('https://z.invalid', 'secret')
    both_started = threading.Barrier(2, timeout=2)

    def get_bug(bug_id):
        both_started.wait()  # 只有两个请求同时在途才会通过
        return {'id': bug_id, 'title': 'v2'}

    def get_page(path):
        both_started.wait()
        return {'bug': {'id': 8, 'title': 'page'}, 'actions': [{'id': 1}]}

    monkeypatch.setattr(cli, 'get_bug', get_bug)
    monkeypatch.setattr(cli, 'get_page', get_page)
    result = cli.get_bug_with_fallback(8)
    assert result['title'] == 'v2'
    assert result['actions'] == [{'id': 1}]


def test_bug_detail_401_still_propagates(monkeypatch):
    cli = ZentaoClient('https://z.invalid', 'secret')
    monkeypatch.setattr(cli, 'get_bug', lambda *a: (_ for _ in ()).throw(ZentaoAPIError(401, 'expired')))
    monkeypatch.setattr(cli, 'get_page', lambda *a: {'bug': {'id': 8}})
    with pytest.raises(ZentaoAPIError) as error:
        cli.get_bug_with_fallback(8)
    assert error.value.status_code == 401


def test_file_proxy_is_privately_cacheable(db_session, monkeypatch):
    user = _create_user(db_session)
    _bind_user(db_session, user.id)

    class Client:
        def get_file(self, file_id):
            return httpx.Response(200, content=b'png-bytes', headers={'content-type': 'image/png'})

    monkeypatch.setattr(zentao_bug_actions, '_get_client', lambda *a: Client())
    client = _make_client(db_session, user)
    try:
        resp = client.get('/zentao/files/42')
        assert resp.status_code == 200
        assert resp.content == b'png-bytes'
        assert resp.headers['content-type'] == 'image/png'
        assert 'private' in resp.headers['cache-control']
        assert 'max-age' in resp.headers['cache-control']
    finally:
        client.app.dependency_overrides.clear()
        client.close()
