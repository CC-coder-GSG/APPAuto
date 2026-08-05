from types import SimpleNamespace

import pytest

from app.api.routes import zentao_hydrate
from app.models.zentao_task_mirror import ZentaoTaskMirror
from app.services.zentao_client_service import ZentaoAPIError


class _TaskClient:
    def __init__(self, task):
        self.task = task

    def get_task(self, task_id):
        assert task_id == 42
        return self.task


class _ExecutionListFallbackClient:
    def __init__(self, task):
        self.task = task
        self.list_calls = []

    def get_task(self, task_id):
        if task_id == 17682:
            raise ZentaoAPIError(500, "Zentao PHP Fatal Error")
        assert task_id == 17681
        return {"id": 17681, "name": "parent task"}

    def list_execution_tasks(self, execution_id, limit=500):
        self.list_calls.append((execution_id, limit))
        return [self.task]


@pytest.mark.parametrize(
    ("finished_by", "finished_by_realname", "expected"),
    [
        ({"account": "zhangsan", "realname": "张三"}, None, "张三"),
        ({"account": "lisi"}, "李四", "李四"),
        ("wangwu", "王五", "王五"),
        ("zhaoliu", None, "zhaoliu"),
        (None, None, ""),
    ],
)
def test_task_preview_returns_finisher(
    monkeypatch,
    db_session,
    finished_by,
    finished_by_realname,
    expected,
):
    task = {
        "id": 42,
        "name": "回归测试",
        "status": "done",
        "parent": 0,
        "assignedTo": {"account": "next_owner", "realname": "下一处理人"},
        "finishedBy": finished_by,
        "finishedByRealName": finished_by_realname,
        "finishedDate": "2026-07-27 15:30:00",
    }
    client = _TaskClient(task)
    monkeypatch.setattr(
        zentao_hydrate,
        "_get_client_ctx",
        lambda user_id, db: (client, "http://zentao.example"),
    )

    result = zentao_hydrate.get_task_detail(
        42,
        current_user=SimpleNamespace(id=7),
        db=db_session,
    )

    assert result["assigned_to"] == "下一处理人"
    assert result["finished_by"] == expected
    assert result["finished_date"] == "2026-07-27T15:30:00"


def test_task_preview_falls_back_to_execution_list(monkeypatch, db_session):
    db_session.add(
        ZentaoTaskMirror(
            task_id=17682,
            execution_id=1910,
            parent=17681,
            is_parent=0,
            name="cached task name",
        )
    )
    db_session.commit()

    task = {
        "id": 17682,
        "name": "[test] export XML",
        "type": "test",
        "status": "pause",
        "parent": 17681,
        "assignedTo": {"account": "wangchao", "realname": "Wang Chao"},
        "estimate": 4,
        "consumed": 2,
        "left": 8,
        "realStarted": "2026-07-15T08:50:33Z",
        "desc": "story r#6790",
    }
    client = _ExecutionListFallbackClient(task)
    monkeypatch.setattr(
        zentao_hydrate,
        "_get_client_ctx",
        lambda user_id, db: (client, "http://zentao.example"),
    )

    result = zentao_hydrate.get_task_detail(
        17682,
        current_user=SimpleNamespace(id=7),
        db=db_session,
    )

    assert result["id"] == 17682
    assert result["name"] == "[test] export XML"
    assert result["status"] == "pause"
    assert result["parent_name"] == "parent task"
    assert result["assigned_to"] == "Wang Chao"
    assert result["desc"] == "story r#6790"
    assert result["real_started"]
    assert client.list_calls == [(1910, 2000)]
