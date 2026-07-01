from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.models import Requirement, RequirementStatus, User, UserRole, Version, VersionType
from app.models.zentao_task_mirror import ZentaoTaskMirror
import app.services.zentao_task_mirror_service as tms
from app.services.zentao_task_mirror_service import ZentaoTaskMirrorService


def _user(db, username, account=None):
    u = User(username=username, password_hash="x", role=UserRole.USER, zentao_account=account)
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def _major(db, no="V-TW-1"):
    v = Version(version_no=no, version_type=VersionType.MAJOR, zentao_execution_id=1900)
    db.add(v)
    db.commit()
    db.refresh(v)
    return v


def _req(db, major_id, req_no, *, task_id=None, story_id=None, owner_id=None):
    r = Requirement(
        zentao_req_id=req_no, title="需求-" + req_no, major_version_id=major_id,
        status=RequirementStatus.PENDING, zentao_task_id=task_id, zentao_story_id=story_id,
        owner_id=owner_id,
    )
    db.add(r)
    db.commit()
    db.refresh(r)
    return r


def _mirror(db, task_id, user_id, *, account="alice", story=None, status="wait", is_parent=0):
    row = ZentaoTaskMirror(
        task_id=task_id, execution_id=1900, execution_name_cache="V-TW-1",
        parent=0, is_parent=is_parent, name=f"任务{task_id}", type="test", status=status,
        story=story, assigned_to=account, assigned_to_realname=None, assignee_user_id=user_id,
    )
    db.add(row)
    db.commit()
    return row


class FakeClient:
    def __init__(self):
        self.calls = []
        self._tasks = {}

    def set_task(self, tid, data):
        self._tasks[tid] = data

    def start_task(self, task_id, **kw):
        self.calls.append(("start", task_id, kw))
        self._tasks.setdefault(task_id, {}).update({"status": "doing"})
        return {"id": task_id}

    def finish_task(self, task_id, **kw):
        self.calls.append(("finish", task_id, kw))
        self._tasks.setdefault(task_id, {}).update({"status": "done"})
        return {"id": task_id}

    def restart_task(self, task_id, **kw):
        self.calls.append(("restart", task_id, kw))
        self._tasks.setdefault(task_id, {}).update({"status": "doing"})
        return {"id": task_id}

    def pause_task(self, task_id, **kw):
        self.calls.append(("pause", task_id, kw))
        self._tasks.setdefault(task_id, {}).update({"status": "pause"})
        return {"id": task_id}

    def reassign_task(self, task_id, account):
        self.calls.append(("reassign", task_id, account))
        self._tasks.setdefault(task_id, {}).update({"assignedTo": account})
        return {"id": task_id}

    def close_task(self, task_id, **kw):
        self.calls.append(("close", task_id, kw))
        self._tasks.setdefault(task_id, {}).update({"status": "closed"})
        return {"id": task_id}

    def update_task(self, task_id, data):
        self.calls.append(("update", task_id, data))
        self._tasks.setdefault(task_id, {}).update(data)
        return {"id": task_id}

    def get_task(self, task_id):
        return self._tasks.get(task_id)


def test_list_mine_with_links_detects_linkage(db_session):
    alice = _user(db_session, "alice_tw", account="alice")
    major = _major(db_session)
    r1 = _req(db_session, major.id, "r#7001", task_id=555, story_id=6706)
    # 三条任务：按 task_id 关联、按 story 关联、独立
    _mirror(db_session, 555, alice.id, story=6706, status="doing")
    _mirror(db_session, 700, alice.id, story=6706, status="wait")
    _mirror(db_session, 900, alice.id, story=None, status="wait")

    rows = ZentaoTaskMirrorService(db_session).list_mine_with_links(alice)
    by_id = {t["task_id"]: t for t in rows}
    assert by_id[555]["linked_requirement"]["id"] == r1.id
    assert by_id[700]["linked_requirement"]["id"] == r1.id  # 按 story 兜底关联
    assert by_id[900]["linked_requirement"] is None
    assert all(t["assigned_to_me"] for t in rows)  # 账号 alice 与指派人匹配


def test_derived_task_of_others_requirement_is_operable(db_session):
    # 需求归属他人(bob)，但衍生任务指派给 alice → alice 可在任务工作台直接操作禅道，不跳转
    alice = _user(db_session, "alice_d6", account="alice")
    bob = _user(db_session, "bob_d6", account="bob")
    major = _major(db_session, "V-TW-6")
    r = _req(db_session, major.id, "r#7601", task_id=560, owner_id=bob.id)
    _mirror(db_session, 560, alice.id, account="alice", status="wait")
    t = ZentaoTaskMirrorService(db_session).list_mine_with_links(alice)[0]
    assert t["linked_requirement"]["id"] == r.id
    assert t["requirement_mine"] is False
    assert t["can_operate"] is True
    assert t["show_jump"] is False


def test_own_requirement_task_shows_jump_not_operate(db_session):
    # 需求归属本人 → 只提供跳转，不在任务工作台直接操作禅道
    alice = _user(db_session, "alice_d7", account="alice")
    major = _major(db_session, "V-TW-7")
    _req(db_session, major.id, "r#7701", task_id=570, owner_id=alice.id)
    _mirror(db_session, 570, alice.id, account="alice")
    t = ZentaoTaskMirrorService(db_session).list_mine_with_links(alice)[0]
    assert t["requirement_mine"] is True
    assert t["show_jump"] is True
    assert t["can_operate"] is False


def test_list_mine_excludes_parents_and_others(db_session):
    alice = _user(db_session, "alice_tw2", account="alice")
    bob = _user(db_session, "bob_tw2", account="bob")
    major = _major(db_session, "V-TW-2")
    _mirror(db_session, 101, alice.id)                    # mine
    _mirror(db_session, 102, alice.id, is_parent=1)       # parent → excluded
    _mirror(db_session, 103, bob.id, account="bob")       # others → excluded (scope=mine)

    rows = ZentaoTaskMirrorService(db_session).list_mine_with_links(alice)
    assert {t["task_id"] for t in rows} == {101}


def test_operate_task_blocked_for_non_assignee(db_session, monkeypatch):
    alice = _user(db_session, "alice_tw3", account="alice")
    bob = _user(db_session, "bob_tw3", account="bob")
    major = _major(db_session, "V-TW-3")
    _mirror(db_session, 201, alice.id, account="alice", status="wait")
    monkeypatch.setattr(tms, "get_system_zentao_client", lambda db: FakeClient())

    with pytest.raises(HTTPException) as ei:
        ZentaoTaskMirrorService(db_session).operate_task(
            task_id=201, action="start", current_user=bob
        )
    assert ei.value.status_code == 403


def test_operate_task_start_and_close(db_session, monkeypatch):
    alice = _user(db_session, "alice_tw4", account="alice")
    major = _major(db_session, "V-TW-4")
    _mirror(db_session, 301, alice.id, account="alice", status="wait")
    client = FakeClient()
    monkeypatch.setattr(tms, "get_system_zentao_client", lambda db: client)

    svc = ZentaoTaskMirrorService(db_session)
    res = svc.operate_task(task_id=301, action="start", current_user=alice)
    assert res["ok"] is True
    assert ("start", 301, {}) in [(c[0], c[1], {}) for c in client.calls] or client.calls[0][0] == "start"
    # 回读镜像状态刷新为 doing
    row = db_session.query(ZentaoTaskMirror).filter(ZentaoTaskMirror.task_id == 301).first()
    assert row.status == "doing"

    res2 = svc.operate_task(task_id=301, action="close", current_user=alice)
    assert res2["ok"] is True
    row = db_session.query(ZentaoTaskMirror).filter(ZentaoTaskMirror.task_id == 301).first()
    assert row.status == "closed"


def test_operate_task_pause_then_resume(db_session, monkeypatch):
    alice = _user(db_session, "alice_p", account="alice")
    _major(db_session, "V-TW-P")
    _mirror(db_session, 501, alice.id, account="alice", status="doing")
    client = FakeClient()
    client.set_task(501, {"status": "doing", "assignedTo": {"account": "alice"}})
    monkeypatch.setattr(tms, "get_system_zentao_client", lambda db: client)
    svc = ZentaoTaskMirrorService(db_session)

    r1 = svc.operate_task(task_id=501, action="pause", current_user=alice)
    assert r1["ok"] is True
    row = db_session.query(ZentaoTaskMirror).filter(ZentaoTaskMirror.task_id == 501).first()
    assert row.status == "pause"

    # 暂停后用「开始」继续 → 走 restart，回到 doing
    r2 = svc.operate_task(task_id=501, action="start", current_user=alice)
    assert r2["ok"] is True
    assert any(c[0] == "restart" for c in client.calls)
    row = db_session.query(ZentaoTaskMirror).filter(ZentaoTaskMirror.task_id == 501).first()
    assert row.status == "doing"


def test_operate_start_preserves_assignee_via_reassign(db_session, monkeypatch):
    # 模拟禅道 start 后把指派人清空 → 兜底应改派回原指派人
    alice = _user(db_session, "alice_keep", account="alice")
    _major(db_session, "V-TW-K")
    _mirror(db_session, 601, alice.id, account="alice", status="wait")

    class ClearingClient(FakeClient):
        def start_task(self, task_id, **kw):
            self.calls.append(("start", task_id, kw))
            # 禅道副作用：开始后指派人被清空
            self._tasks[task_id] = {"status": "doing", "assignedTo": ""}
            return {"id": task_id}

    client = ClearingClient()
    monkeypatch.setattr(tms, "get_system_zentao_client", lambda db: client)
    svc = ZentaoTaskMirrorService(db_session)
    res = svc.operate_task(task_id=601, action="start", current_user=alice)
    assert res["ok"] is True
    # 兜底改派回 alice
    assert any(c[0] == "reassign" and c[2] == "alice" for c in client.calls)


def test_pause_task_falls_back_to_page_action_when_rest_unauthorized(monkeypatch):
    # 禅道 v1 未开放 /tasks/{id}/pause（401）→ pause_task 回退走传统页面动作 task-pause
    from app.services.zentao_client_service import ZentaoClient, ZentaoAPIError

    client = ZentaoClient(base_url="http://z", token="t")
    calls = {}

    def fake_post(path, body=None):
        raise ZentaoAPIError(401, '{"error":"Unauthorized"}')

    def fake_post_page(path, data=None):
        calls["page"] = (path, data)
        return {"status": "success"}

    monkeypatch.setattr(client, "post", fake_post)
    monkeypatch.setattr(client, "post_page", fake_post_page)
    res = client.pause_task(17246)
    assert calls["page"][0] == "task-pause-17246.json"
    assert res.get("status") == "success"


def test_operate_pause_noop_surfaces_error(db_session, monkeypatch):
    # 禅道返回 200 但状态没真正切换（仍 doing）→ 应报「未生效」而非假成功
    alice = _user(db_session, "alice_noop", account="alice")
    _major(db_session, "V-TW-N")
    _mirror(db_session, 701, alice.id, account="alice", status="doing")

    class NoopPauseClient(FakeClient):
        def pause_task(self, task_id, **kw):
            self.calls.append(("pause", task_id, kw))
            self._tasks[task_id] = {"status": "doing", "assignedTo": {"account": "alice"}}
            return {"message": "success"}

    client = NoopPauseClient()
    monkeypatch.setattr(tms, "get_system_zentao_client", lambda db: client)
    res = ZentaoTaskMirrorService(db_session).operate_task(task_id=701, action="pause", current_user=alice)
    assert res["ok"] is False
    assert any("未生效" in e for e in res["errors"])


def test_operate_task_set_time_validates(db_session, monkeypatch):
    alice = _user(db_session, "alice_tw5", account="alice")
    major = _major(db_session, "V-TW-5")
    _mirror(db_session, 401, alice.id, account="alice")
    monkeypatch.setattr(tms, "get_system_zentao_client", lambda db: FakeClient())
    svc = ZentaoTaskMirrorService(db_session)
    with pytest.raises(HTTPException) as ei:
        svc.operate_task(task_id=401, action="set_time", current_user=alice, hours=0)
    assert ei.value.status_code == 400
