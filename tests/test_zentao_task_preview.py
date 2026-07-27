from types import SimpleNamespace

import pytest

from app.api.routes import zentao_hydrate


class _TaskClient:
    def __init__(self, task):
        self.task = task

    def get_task(self, task_id):
        assert task_id == 42
        return self.task


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
