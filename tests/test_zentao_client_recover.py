"""ZentaoClient.create_execution_task 在禅道返回空体（无 id）时回查找回任务 id。

复现线上问题：禅道建任务成功但返回 {"message":"success"}（无 id），
导致本地需求写不回 zentao_task_id、显示「未关联任务」。
"""
from __future__ import annotations

from app.services.zentao_client_service import ZentaoClient


def test_create_task_recovers_id_when_response_has_no_id(monkeypatch):
    cli = ZentaoClient(base_url="http://z", token="t")
    # 禅道 create 返回空体 → _parse_write_response 给出 {"message":"success"}（无 id）
    monkeypatch.setattr(cli, "post", lambda path, body=None: {"message": "success"})
    monkeypatch.setattr(
        cli,
        "list_execution_tasks",
        lambda execution_id, limit=500: [
            {"id": 10, "name": "需求一", "type": "test", "assignedTo": {"account": "alice"}, "story": 6706},
            {"id": 22, "name": "需求一", "type": "test", "assignedTo": {"account": "alice"}, "story": 6706},
            {"id": 30, "name": "别的任务", "type": "test", "assignedTo": {"account": "alice"}},
        ],
    )
    out = cli.create_execution_task(99, name="需求一", assigned_to="alice", story=6706)
    # 取名称(+指派人/需求)匹配里 id 最大（最新建）的一条
    assert out and out.get("id") == 22


def test_create_task_uses_direct_id_without_recovery(monkeypatch):
    cli = ZentaoClient(base_url="http://z", token="t")
    monkeypatch.setattr(cli, "post", lambda path, body=None: {"id": 777})
    calls = {"n": 0}

    def _list(*a, **k):
        calls["n"] += 1
        return []

    monkeypatch.setattr(cli, "list_execution_tasks", _list)
    out = cli.create_execution_task(99, name="x", assigned_to="alice")
    assert out["id"] == 777
    assert calls["n"] == 0  # 已带 id 时不应触发回查


def test_create_task_recovery_returns_none_when_no_match(monkeypatch):
    cli = ZentaoClient(base_url="http://z", token="t")
    monkeypatch.setattr(cli, "post", lambda path, body=None: {"message": "success"})
    monkeypatch.setattr(cli, "list_execution_tasks", lambda execution_id, limit=500: [])
    out = cli.create_execution_task(99, name="需求一", assigned_to="alice")
    # 找不回时退回原响应（无 id），交由上层报错
    assert out == {"message": "success"}
