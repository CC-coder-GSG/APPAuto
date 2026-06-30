from __future__ import annotations

import pytest

from app.models import Requirement, User, Version, VersionType
import app.services.zentao_task_sync_service as tss
from app.services.zentao_task_sync_service import ZentaoTaskSyncService


class FakeClient:
    def __init__(self, assignable=None):
        self._assignable = assignable or {}
        self.created = []
        self.linked = []
        self.reassigned = []
        self._next_id = 1000

    def list_assignable_users(self, execution_id):
        return self._assignable

    def create_execution_task(self, execution_id, **kwargs):
        self._next_id += 1
        self.created.append({"execution_id": execution_id, **kwargs, "id": self._next_id})
        return {"id": self._next_id}

    def link_task_parent(self, child_id, parent_id):
        self.linked.append((child_id, parent_id))
        return {"id": child_id, "parent": parent_id}

    def reassign_task(self, task_id, account):
        self.reassigned.append((task_id, account))
        return {"id": task_id, "assignedTo": account}


@pytest.fixture()
def setup(db_session, monkeypatch):
    # 关掉节假日联网
    monkeypatch.setattr(tss, "get_holiday_map", lambda db, a, b: {})
    major = Version(version_no="V4.0.3.15", version_type=VersionType.MAJOR, zentao_execution_id=1896)
    db_session.add(major)
    db_session.flush()
    alice = User(username="alice", display_name="爱丽丝", password_hash="x", zentao_account="alice")
    bob = User(username="bob", display_name="鲍勃", password_hash="x")  # 无账号，靠姓名匹配
    actor = User(username="boss", display_name="老板", password_hash="x", zentao_account="boss")
    db_session.add_all([alice, bob, actor])
    db_session.flush()
    r1 = Requirement(zentao_req_id="ST1", title="需求一", major_version_id=major.id, zentao_story_id=6706, estimated_test_hours=4.0)
    r2 = Requirement(zentao_req_id="ST2", title="需求二", major_version_id=major.id, zentao_story_id=6705, estimated_test_hours=6.0)
    db_session.add_all([r1, r2])
    db_session.commit()
    return {"major": major, "alice": alice, "bob": bob, "actor": actor, "r1": r1, "r2": r2}


def _patch_client(monkeypatch, client):
    monkeypatch.setattr(tss, "get_system_zentao_client", lambda db: client)


def test_create_parent_and_children_for_new_assignments(db_session, monkeypatch, setup):
    client = FakeClient(assignable={"alice": "爱丽丝", "bob": "鲍勃"})
    _patch_client(monkeypatch, client)
    svc = ZentaoTaskSyncService(db_session)
    res = svc.create_tasks_for_assignment(
        setup["major"].id,
        [
            {"requirement_id": setup["r1"].id, "owner_id": setup["alice"].id},
            {"requirement_id": setup["r2"].id, "owner_id": setup["bob"].id},
        ],
        est_started="2026-06-29",
        deadline="2026-07-03",
        actor=setup["actor"],
    )
    assert res["ok"] is True, res["errors"]
    assert res["parent_task_id"] is not None
    # 1 父 + 2 子
    assert len(client.created) == 3
    assert len(res["created_tasks"]) == 2
    assert len(client.linked) == 2
    # 本地回写
    db_session.refresh(setup["r1"]); db_session.refresh(setup["r2"])
    assert setup["r1"].zentao_task_id is not None
    assert setup["r1"].zentao_parent_task_id == res["parent_task_id"]
    assert setup["r1"].zentao_task_status_cache == "wait"
    # 父任务 estimate = 5 工作日 × 8 = 40
    parent = next(c for c in client.created if c.get("estimate") == 40.0)
    assert parent["task_type"] == "test"
    # bob 通过姓名匹配回填了账号
    db_session.refresh(setup["bob"])
    assert setup["bob"].zentao_account == "bob"


def test_child_estimate_uses_requirement_hours(db_session, monkeypatch, setup):
    client = FakeClient(assignable={"alice": "爱丽丝", "bob": "鲍勃"})
    _patch_client(monkeypatch, client)
    svc = ZentaoTaskSyncService(db_session)
    svc.create_tasks_for_assignment(
        setup["major"].id,
        [{"requirement_id": setup["r2"].id, "owner_id": setup["bob"].id}],
        est_started="2026-06-29", deadline="2026-07-03", actor=setup["actor"],
    )
    child = next(c for c in client.created if c.get("story") == 6705)
    assert child["estimate"] == 6.0  # r2.estimated_test_hours


def test_reassign_existing_task_no_new_parent(db_session, monkeypatch, setup):
    # r1 已有任务 → 换负责人应走改派，不建父任务
    setup["r1"].zentao_task_id = 555
    db_session.commit()
    client = FakeClient(assignable={"alice": "爱丽丝", "bob": "鲍勃"})
    _patch_client(monkeypatch, client)
    svc = ZentaoTaskSyncService(db_session)
    res = svc.create_tasks_for_assignment(
        setup["major"].id,
        [{"requirement_id": setup["r1"].id, "owner_id": setup["bob"].id}],
        est_started="2026-06-29", deadline="2026-07-03", actor=setup["actor"],
    )
    assert res["parent_task_id"] is None
    assert client.created == []
    assert client.reassigned == [(555, "bob")]
    assert res["reassigned_tasks"] == [555]


def test_unassigned_when_no_account_match(db_session, monkeypatch, setup):
    # 可指派列表里没有 bob 的姓名/账号 → 列入 unassigned
    client = FakeClient(assignable={"alice": "爱丽丝"})
    _patch_client(monkeypatch, client)
    svc = ZentaoTaskSyncService(db_session)
    res = svc.create_tasks_for_assignment(
        setup["major"].id,
        [{"requirement_id": setup["r2"].id, "owner_id": setup["bob"].id}],
        est_started="2026-06-29", deadline="2026-07-03", actor=setup["actor"],
    )
    # 父任务建了，但 bob 的子任务因无账号被跳过
    assert any(u["owner_name"] == "鲍勃" for u in res["unassigned"])
    db_session.refresh(setup["r2"])
    assert setup["r2"].zentao_task_id is None


def test_no_execution_binding_returns_error(db_session, monkeypatch):
    monkeypatch.setattr(tss, "get_holiday_map", lambda db, a, b: {})
    major = Version(version_no="V9.9", version_type=VersionType.MAJOR)  # 无 zentao_execution_id
    db_session.add(major)
    db_session.commit()
    svc = ZentaoTaskSyncService(db_session)
    res = svc.create_tasks_for_assignment(major.id, [], actor=None)
    assert res["ok"] is False
    assert any("未绑定禅道执行" in e for e in res["errors"])
