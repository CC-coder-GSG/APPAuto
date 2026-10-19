from __future__ import annotations

import json

from app.services.zentao_client_service import ZentaoClient


class _FakeResponse:
    def __init__(self, *, status_code: int = 200, text: str = ""):
        self.status_code = status_code
        self.text = text

    def json(self):
        return json.loads(self.text)


def test_token_page_does_not_forward_token_to_redirect(monkeypatch):
    captured = {}

    def _fake_get(url, **kwargs):
        captured["kwargs"] = kwargs
        payload = {"status": "success", "data": "{\"users\":{\"chenwenbo\":\"陈文博\"}}"}
        return _FakeResponse(status_code=200, text=json.dumps(payload, ensure_ascii=False))

    monkeypatch.setattr("app.services.zentao_client_service.httpx.get", _fake_get)
    client = ZentaoClient("http://zentao.example/zentao", "token")

    result = client.get_page("bug-create-15-1647.json")

    assert result == {"users": {"chenwenbo": "陈文博"}}
    assert captured["kwargs"]["follow_redirects"] is False


def test_token_html_does_not_forward_token_to_redirect(monkeypatch):
    captured = {}

    def _fake_get(url, **kwargs):
        captured["kwargs"] = kwargs
        return _FakeResponse(status_code=200, text="ok")

    monkeypatch.setattr("app.services.zentao_client_service.httpx.get", _fake_get)
    client = ZentaoClient("http://zentao.example/zentao", "token")

    result = client.get_page_text("bug-create-15-1647.html")

    assert result == "ok"
    assert captured["kwargs"]["follow_redirects"] is False


def test_create_execution_build_uses_v2_required_fields():
    captured = {}
    client = ZentaoClient("http://zentao.example/zentao", "token")

    def _fake_post(path, body):
        captured["path"] = path
        captured["body"] = body
        return {"id": 4591, "name": body["name"]}

    client.post = _fake_post  # type: ignore[assignment]

    result = client.create_execution_build(
        1647,
        "4.0.3.1.260514(40301051)(64-bit)",
        project_id=134,
        product_id=15,
        builder="chenwenbo",
        date="2026-05-15",
        system_id=8,
    )

    assert captured["path"] == "builds"
    assert captured["body"]["executionID"] == 1647
    assert captured["body"]["system"] == 8
    assert captured["body"]["product"] == 15
    assert captured["body"]["name"] == "4.0.3.1.260514(40301051)(64-bit)"
    assert captured["body"]["builder"] == "chenwenbo"
    assert captured["body"]["date"] == "2026-05-15"
    assert result == {"id": 4591, "name": "4.0.3.1.260514(40301051)(64-bit)"}


def test_create_execution_build_requires_product_and_builder_before_write():
    import pytest

    client = ZentaoClient("http://zentao.example/zentao", "token")
    from app.services.zentao_client_service import ZentaoAPIError
    with pytest.raises(ZentaoAPIError, match="产品"):
        client.create_execution_build(1647, "x", builder="alice")
    with pytest.raises(ZentaoAPIError, match="构建者"):
        client.create_execution_build(1647, "x", product_id=15)


def test_create_execution_build_defaults_date_to_today():
    """禅道 IPD 4.3：date 不传 → 400 "打包日期不能为空"。客户端默认填今天，
    免得每个调用方都得记着传这个字段。"""
    from datetime import datetime
    captured = {}
    client = ZentaoClient("http://zentao.example/zentao", "token")

    def _fake_post(path, body):
        captured["body"] = body
        return {"id": 5000, "name": body["name"]}

    client.post = _fake_post  # type: ignore[assignment]

    client.create_execution_build(
        1647, "build-x",
        project_id=134, product_id=15, builder="chenwenbo",
        system_id=8,
        # date omitted on purpose
    )
    assert captured["body"]["date"] == datetime.now().strftime("%Y-%m-%d")
