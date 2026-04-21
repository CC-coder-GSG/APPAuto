from __future__ import annotations

import json

from app.services.zentao_client_service import ZentaoClient


class _FakeResponse:
    def __init__(self, *, status_code: int = 200, text: str = ""):
        self.status_code = status_code
        self.text = text

    def json(self):
        return json.loads(self.text)


def test_get_page_uses_follow_redirects(monkeypatch):
    captured = {}

    def _fake_get(url, **kwargs):
        captured["kwargs"] = kwargs
        payload = {"status": "success", "data": "{\"users\":{\"chenwenbo\":\"陈文博\"}}"}
        return _FakeResponse(status_code=200, text=json.dumps(payload, ensure_ascii=False))

    monkeypatch.setattr("app.services.zentao_client_service.httpx.get", _fake_get)
    client = ZentaoClient("http://zentao.example/zentao", "token")

    result = client.get_page("bug-create-15-1647.json")

    assert result == {"users": {"chenwenbo": "陈文博"}}
    assert captured["kwargs"]["follow_redirects"] is True


def test_get_page_text_uses_follow_redirects(monkeypatch):
    captured = {}

    def _fake_get(url, **kwargs):
        captured["kwargs"] = kwargs
        return _FakeResponse(status_code=200, text="ok")

    monkeypatch.setattr("app.services.zentao_client_service.httpx.get", _fake_get)
    client = ZentaoClient("http://zentao.example/zentao", "token")

    result = client.get_page_text("bug-create-15-1647.html")

    assert result == "ok"
    assert captured["kwargs"]["follow_redirects"] is True
