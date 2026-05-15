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


def test_create_execution_build_posts_to_projects_endpoint():
    """禅道 IPD 4.3 实测：/v1/executions/{id}/builds 静默不写库，必须走
    /v1/projects/{project_id}/builds，body 里带 execution=exec_id。"""
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
    )

    assert captured["path"] == "projects/134/builds"
    assert captured["body"]["execution"] == 1647
    assert captured["body"]["product"] == 15
    assert captured["body"]["name"] == "4.0.3.1.260514(40301051)(64-bit)"
    assert captured["body"]["builder"] == "chenwenbo"
    assert captured["body"]["date"] == "2026-05-15"
    assert result == {"id": 4591, "name": "4.0.3.1.260514(40301051)(64-bit)"}


def test_create_execution_build_requires_project_id():
    """没解析到 project_id 不能继续 —— 否则禅道会静默吞掉请求，调用方拿不到任何信号。"""
    import pytest

    client = ZentaoClient("http://zentao.example/zentao", "token")
    with pytest.raises(ValueError, match="project_id"):
        client.create_execution_build(1647, "x", product_id=15)
