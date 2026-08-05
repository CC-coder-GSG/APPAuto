from __future__ import annotations

import pytest

from app.models import Requirement, User, Version, VersionType
import app.services.zentao_task_sync_service as tss
from app.services.zentao_task_sync_service import ZentaoTaskSyncService


class FakeClient:
    def __init__(self, assignable=None, existing_tasks=None):
        self._assignable = assignable or {}
        self._existing_tasks = existing_tasks or []
        self.created = []
        self.linked = []
        self.reassigned = []
        self._next_id = 1000

    def list_assignable_users(self, execution_id):
        return self._assignable

    def list_execution_tasks(self, execution_id, limit=500):
        return self._existing_tasks

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
    # 命名规范：父/子任务名统一 [测试] 前缀（2026-07-08）
    assert all(c["name"].startswith("[测试]") for c in client.created)
    assert any(c["name"] == "[测试]V4.0.3.15 测试任务" for c in client.created)
    assert any(c["name"] == "[测试]需求一" for c in client.created)
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


def test_adopts_existing_task_instead_of_recreating(db_session, monkeypatch, setup):
    # r1(story 6706) 在禅道已有一条 test 子任务 8888 → 应被认领，不重复新建。
    existing = [{"id": 8888, "type": "test", "story": 6706, "parent": 9000,
                 "status": "doing", "assignedTo": {"account": "alice"}}]
    client = FakeClient(assignable={"alice": "爱丽丝", "bob": "鲍勃"}, existing_tasks=existing)
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
    db_session.refresh(setup["r1"]); db_session.refresh(setup["r2"])
    # r1 认领已存在任务 8888（不重复建）
    assert setup["r1"].zentao_task_id == 8888
    assert setup["r1"].zentao_parent_task_id == 9000
    assert setup["r1"].zentao_task_status_cache == "doing"
    # r2 没有已存在任务 → 正常新建（父 + 1 子）
    assert setup["r2"].zentao_task_id is not None and setup["r2"].zentao_task_id != 8888
    # 只为 r2 建了 1 父 + 1 子，r1 未新建
    child_creates = [c for c in client.created if c.get("story") == 6706]
    assert child_creates == []  # r1 的 story 没有新建子任务


def test_incremental_assignment_reuses_prefixed_parent(db_session, monkeypatch, setup):
    # 新命名规范的父任务（[测试] 前缀）→ 正常复用
    existing = [{"id": 9100, "type": "test", "story": 0, "parent": 0,
                 "status": "wait", "name": "[测试]V4.0.3.15 测试任务", "assignedTo": {"account": "boss"}}]
    client = FakeClient(assignable={"alice": "爱丽丝", "bob": "鲍勃"}, existing_tasks=existing)
    _patch_client(monkeypatch, client)
    svc = ZentaoTaskSyncService(db_session)
    res = svc.create_tasks_for_assignment(
        setup["major"].id,
        [{"requirement_id": setup["r2"].id, "owner_id": setup["bob"].id}],
        est_started="2026-06-29", deadline="2026-07-03", actor=setup["actor"],
    )
    assert res["ok"] is True, res["errors"]
    assert res["parent_task_id"] == 9100
    assert res["reused_parent"] is True


def test_incremental_assignment_reuses_existing_parent(db_session, monkeypatch, setup):
    # 执行下已有同名存活父任务 9000（旧命名，无前缀）→ 兼容复用，不因改名再建一个
    existing = [{"id": 9000, "type": "test", "story": 0, "parent": 0,
                 "status": "wait", "name": "V4.0.3.15 测试任务", "assignedTo": {"account": "boss"}}]
    client = FakeClient(assignable={"alice": "爱丽丝", "bob": "鲍勃"}, existing_tasks=existing)
    _patch_client(monkeypatch, client)
    svc = ZentaoTaskSyncService(db_session)
    res = svc.create_tasks_for_assignment(
        setup["major"].id,
        [{"requirement_id": setup["r2"].id, "owner_id": setup["bob"].id}],
        est_started="2026-06-29", deadline="2026-07-03", actor=setup["actor"],
    )
    assert res["ok"] is True, res["errors"]
    assert res["parent_task_id"] == 9000
    assert res["reused_parent"] is True
    # 只新建了 1 个子任务（没有新父任务），且挂到旧父任务 9000 下
    assert len(client.created) == 1
    assert client.linked == [(client.created[0]["id"], 9000)]
    db_session.refresh(setup["r2"])
    assert setup["r2"].zentao_parent_task_id == 9000


@pytest.mark.parametrize("parent_status", ["cancel", "closed"])
def test_unusable_parent_not_reused(db_session, monkeypatch, setup, parent_status):
    # 同名父任务已取消或关闭 → 不复用，新建一个
    existing = [{"id": 9000, "type": "test", "story": 0, "parent": 0,
                 "status": parent_status, "name": "V4.0.3.15 测试任务"}]
    client = FakeClient(assignable={"alice": "爱丽丝", "bob": "鲍勃"}, existing_tasks=existing)
    _patch_client(monkeypatch, client)
    svc = ZentaoTaskSyncService(db_session)
    res = svc.create_tasks_for_assignment(
        setup["major"].id,
        [{"requirement_id": setup["r2"].id, "owner_id": setup["bob"].id}],
        est_started="2026-06-29", deadline="2026-07-03", actor=setup["actor"],
    )
    assert res["ok"] is True, res["errors"]
    assert res["reused_parent"] is False
    assert res["parent_task_id"] not in (None, 9000)
    # 新建了 1 父 + 1 子
    assert len(client.created) == 2


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


def test_recreate_when_existing_task_cancelled(db_session, monkeypatch, setup):
    # r1 已关联任务 555，但禅道侧该任务已被取消(cancel) → 作废本地关联并新建，不改派死任务
    setup["r1"].zentao_task_id = 555
    setup["r1"].zentao_task_assigned_to = "alice"
    db_session.commit()
    existing = [{"id": 555, "type": "test", "story": 6706, "parent": 9000,
                 "status": "cancel", "assignedTo": {"account": "alice"}}]
    client = FakeClient(assignable={"alice": "爱丽丝", "bob": "鲍勃"}, existing_tasks=existing)
    _patch_client(monkeypatch, client)
    svc = ZentaoTaskSyncService(db_session)
    res = svc.create_tasks_for_assignment(
        setup["major"].id,
        [{"requirement_id": setup["r1"].id, "owner_id": setup["bob"].id}],
        est_started="2026-06-29", deadline="2026-07-03", actor=setup["actor"],
    )
    assert res["ok"] is True, res["errors"]
    # 没有改派/认领已取消的死任务
    assert client.reassigned == []
    assert res["parent_task_id"] is not None
    db_session.refresh(setup["r1"])
    # 关联被换成新建的任务，指派人回写为新负责人 bob
    assert setup["r1"].zentao_task_id not in (None, 555)
    assert setup["r1"].zentao_task_assigned_to == "bob"


def test_retains_linked_task_when_existing_task_closed(db_session, monkeypatch, setup):
    # 已关闭任务是有效终态：保留原关联，不改派、不重复创建
    setup["r1"].zentao_task_id = 556
    setup["r1"].zentao_parent_task_id = 9000
    db_session.commit()
    existing = [{"id": 556, "type": "test", "story": 6706, "parent": 9000,
                 "status": "closed", "assignedTo": {"account": "alice"}}]
    client = FakeClient(assignable={"alice": "爱丽丝", "bob": "鲍勃"}, existing_tasks=existing)
    _patch_client(monkeypatch, client)
    svc = ZentaoTaskSyncService(db_session)
    res = svc.create_tasks_for_assignment(
        setup["major"].id,
        [{"requirement_id": setup["r1"].id, "owner_id": setup["bob"].id}],
        est_started="2026-06-29", deadline="2026-07-03", actor=setup["actor"],
    )
    assert res["ok"] is True, res["errors"]
    assert client.created == []
    assert client.reassigned == []
    assert res["parent_task_id"] is None
    assert res["retained_closed_tasks"] == [556]
    db_session.refresh(setup["r1"])
    assert setup["r1"].zentao_task_id == 556
    assert setup["r1"].zentao_parent_task_id == 9000
    assert setup["r1"].zentao_task_status_cache == "closed"
    assert setup["r1"].zentao_task_assigned_to == "alice"


def test_adopts_closed_task_by_story_without_recreating(db_session, monkeypatch, setup):
    # 本地未写回任务 id，但同 story 在禅道已有关闭任务 → 认领并保留，不重复创建
    existing = [{"id": 557, "type": "test", "story": 6706, "parent": 9000,
                 "status": "closed", "assignedTo": {"account": "alice"}}]
    client = FakeClient(assignable={"alice": "爱丽丝", "bob": "鲍勃"}, existing_tasks=existing)
    _patch_client(monkeypatch, client)

    res = ZentaoTaskSyncService(db_session).create_tasks_for_assignment(
        setup["major"].id,
        [{"requirement_id": setup["r1"].id, "owner_id": setup["bob"].id}],
        est_started="2026-06-29", deadline="2026-07-03", actor=setup["actor"],
    )

    assert res["ok"] is True, res["errors"]
    assert client.created == []
    assert client.reassigned == []
    assert res["retained_closed_tasks"] == [557]
    db_session.refresh(setup["r1"])
    assert setup["r1"].zentao_task_id == 557
    assert setup["r1"].zentao_parent_task_id == 9000
    assert setup["r1"].zentao_task_status_cache == "closed"


def test_active_task_by_story_wins_over_newer_closed_task(db_session, monkeypatch, setup):
    existing = [
        {"id": 8800, "type": "test", "story": 6706, "parent": 9000,
         "status": "doing", "assignedTo": {"account": "alice"}},
        {"id": 9900, "type": "test", "story": 6706, "parent": 9000,
         "status": "closed", "assignedTo": {"account": "alice"}},
    ]
    client = FakeClient(assignable={"alice": "爱丽丝"}, existing_tasks=existing)
    _patch_client(monkeypatch, client)

    res = ZentaoTaskSyncService(db_session).create_tasks_for_assignment(
        setup["major"].id,
        [{"requirement_id": setup["r1"].id, "owner_id": setup["alice"].id}],
        actor=setup["actor"],
    )

    assert res["ok"] is True, res["errors"]
    assert res["retained_closed_tasks"] == []
    assert client.created == []
    db_session.refresh(setup["r1"])
    assert setup["r1"].zentao_task_id == 8800
    assert setup["r1"].zentao_task_status_cache == "doing"


def test_sync_tasks_status_for_major_updates_cache_and_assignee(db_session, monkeypatch, setup):
    setup["r1"].zentao_task_id = 700
    setup["r1"].zentao_task_status_cache = "wait"
    setup["r1"].zentao_task_assigned_to = "alice"
    db_session.commit()
    existing = [{"id": 700, "type": "test", "story": 6706, "parent": 9000,
                 "status": "done", "assignedTo": {"account": "carol"}}]
    client = FakeClient(existing_tasks=existing)
    _patch_client(monkeypatch, client)
    out = ZentaoTaskSyncService(db_session).sync_tasks_status_for_major(setup["major"].id)
    db_session.refresh(setup["r1"])
    assert setup["r1"].zentao_task_status_cache == "done"
    assert setup["r1"].zentao_task_assigned_to == "carol"
    assert out["updated"] == 1


def test_sync_tasks_status_derives_changed_from_left_and_finish_fact(
    db_session,
    monkeypatch,
    setup,
):
    setup["r1"].zentao_task_id = 701
    setup["r1"].zentao_task_status_cache = "wait"
    db_session.commit()
    completed = [{
        "id": 701,
        "type": "test",
        "story": 6706,
        "parent": 9000,
        "status": "changed",
        "left": 0,
        "finishedDate": "2026-07-24 17:19:11",
        "assignedTo": {"account": "alice"},
    }]
    client = FakeClient(existing_tasks=completed)
    _patch_client(monkeypatch, client)

    ZentaoTaskSyncService(db_session).sync_tasks_status_for_major(setup["major"].id)
    db_session.refresh(setup["r1"])
    assert setup["r1"].zentao_task_status_cache == "done"

    # restart 后禅道仍保留旧 finishedDate，但 left>0，必须恢复为活动态。
    client._existing_tasks = [{
        **completed[0],
        "left": 3,
    }]
    ZentaoTaskSyncService(db_session).sync_tasks_status_for_major(setup["major"].id)
    db_session.refresh(setup["r1"])
    assert setup["r1"].zentao_task_status_cache == "changed"


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
