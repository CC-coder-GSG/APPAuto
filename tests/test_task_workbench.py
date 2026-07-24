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


def _mirror(db, task_id, user_id, *, account="alice", story=None, status="wait", is_parent=0, parent=0):
    row = ZentaoTaskMirror(
        task_id=task_id, execution_id=1900, execution_name_cache="V-TW-1",
        parent=parent, is_parent=is_parent, name=f"任务{task_id}", type="test", status=status,
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


def test_list_mine_includes_parents_with_children(db_session):
    # 2026-07-06：父任务同步在其指派人名下展示，带全部子任务与完成进度；
    # 他人任务仍然排除（scope=mine）。
    alice = _user(db_session, "alice_tw2", account="alice")
    bob = _user(db_session, "bob_tw2", account="bob")
    major = _major(db_session, "V-TW-2")
    _mirror(db_session, 101, alice.id)                                          # mine 独立任务
    _mirror(db_session, 102, alice.id, is_parent=1, status="doing")             # mine 父任务
    _mirror(db_session, 103, bob.id, account="bob")                             # 他人任务 → 排除
    _mirror(db_session, 104, alice.id, parent=102, status="done")               # 父任务的子任务（我的）
    _mirror(db_session, 105, bob.id, account="bob", parent=102, status="wait")  # 父任务的子任务（他人的）

    rows = ZentaoTaskMirrorService(db_session).list_mine_with_links(alice)
    by_id = {t["task_id"]: t for t in rows}
    assert set(by_id) == {101, 102, 104}
    parent = by_id[102]
    assert parent["is_parent"] is True
    assert parent["children_total"] == 2
    assert parent["children_done"] == 1
    assert {c["task_id"] for c in parent["children"]} == {104, 105}
    # 我的子任务带父任务概要
    assert by_id[104]["parent_info"]["task_id"] == 102
    assert by_id[101]["parent_info"] is None


def test_parent_task_operations_follow_zentao_rules(db_session, monkeypatch):
    # 子任务未完成：父任务只能暂停/取消，不能开始/完成/关闭/设置工时
    alice = _user(db_session, "alice_pt", account="alice")
    _major(db_session, "V-TW-PT")
    _mirror(db_session, 210, alice.id, is_parent=1, status="doing")
    _mirror(db_session, 211, alice.id, parent=210, status="doing")
    client = FakeClient()
    client.set_task(210, {"status": "doing", "assignedTo": {"account": "alice"}})
    monkeypatch.setattr(tms, "get_system_zentao_client", lambda db: client)
    monkeypatch.setattr(tms, "get_user_zentao_web_login", lambda uid, db: None)
    monkeypatch.setattr(tms, "get_system_zentao_web_login", lambda db: None)
    svc = ZentaoTaskMirrorService(db_session)

    for bad in ("start", "finish", "close", "set_time"):
        with pytest.raises(HTTPException) as ei:
            svc.operate_task(task_id=210, action=bad, current_user=alice, hours=4)
        assert ei.value.status_code == 400

    # 暂停允许（REST pause 生效）
    res = svc.operate_task(task_id=210, action="pause", current_user=alice)
    assert res["ok"] is True

    # 暂停后允许「继续」(start)
    res2 = svc.operate_task(task_id=210, action="start", current_user=alice)
    assert res2["ok"] is True


def test_parent_task_done_can_close(db_session, monkeypatch):
    # 子任务全部完成 → 禅道自动置父任务 done → 此时允许关闭
    alice = _user(db_session, "alice_pd", account="alice")
    _major(db_session, "V-TW-PD")
    _mirror(db_session, 220, alice.id, is_parent=1, status="done")
    _mirror(db_session, 221, alice.id, parent=220, status="done")
    client = FakeClient()
    monkeypatch.setattr(tms, "get_system_zentao_client", lambda db: client)
    res = ZentaoTaskMirrorService(db_session).operate_task(task_id=220, action="close", current_user=alice)
    assert res["ok"] is True
    assert any(c[0] == "close" for c in client.calls)


def test_cancel_action_via_rest(db_session, monkeypatch):
    alice = _user(db_session, "alice_cx", account="alice")
    _major(db_session, "V-TW-CX")
    _mirror(db_session, 230, alice.id, is_parent=1, status="doing")
    _mirror(db_session, 231, alice.id, parent=230, status="wait")

    class CancelClient(FakeClient):
        def cancel_task(self, task_id, **kw):
            self.calls.append(("cancel", task_id, kw))
            self._tasks.setdefault(task_id, {}).update({"status": "cancel"})
            return {"id": task_id}

    client = CancelClient()
    client.set_task(230, {"status": "doing", "assignedTo": {"account": "alice"}})
    monkeypatch.setattr(tms, "get_system_zentao_client", lambda db: client)
    monkeypatch.setattr(tms, "get_user_zentao_web_login", lambda uid, db: None)
    monkeypatch.setattr(tms, "get_system_zentao_web_login", lambda db: None)

    res = ZentaoTaskMirrorService(db_session).operate_task(task_id=230, action="cancel", current_user=alice)
    assert res["ok"] is True
    row = db_session.query(ZentaoTaskMirror).filter(ZentaoTaskMirror.task_id == 230).first()
    assert row.status == "cancel"


def test_assign_open_to_all_users(db_session, monkeypatch):
    # bob 不是指派人，也能把 alice 的任务指派给 carol（面向所有用户开放）
    alice = _user(db_session, "alice_as", account="alice")
    bob = _user(db_session, "bob_as", account="bob")
    _user(db_session, "carol_as", account="carol")
    _major(db_session, "V-TW-AS")
    _mirror(db_session, 240, alice.id, account="alice", status="wait")
    client = FakeClient()
    monkeypatch.setattr(tms, "get_system_zentao_client", lambda db: client)

    svc = ZentaoTaskMirrorService(db_session)
    res = svc.operate_task(task_id=240, action="assign", current_user=bob, assigned_to="carol")
    assert res["ok"] is True
    assert ("reassign", 240, "carol") in client.calls
    row = db_session.query(ZentaoTaskMirror).filter(ZentaoTaskMirror.task_id == 240).first()
    assert row.assigned_to == "carol"

    # 未提供目标人员 → 400
    with pytest.raises(HTTPException) as ei:
        svc.operate_task(task_id=240, action="assign", current_user=bob, assigned_to="  ")
    assert ei.value.status_code == 400


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


@pytest.mark.parametrize("initial_status", ["done", "closed"])
def test_reactivate_linked_completed_task_syncs_requirement_and_keeps_effort_ledger(
    db_session,
    monkeypatch,
    initial_status,
):
    """任务看板重新激活后，禅道/镜像/需求工作台共用状态和原工时账本。"""
    from datetime import datetime

    alice = _user(db_session, f"alice_reactivate_{initial_status}", account="alice")
    major = _major(db_session, f"V-REACT-{initial_status}")
    req = _req(
        db_session,
        major.id,
        f"r#react-{initial_status}",
        task_id=7771 if initial_status == "done" else 7772,
        owner_id=alice.id,
    )
    req.case_completed = True
    req.test_completed = True
    req.retest_completed = True
    req.retest_passed = True
    req.status = RequirementStatus.RETEST_DONE
    req.task_efforts_submitted = 2.5
    task_id = int(req.zentao_task_id)
    row = _mirror(db_session, task_id, alice.id, account="alice", status=initial_status)
    row.consumed = 2.5
    row.efforts_submitted = 2.5
    db_session.commit()

    now = datetime(2026, 7, 24, 10, 30)
    client = FakeClient()
    client.set_task(
        task_id,
        {
            "status": initial_status,
            "assignedTo": {"account": "alice"},
            "consumed": 2.5,
        },
    )
    events = []
    monkeypatch.setattr(tms, "get_system_zentao_client", lambda db: client)
    monkeypatch.setattr(tms, "local_now", lambda: now)
    monkeypatch.setattr(
        tms,
        "sse_publish",
        lambda event, payload, channels=None: events.append((event, payload, channels)),
    )

    result = ZentaoTaskMirrorService(db_session).operate_task(
        task_id=task_id,
        action="reactivate",
        current_user=alice,
    )

    assert result["ok"] is True
    assert any(call[0] == "restart" for call in client.calls)
    assert row.status == "doing"
    assert row.local_started_at == now
    assert row.consumed_accum == 0.0
    assert row.efforts_submitted == 2.5
    assert req.zentao_task_status_cache == "doing"
    assert req.task_started_at == now
    assert req.task_efforts_submitted == 2.5
    assert req.test_completed is False
    assert req.retest_completed is False
    assert req.retest_passed is None
    assert req.status == RequirementStatus.CASE_DONE
    task_event = next(payload for event, payload, _ in events if event == "zentao_task_changed")
    assert task_event["requirement_id"] == req.id
    reopen_event = next(payload for event, payload, _ in events if event == "retest_requirement_status_changed")
    assert reopen_event["test_completed"] is False


def test_operate_hours_settlement_excludes_pause(db_session, monkeypatch):
    """独立任务工时自动结算：暂停结算本段并停表，继续重新起算，
    完成上报「累计 + 最后一段」，暂停期不计入。"""
    from datetime import datetime

    alice = _user(db_session, "alice_wh", account="alice")
    _major(db_session, "V-TW-WH")
    row = _mirror(db_session, 901, alice.id, account="alice", status="doing")
    row.local_started_at = datetime(2026, 6, 29, 9, 0)   # 周一 9:00 开始（平台记录）
    db_session.commit()
    client = FakeClient()
    client.set_task(901, {"status": "doing", "assignedTo": {"account": "alice"}})
    monkeypatch.setattr(tms, "get_system_zentao_client", lambda db: client)
    monkeypatch.setattr(tms, "get_holiday_map", lambda db, a, b: {})
    svc = ZentaoTaskMirrorService(db_session)

    # 周一 11:00 暂停：结算 2h、停表
    monkeypatch.setattr(tms, "local_now", lambda: datetime(2026, 6, 29, 11, 0))
    r1 = svc.operate_task(task_id=901, action="pause", current_user=alice)
    assert r1["ok"] is True
    assert row.consumed_accum == 2.0
    assert row.local_started_at is None

    # 周三 9:00 继续：累计保留、重新起算
    monkeypatch.setattr(tms, "local_now", lambda: datetime(2026, 7, 1, 9, 0))
    r2 = svc.operate_task(task_id=901, action="start", current_user=alice)
    assert r2["ok"] is True
    assert row.consumed_accum == 2.0
    assert row.local_started_at == datetime(2026, 7, 1, 9, 0)

    # 周三 10:30 完成：currentConsumed = 2 + 1.5 = 3.5，暂停的一天半不计
    monkeypatch.setattr(tms, "local_now", lambda: datetime(2026, 7, 1, 10, 30))
    r3 = svc.operate_task(task_id=901, action="finish", current_user=alice)
    assert r3["ok"] is True
    finish = next(c for c in client.calls if c[0] == "finish")
    assert finish[2]["current_consumed"] == 3.5
    assert row.local_started_at is None


def test_operate_finish_auto_consumed_from_zentao_real_started(db_session, monkeypatch):
    """平台没记过开始（禅道网页上开始的任务）：完成时回退用禅道 real_started 起算。"""
    from datetime import datetime

    alice = _user(db_session, "alice_wh2", account="alice")
    _major(db_session, "V-TW-WH2")
    row = _mirror(db_session, 902, alice.id, account="alice", status="doing")
    row.real_started = datetime(2026, 7, 1, 9, 0)
    db_session.commit()
    client = FakeClient()
    client.set_task(902, {"status": "doing", "assignedTo": {"account": "alice"}})
    monkeypatch.setattr(tms, "get_system_zentao_client", lambda db: client)
    monkeypatch.setattr(tms, "get_holiday_map", lambda db, a, b: {})
    monkeypatch.setattr(tms, "local_now", lambda: datetime(2026, 7, 1, 11, 0))
    res = ZentaoTaskMirrorService(db_session).operate_task(task_id=902, action="finish", current_user=alice)
    assert res["ok"] is True
    finish = next(c for c in client.calls if c[0] == "finish")
    assert finish[2]["current_consumed"] == 2.0  # 9:00→11:00 工作时段内 2h


def test_operate_finish_short_timer_uses_minimum_not_estimate(db_session, monkeypatch):
    """开始后不足一分钟完成：使用禅道最小工时 0.1，不能回退预计/剩余工时。"""
    from datetime import datetime

    alice = _user(db_session, "alice_short_timer", account="alice")
    _major(db_session, "V-TW-SHORT")
    row = _mirror(db_session, 903, alice.id, account="alice", status="wait")
    row.left = 4.0
    row.estimate = 4.0
    db_session.commit()
    client = FakeClient()
    client.set_task(903, {"status": "wait", "assignedTo": {"account": "alice"}})
    monkeypatch.setattr(tms, "get_system_zentao_client", lambda db: client)
    monkeypatch.setattr(tms, "get_holiday_map", lambda db, a, b: {})

    monkeypatch.setattr(tms, "local_now", lambda: datetime(2026, 7, 20, 10, 0, 0))
    assert ZentaoTaskMirrorService(db_session).operate_task(
        task_id=903, action="start", current_user=alice
    )["ok"] is True
    monkeypatch.setattr(tms, "local_now", lambda: datetime(2026, 7, 20, 10, 0, 30))
    assert ZentaoTaskMirrorService(db_session).operate_task(
        task_id=903, action="finish", current_user=alice
    )["ok"] is True

    finish = next(call for call in client.calls if call[0] == "finish")
    assert finish[2]["current_consumed"] == 0.1


def test_operate_finish_without_timer_requires_explicit_consumed(db_session, monkeypatch):
    """完全没有计时依据时不再静默使用预计工时，必须由前端确认实际工时。"""
    alice = _user(db_session, "alice_manual_effort", account="alice")
    _major(db_session, "V-TW-MANUAL")
    row = _mirror(db_session, 904, alice.id, account="alice", status="doing")
    row.left = 4.0
    row.estimate = 4.0
    db_session.commit()
    client = FakeClient()
    client.set_task(904, {"status": "doing", "assignedTo": {"account": "alice"}})
    monkeypatch.setattr(tms, "get_system_zentao_client", lambda db: client)

    with pytest.raises(HTTPException) as exc:
        ZentaoTaskMirrorService(db_session).operate_task(
            task_id=904, action="finish", current_user=alice
        )
    assert exc.value.status_code == 400
    assert "实际工时" in exc.value.detail
    assert row.status == "doing"
    assert not any(call[0] == "finish" for call in client.calls)

    result = ZentaoTaskMirrorService(db_session).operate_task(
        task_id=904, action="finish", current_user=alice, consumed=0.75
    )
    assert result["ok"] is True
    finish = next(call for call in client.calls if call[0] == "finish")
    assert finish[2]["current_consumed"] == 0.75


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


def test_operate_falls_back_to_system_when_self_token_unauthorized(db_session, monkeypatch):
    # 本人 token 被当作 guest（pause 返回 401）→ 回退系统管理员账号完成暂停
    from app.services.zentao_client_service import ZentaoAPIError

    alice = _user(db_session, "alice_fb", account="alice")
    _major(db_session, "V-TW-FB")
    _mirror(db_session, 801, alice.id, account="alice", status="doing")

    class GuestClient(FakeClient):
        def pause_task(self, task_id, **kw):
            raise ZentaoAPIError(401, '{"error":"Unauthorized"}')

    good = FakeClient()
    good.set_task(801, {"status": "doing", "assignedTo": {"account": "alice"}})
    monkeypatch.setattr(tms, "get_user_zentao_client", lambda uid, db: GuestClient())
    monkeypatch.setattr(tms, "get_system_zentao_client", lambda db: good)

    res = ZentaoTaskMirrorService(db_session).operate_task(task_id=801, action="pause", current_user=alice)
    assert res["ok"] is True
    assert any(c[0] == "pause" for c in good.calls)
    row = db_session.query(ZentaoTaskMirror).filter(ZentaoTaskMirror.task_id == 801).first()
    assert row.status == "pause"


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


def test_operate_pause_rest_first_skips_web(db_session, monkeypatch):
    # REST pause 生效（pause_task 已固定带 comment 字段）→ 不再动用网页会话
    from app.services.zentao_web_session import ZentaoWebLogin

    alice = _user(db_session, "alice_web", account="alice")
    _major(db_session, "V-TW-W")
    _mirror(db_session, 901, alice.id, account="alice", status="doing")

    client = FakeClient()
    client.set_task(901, {"status": "doing", "assignedTo": {"account": "alice"}})
    web_calls = []

    def fake_web_pause(login, task_id, *, comment=None):
        web_calls.append((login.account, task_id, comment))
        return {"result": "success"}

    monkeypatch.setattr(tms, "get_system_zentao_client", lambda db: client)
    monkeypatch.setattr(tms, "get_user_zentao_web_login", lambda uid, db: None)
    monkeypatch.setattr(
        tms, "get_system_zentao_web_login",
        lambda db: ZentaoWebLogin(base_url="http://z", account="admin", password="p"),
    )
    monkeypatch.setattr(tms, "pause_task_via_web", fake_web_pause)

    res = ZentaoTaskMirrorService(db_session).operate_task(task_id=901, action="pause", current_user=alice)
    assert res["ok"] is True
    assert any(c[0] == "pause" for c in client.calls)  # 走了 REST
    assert web_calls == []  # 网页会话未被动用
    row = db_session.query(ZentaoTaskMirror).filter(ZentaoTaskMirror.task_id == 901).first()
    assert row.status == "pause"


def test_operate_pause_falls_back_to_web_when_rest_noop(db_session, monkeypatch):
    # REST 200 但未生效（如 token 被当 guest）→ 回退网页 cookie 会话完成暂停
    from app.services.zentao_web_session import ZentaoWebLogin

    alice = _user(db_session, "alice_webfb", account="alice")
    _major(db_session, "V-TW-WF")
    _mirror(db_session, 902, alice.id, account="alice", status="doing")

    class NoopPauseClient(FakeClient):
        def pause_task(self, task_id, **kw):
            self.calls.append(("pause", task_id, kw))
            self._tasks[task_id] = {"status": "doing", "assignedTo": {"account": "alice"}}
            return {"message": "success"}

    client = NoopPauseClient()
    client.set_task(902, {"status": "doing", "assignedTo": {"account": "alice"}})
    web_calls = []

    def fake_web_pause(login, task_id, *, comment=None):
        web_calls.append((login.account, task_id))
        client.set_task(task_id, {"status": "pause", "assignedTo": {"account": "alice"}})
        return {"result": "success"}

    monkeypatch.setattr(tms, "get_system_zentao_client", lambda db: client)
    monkeypatch.setattr(tms, "get_user_zentao_web_login", lambda uid, db: None)
    monkeypatch.setattr(
        tms, "get_system_zentao_web_login",
        lambda db: ZentaoWebLogin(base_url="http://z", account="admin", password="p"),
    )
    monkeypatch.setattr(tms, "pause_task_via_web", fake_web_pause)

    res = ZentaoTaskMirrorService(db_session).operate_task(task_id=902, action="pause", current_user=alice)
    assert res["ok"] is True
    assert web_calls == [("admin", 902)]
    row = db_session.query(ZentaoTaskMirror).filter(ZentaoTaskMirror.task_id == 902).first()
    assert row.status == "pause"


def test_pause_and_close_always_send_nonempty_body(monkeypatch):
    # ipd4.3 空 body 的任务动作会被静默忽略 → pause/close 必须始终带 comment 字段
    import httpx as _httpx
    from app.services.zentao_client_service import ZentaoClient

    sent = []

    def fake_post(url, json=None, headers=None, timeout=None):
        sent.append((url, json))
        return _httpx.Response(200, json={"id": 1, "status": "x"}, request=_httpx.Request("POST", url))

    monkeypatch.setattr(_httpx, "post", fake_post)
    cli = ZentaoClient(base_url="http://z/zentao", token="t")
    cli.pause_task(1)
    cli.close_task(1)
    cli.pause_task(1, comment="备注")
    assert sent[0][1] == {"comment": ""}
    assert sent[1][1] == {"comment": ""}
    assert sent[2][1] == {"comment": "备注"}


def test_ensure_test_prefix_variants():
    from app.utils.task_naming import ensure_test_prefix

    assert ensure_test_prefix("RTK 打点验证") == "[测试]RTK 打点验证"
    assert ensure_test_prefix("[测试]RTK 打点验证") == "[测试]RTK 打点验证"  # 不重复加
    assert ensure_test_prefix("【测试】RTK 打点验证") == "[测试]RTK 打点验证"  # 全角变体归一
    assert ensure_test_prefix("  [测试] 带空格  ") == "[测试]带空格"


def test_create_board_task_adds_test_prefix(db_session, monkeypatch):
    alice = _user(db_session, "alice_pfx", account="alice")
    major = _major(db_session, "V-TW-PFX")

    class CreateClient(FakeClient):
        def __init__(self):
            super().__init__()
            self.created = []

        def create_execution_task(self, exec_id, **kw):
            self.created.append(kw)
            return {"id": 7777}

        def list_execution_tasks(self, exec_id, limit=500):
            return []

    client = CreateClient()
    monkeypatch.setattr(tms, "get_user_zentao_client", lambda uid, db: None)
    monkeypatch.setattr(tms, "get_system_zentao_client", lambda db: client)

    res = ZentaoTaskMirrorService(db_session).create_board_task(
        current_user=alice, major_version_id=major.id, name="外业回归验证",
    )
    assert res["ok"] is True
    assert client.created[0]["name"] == "[测试]外业回归验证"

    # 用户自己带了前缀 → 不重复
    ZentaoTaskMirrorService(db_session).create_board_task(
        current_user=alice, major_version_id=major.id, name="[测试]外业回归验证2",
    )
    assert client.created[1]["name"] == "[测试]外业回归验证2"


def test_operate_task_set_time_validates(db_session, monkeypatch):
    alice = _user(db_session, "alice_tw5", account="alice")
    major = _major(db_session, "V-TW-5")
    _mirror(db_session, 401, alice.id, account="alice")
    monkeypatch.setattr(tms, "get_system_zentao_client", lambda db: FakeClient())
    svc = ZentaoTaskMirrorService(db_session)
    with pytest.raises(HTTPException) as ei:
        svc.operate_task(task_id=401, action="set_time", current_user=alice, hours=0)
    assert ei.value.status_code == 400


# ─── 分段提交禅道工时记录（2026-07-17 工时口径升级）──────────────────────────


def _fake_login():
    from app.services.zentao_web_session import ZentaoWebLogin
    return ZentaoWebLogin(base_url="http://z", account="alice", password="p")


def test_pause_submits_day_split_efforts(db_session, monkeypatch):
    """有本人网页凭据：暂停把本段按天拆分提交为工时记录，本地累计保持 0。"""
    from datetime import date, datetime

    alice = _user(db_session, "alice_eff", account="alice")
    _major(db_session, "V-TW-EFF")
    row = _mirror(db_session, 1001, alice.id, account="alice", status="doing")
    row.local_started_at = datetime(2026, 6, 29, 15, 0)  # 周一 15:00 开始
    row.left = 20.0
    db_session.commit()
    client = FakeClient()
    client.set_task(1001, {"status": "doing", "assignedTo": {"account": "alice"}})
    monkeypatch.setattr(tms, "get_system_zentao_client", lambda db: client)
    monkeypatch.setattr(tms, "get_holiday_map", lambda db, a, b: {})
    monkeypatch.setattr(tms, "get_user_zentao_web_login", lambda uid, db: _fake_login())
    submitted = []

    def fake_submit(login, task_id, day_rows, *, left_before, note=""):
        submitted.append((login.account, task_id, day_rows, left_before))
        return round(sum(h for _, h in day_rows), 2)

    monkeypatch.setattr(tms, "submit_day_efforts", fake_submit)

    # 周三 10:30 暂停：周一 9h + 周二 24h + 周三 10.5h 三行记录
    # （2026-07-17 起取消工作时段窗口：工作日内自然时间全计、周末节假日跳过）
    monkeypatch.setattr(tms, "local_now", lambda: datetime(2026, 7, 1, 10, 30))
    res = ZentaoTaskMirrorService(db_session).operate_task(task_id=1001, action="pause", current_user=alice)
    assert res["ok"] is True
    assert submitted == [("alice", 1001, [
        (date(2026, 6, 29), 9.0),
        (date(2026, 6, 30), 24.0),
        (date(2026, 7, 1), 10.5),
    ], 20.0)]
    assert row.consumed_accum == 0.0
    assert row.efforts_submitted == 43.5
    assert row.local_started_at is None


def test_pause_merges_legacy_accum_into_today(db_session, monkeypatch):
    """存量任务兼容：旧口径遗留的本地累计随首次暂停补录到今天。"""
    from datetime import date, datetime

    alice = _user(db_session, "alice_leg", account="alice")
    _major(db_session, "V-TW-LEG")
    row = _mirror(db_session, 1002, alice.id, account="alice", status="doing")
    row.local_started_at = datetime(2026, 7, 1, 9, 0)
    row.consumed_accum = 2.0  # 旧口径遗留
    db_session.commit()
    client = FakeClient()
    client.set_task(1002, {"status": "doing", "assignedTo": {"account": "alice"}})
    monkeypatch.setattr(tms, "get_system_zentao_client", lambda db: client)
    monkeypatch.setattr(tms, "get_holiday_map", lambda db, a, b: {})
    monkeypatch.setattr(tms, "get_user_zentao_web_login", lambda uid, db: _fake_login())
    submitted = []

    def fake_submit(login, task_id, day_rows, *, left_before, note=""):
        submitted.append(day_rows)
        return round(sum(h for _, h in day_rows), 2)

    monkeypatch.setattr(tms, "submit_day_efforts", fake_submit)
    monkeypatch.setattr(tms, "local_now", lambda: datetime(2026, 7, 1, 11, 0))
    res = ZentaoTaskMirrorService(db_session).operate_task(task_id=1002, action="pause", current_user=alice)
    assert res["ok"] is True
    # 本段 2h + 遗留 2h 合并到今天一行
    assert submitted == [[(date(2026, 7, 1), 4.0)]]
    assert row.consumed_accum == 0.0
    assert row.efforts_submitted == 4.0


def test_pause_submit_failure_falls_back_to_local_accum(db_session, monkeypatch):
    """提交失败：退回旧口径本地累计（完成时一次性提交），暂停本身不受影响。"""
    from datetime import datetime

    alice = _user(db_session, "alice_fbk", account="alice")
    _major(db_session, "V-TW-FBK")
    row = _mirror(db_session, 1003, alice.id, account="alice", status="doing")
    row.local_started_at = datetime(2026, 6, 29, 9, 0)
    db_session.commit()
    client = FakeClient()
    client.set_task(1003, {"status": "doing", "assignedTo": {"account": "alice"}})
    monkeypatch.setattr(tms, "get_system_zentao_client", lambda db: client)
    monkeypatch.setattr(tms, "get_holiday_map", lambda db, a, b: {})
    monkeypatch.setattr(tms, "get_user_zentao_web_login", lambda uid, db: _fake_login())

    def boom(*a, **kw):
        raise RuntimeError("web down")

    monkeypatch.setattr(tms, "submit_day_efforts", boom)
    monkeypatch.setattr(tms, "local_now", lambda: datetime(2026, 6, 29, 11, 0))
    res = ZentaoTaskMirrorService(db_session).operate_task(task_id=1003, action="pause", current_user=alice)
    assert res["ok"] is True
    assert row.consumed_accum == 2.0
    assert row.efforts_submitted == 0.0
    assert row.local_started_at is None


def test_finish_presubmits_prev_days_and_reports_today_only(db_session, monkeypatch):
    """完成：今天之前的段先按天提交为工时记录，finish 只带今天的部分。"""
    from datetime import date, datetime

    alice = _user(db_session, "alice_fin", account="alice")
    _major(db_session, "V-TW-FIN")
    row = _mirror(db_session, 1004, alice.id, account="alice", status="doing")
    row.local_started_at = datetime(2026, 6, 29, 15, 0)  # 周一 15:00
    db_session.commit()
    client = FakeClient()
    client.set_task(1004, {"status": "doing", "assignedTo": {"account": "alice"}})
    monkeypatch.setattr(tms, "get_system_zentao_client", lambda db: client)
    monkeypatch.setattr(tms, "get_holiday_map", lambda db, a, b: {})
    monkeypatch.setattr(tms, "get_user_zentao_web_login", lambda uid, db: _fake_login())
    submitted = []

    def fake_submit(login, task_id, day_rows, *, left_before, note=""):
        submitted.append(day_rows)
        return round(sum(h for _, h in day_rows), 2)

    monkeypatch.setattr(tms, "submit_day_efforts", fake_submit)
    # 周三 10:30 完成：周一 9h + 周二 24h 预提交，finish 只带周三的 10.5h
    monkeypatch.setattr(tms, "local_now", lambda: datetime(2026, 7, 1, 10, 30))
    res = ZentaoTaskMirrorService(db_session).operate_task(task_id=1004, action="finish", current_user=alice)
    assert res["ok"] is True
    assert submitted == [[(date(2026, 6, 29), 9.0), (date(2026, 6, 30), 24.0)]]
    finish = next(c for c in client.calls if c[0] == "finish")
    assert finish[2]["current_consumed"] == 10.5
    assert row.consumed_accum == 0.0
    assert row.efforts_submitted == round(33.0 + 10.5, 2)
    assert row.local_started_at is None


def test_finish_after_segments_submitted_uses_min_floor(db_session, monkeypatch):
    """暂停时已分段提交过 → 暂停中直接完成不再拿剩余/预计工时兜底，给最小值 0.1。"""
    from datetime import datetime

    alice = _user(db_session, "alice_min", account="alice")
    _major(db_session, "V-TW-MIN")
    row = _mirror(db_session, 1005, alice.id, account="alice", status="pause")
    row.left = 8.0
    row.estimate = 8.0
    row.real_started = datetime(2026, 6, 29, 9, 0)
    row.consumed_accum = 0.0
    row.efforts_submitted = 2.0  # 暂停时已提交过
    db_session.commit()
    client = FakeClient()
    client.set_task(1005, {"status": "pause", "assignedTo": {"account": "alice"}})
    monkeypatch.setattr(tms, "get_system_zentao_client", lambda db: client)
    monkeypatch.setattr(tms, "get_holiday_map", lambda db, a, b: {})
    monkeypatch.setattr(tms, "local_now", lambda: datetime(2026, 7, 1, 15, 0))
    res = ZentaoTaskMirrorService(db_session).operate_task(task_id=1005, action="finish", current_user=alice)
    assert res["ok"] is True
    finish = next(c for c in client.calls if c[0] == "finish")
    # 不回退 real_started 起算（会重复计入），也不拿 left/estimate 兜底
    assert finish[2]["current_consumed"] == 0.1


# ─── 操作人归属：本人 REST 失效 → 本人网页会话兜底（2026-07-17）──────────────


def test_operate_start_falls_back_to_self_web_not_system(db_session, monkeypatch):
    """本人 token 被当 guest（200 无效果）→ 先用本人网页会话开始，不落系统账号。"""
    from app.services.zentao_web_session import ZentaoWebLogin

    alice = _user(db_session, "alice_selfweb", account="alice")
    _major(db_session, "V-TW-SW")
    _mirror(db_session, 1101, alice.id, account="alice", status="wait")

    class GuestStartClient(FakeClient):
        def start_task(self, task_id, **kw):
            self.calls.append(("start", task_id, kw))
            # 200 但状态不变（guest 静默失败）
            return {"message": "success"}

    guest = GuestStartClient()
    guest.set_task(1101, {"status": "wait", "assignedTo": {"account": "alice"}})
    system = FakeClient()
    system.set_task(1101, {"status": "wait", "assignedTo": {"account": "alice"}})
    web_calls = []

    def fake_web_start(login, task_id, *, left, real_started=None, comment=None):
        web_calls.append((login.account, task_id, left))
        guest.set_task(task_id, {"status": "doing", "assignedTo": {"account": "alice"}})
        return {"result": "success"}

    monkeypatch.setattr(tms, "get_user_zentao_client", lambda uid, db: guest)
    monkeypatch.setattr(tms, "get_system_zentao_client", lambda db: system)
    monkeypatch.setattr(
        tms, "get_user_zentao_web_login",
        lambda uid, db: ZentaoWebLogin(base_url="http://z", account="alice", password="p"),
    )
    monkeypatch.setattr(tms, "get_system_zentao_web_login", lambda db: None)
    monkeypatch.setattr(tms, "start_task_via_web", fake_web_start)

    res = ZentaoTaskMirrorService(db_session).operate_task(task_id=1101, action="start", current_user=alice)
    assert res["ok"] is True
    assert web_calls == [("alice", 1101, 1.0)]   # 本人网页会话完成了开始
    assert system.calls == []                     # 系统账号完全没出场
    row = db_session.query(ZentaoTaskMirror).filter(ZentaoTaskMirror.task_id == 1101).first()
    assert row.status == "doing"


def test_operate_finish_falls_back_to_self_web(db_session, monkeypatch):
    """完成同理：本人 REST 未生效 → 本人网页会话 finish，操作人保持本人。"""
    from datetime import datetime
    from app.services.zentao_web_session import ZentaoWebLogin

    alice = _user(db_session, "alice_finweb", account="alice")
    _major(db_session, "V-TW-FW")
    row = _mirror(db_session, 1102, alice.id, account="alice", status="doing")
    row.local_started_at = datetime(2026, 7, 1, 9, 0)
    db_session.commit()

    class GuestFinishClient(FakeClient):
        def finish_task(self, task_id, **kw):
            self.calls.append(("finish", task_id, kw))
            return {"message": "success"}  # 200 但状态不变

    guest = GuestFinishClient()
    guest.set_task(1102, {"status": "doing", "assignedTo": {"account": "alice"}})
    system = FakeClient()
    web_calls = []

    def fake_web_finish(login, task_id, *, current_consumed, finished_date=None, comment=None):
        web_calls.append((login.account, task_id, current_consumed))
        guest.set_task(task_id, {"status": "done", "assignedTo": {"account": "alice"}})
        return {"result": "success"}

    monkeypatch.setattr(tms, "get_user_zentao_client", lambda uid, db: guest)
    monkeypatch.setattr(tms, "get_system_zentao_client", lambda db: system)
    monkeypatch.setattr(tms, "get_holiday_map", lambda db, a, b: {})
    monkeypatch.setattr(
        tms, "get_user_zentao_web_login",
        lambda uid, db: ZentaoWebLogin(base_url="http://z", account="alice", password="p"),
    )
    monkeypatch.setattr(tms, "get_system_zentao_web_login", lambda db: None)
    monkeypatch.setattr(tms, "finish_task_via_web", fake_web_finish)
    monkeypatch.setattr(tms, "local_now", lambda: datetime(2026, 7, 1, 11, 0))

    res = ZentaoTaskMirrorService(db_session).operate_task(task_id=1102, action="finish", current_user=alice)
    assert res["ok"] is True
    assert web_calls == [("alice", 1102, 2.0)]  # 工时随网页 finish 上报
    assert system.calls == []


# ─── 暂停不被自动激活：工时必须在暂停之前提交（2026-07-17 线上问题）──────────


def test_operate_finish_does_not_repeat_when_rest_confirms_done_but_readback_is_stale(db_session, monkeypatch):
    """REST 已确认完成时，旧状态回读不能再触发相同工时的网页 finish。"""
    from datetime import datetime
    from app.services.zentao_web_session import ZentaoWebLogin

    alice = _user(db_session, "alice_finish_once", account="alice")
    _major(db_session, "V-TW-ONCE")
    row = _mirror(db_session, 1103, alice.id, account="alice", status="doing")
    row.local_started_at = datetime(2026, 7, 20, 9, 0)
    db_session.commit()

    class StaleReadbackFinishClient(FakeClient):
        def finish_task(self, task_id, **kw):
            self.calls.append(("finish", task_id, kw))
            return {"id": task_id, "status": "done"}  # 写响应成功，紧随其后的 GET 仍是 doing

    client = StaleReadbackFinishClient()
    client.set_task(1103, {"status": "doing", "assignedTo": {"account": "alice"}})
    system = FakeClient()
    web_calls = []
    monkeypatch.setattr(tms, "get_user_zentao_client", lambda uid, db: client)
    monkeypatch.setattr(tms, "get_system_zentao_client", lambda db: system)
    monkeypatch.setattr(tms, "get_holiday_map", lambda db, a, b: {})
    monkeypatch.setattr(
        tms,
        "get_user_zentao_web_login",
        lambda uid, db: ZentaoWebLogin(base_url="http://z", account="alice", password="p"),
    )
    monkeypatch.setattr(tms, "get_system_zentao_web_login", lambda db: None)
    monkeypatch.setattr(
        tms,
        "finish_task_via_web",
        lambda *args, **kwargs: web_calls.append((args, kwargs)),
    )
    monkeypatch.setattr(tms, "local_now", lambda: datetime(2026, 7, 20, 11, 0))

    res = ZentaoTaskMirrorService(db_session).operate_task(task_id=1103, action="finish", current_user=alice)

    assert res["ok"] is True
    assert client.calls == [
        ("finish", 1103, {"current_consumed": 2.0, "finished_date": "2026-07-20 11:00:00"})
    ]
    assert web_calls == []
    assert system.calls == []
    assert row.status == "done"
    assert row.efforts_submitted == 2.0


def test_operate_task_publishes_task_board_refresh_event(db_session, monkeypatch):
    """任务工作台操作提交镜像后通知已打开的任务看板刷新。"""
    alice = _user(db_session, "alice_task_event", account="alice")
    _major(db_session, "V-TW-EVENT")
    row = _mirror(db_session, 1104, alice.id, account="alice", status="wait")
    client = FakeClient()
    client.set_task(1104, {"status": "wait", "assignedTo": {"account": "alice"}})
    events = []
    monkeypatch.setattr(tms, "get_user_zentao_client", lambda uid, db: client)
    monkeypatch.setattr(tms, "get_system_zentao_client", lambda db: None)
    monkeypatch.setattr(tms, "get_user_zentao_web_login", lambda uid, db: None)
    monkeypatch.setattr(tms, "get_system_zentao_web_login", lambda db: None)
    monkeypatch.setattr(
        tms,
        "sse_publish",
        lambda event, payload, channels=None: events.append((event, payload, channels)),
    )

    res = ZentaoTaskMirrorService(db_session).operate_task(task_id=1104, action="start", current_user=alice)

    assert res["ok"] is True
    assert row.status == "doing"
    assert len(events) == 1
    event, payload, channels = events[0]
    assert event == "zentao_task_changed"
    assert payload["task_id"] == 1104
    assert payload["status"] == "doing"
    assert payload["action"] == "start"
    assert payload["source"] == "task_workbench"
    assert payload["task"]["status"] == "doing"
    assert channels == ["global"]


@pytest.mark.parametrize(
    ("action", "local_status", "remote_status"),
    [("start", "wait", "pause"), ("pause", "doing", "wait")],
)
def test_failed_start_or_pause_does_not_change_local_state(
    db_session, monkeypatch, action, local_status, remote_status
):
    """禅道未切到目标状态时，回读只用于校验，不能反向改写本地镜像。"""
    from datetime import datetime

    alice = _user(db_session, f"alice_failed_{action}", account="alice")
    _major(db_session, f"V-TW-FAILED-{action}")
    task_id = 1190 if action == "start" else 1191
    row = _mirror(db_session, task_id, alice.id, account="alice", status=local_status)
    before_started = datetime(2026, 7, 1, 9, 0)
    row.local_started_at = before_started
    row.consumed_accum = 5.0
    db_session.commit()

    class IneffectiveClient(FakeClient):
        def start_task(self, task_id, **kw):
            self.calls.append(("start", task_id, kw))
            return {"message": "success"}

        def pause_task(self, task_id, **kw):
            self.calls.append(("pause", task_id, kw))
            return {"message": "success"}

    client = IneffectiveClient()
    client.set_task(task_id, {"status": remote_status, "assignedTo": {"account": "alice"}})
    monkeypatch.setattr(tms, "get_user_zentao_client", lambda uid, db: client)
    monkeypatch.setattr(tms, "get_system_zentao_client", lambda db: None)
    monkeypatch.setattr(tms, "get_user_zentao_web_login", lambda uid, db: None)
    monkeypatch.setattr(tms, "get_system_zentao_web_login", lambda db: None)

    res = ZentaoTaskMirrorService(db_session).operate_task(
        task_id=task_id, action=action, current_user=alice
    )

    assert res["ok"] is False
    assert row.status == local_status
    assert row.local_started_at == before_started
    assert row.consumed_accum == 5.0
    assert res["task"]["status"] == local_status


def test_pause_submits_efforts_before_pausing(db_session, monkeypatch):
    """禅道对 pause 任务记工时会自动激活回 doing → 分段提交必须发生在暂停之前。"""
    from datetime import datetime

    alice = _user(db_session, "alice_order", account="alice")
    _major(db_session, "V-TW-ORD")
    row = _mirror(db_session, 1201, alice.id, account="alice", status="doing")
    row.local_started_at = datetime(2026, 7, 1, 9, 0)
    db_session.commit()
    seq = []

    class SeqClient(FakeClient):
        def pause_task(self, task_id, **kw):
            seq.append("pause")
            return super().pause_task(task_id, **kw)

    client = SeqClient()
    client.set_task(1201, {"status": "doing", "assignedTo": {"account": "alice"}})

    def fake_submit(login, task_id, day_rows, *, left_before, note=""):
        seq.append("submit")
        return round(sum(h for _, h in day_rows), 2)

    monkeypatch.setattr(tms, "get_system_zentao_client", lambda db: client)
    monkeypatch.setattr(tms, "get_holiday_map", lambda db, a, b: {})
    monkeypatch.setattr(tms, "get_user_zentao_web_login", lambda uid, db: _fake_login())
    monkeypatch.setattr(tms, "submit_day_efforts", fake_submit)
    monkeypatch.setattr(tms, "local_now", lambda: datetime(2026, 7, 1, 11, 0))

    res = ZentaoTaskMirrorService(db_session).operate_task(task_id=1201, action="pause", current_user=alice)
    assert res["ok"] is True
    assert seq == ["submit", "pause"]   # 先记工时（doing 态，不改状态），后暂停
    assert row.local_started_at is None
    assert row.consumed_accum == 0.0
    assert row.efforts_submitted == 2.0


def test_pause_presubmit_keeps_clock_when_pause_ineffective(db_session, monkeypatch):
    """工时已提交但暂停未生效：计时起点重置到暂停时刻续跑，已提交段不会重复计入。"""
    from datetime import datetime

    alice = _user(db_session, "alice_ord2", account="alice")
    _major(db_session, "V-TW-ORD2")
    row = _mirror(db_session, 1202, alice.id, account="alice", status="doing")
    row.local_started_at = datetime(2026, 7, 1, 9, 0)
    db_session.commit()

    class NoopPauseClient(FakeClient):
        def pause_task(self, task_id, **kw):
            self.calls.append(("pause", task_id, kw))
            return {"message": "success"}  # 200 但状态不变

    client = NoopPauseClient()
    client.set_task(1202, {"status": "doing", "assignedTo": {"account": "alice"}})
    monkeypatch.setattr(tms, "get_system_zentao_client", lambda db: client)
    monkeypatch.setattr(tms, "get_holiday_map", lambda db, a, b: {})
    monkeypatch.setattr(tms, "get_user_zentao_web_login", lambda uid, db: _fake_login())
    monkeypatch.setattr(tms, "get_system_zentao_web_login", lambda db: None)
    monkeypatch.setattr(tms, "pause_task_via_web", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("web down")))
    monkeypatch.setattr(tms, "submit_day_efforts",
                        lambda login, task_id, day_rows, **kw: round(sum(h for _, h in day_rows), 2))
    monkeypatch.setattr(tms, "local_now", lambda: datetime(2026, 7, 1, 11, 0))

    res = ZentaoTaskMirrorService(db_session).operate_task(task_id=1202, action="pause", current_user=alice)
    assert res["ok"] is False                                # 暂停未生效如实上报
    assert row.efforts_submitted == 2.0                      # 工时已落禅道
    assert row.consumed_accum == 0.0
    assert row.local_started_at == datetime(2026, 7, 1, 11, 0)  # 新段起点=提交时刻，续跑不重复


def test_stale_board_pause_is_idempotent_and_syncs_linked_requirement(db_session, monkeypatch):
    """If Zentao is already paused, a stale board button must not add effort or pause again."""
    from datetime import datetime

    alice = _user(db_session, "alice_pause_idem", account="alice")
    major = _major(db_session, "V-TW-IDEM")
    req = _req(db_session, major.id, "r#idem", task_id=1203, owner_id=alice.id)
    row = _mirror(db_session, 1203, alice.id, account="alice", status="doing")
    req.zentao_task_status_cache = "doing"
    req.task_started_at = datetime(2026, 7, 21, 11, 0)
    req.task_efforts_submitted = 2.0
    row.real_started = datetime(2026, 7, 21, 9, 0)
    db_session.commit()

    client = FakeClient()
    client.set_task(1203, {"status": "pause", "assignedTo": {"account": "alice"}})
    monkeypatch.setattr(tms, "get_user_zentao_client", lambda uid, db: client)
    monkeypatch.setattr(tms, "get_system_zentao_client", lambda db: client)
    monkeypatch.setattr(tms, "local_now", lambda: datetime(2026, 7, 21, 11, 0))
    monkeypatch.setattr(
        tms,
        "submit_day_efforts",
        lambda *args, **kwargs: pytest.fail("idempotent pause must not submit effort"),
    )

    result = ZentaoTaskMirrorService(db_session).operate_task(
        task_id=1203, action="pause", current_user=alice
    )

    assert result["ok"] is True
    assert result["idempotent"] is True
    assert not any(call[0] == "pause" for call in client.calls)
    assert row.status == "pause"
    assert row.efforts_submitted == 2.0
    assert row.local_started_at is None
    assert req.zentao_task_status_cache == "pause"
    assert req.task_efforts_submitted == 2.0
    assert req.task_started_at is None


# ─── 完成时间兜底：禅道 left=0 自动完成不写 finishedDate（2026-07-17）────────


def test_finish_backfills_finished_date_when_zentao_omits_it(db_session, monkeypatch):
    """完成生效但禅道回读没有 finishedDate → 镜像用操作时刻兜底（看板延期归列依赖它）。"""
    from datetime import datetime

    alice = _user(db_session, "alice_fd", account="alice")
    _major(db_session, "V-TW-FD")
    row = _mirror(db_session, 1301, alice.id, account="alice", status="doing")
    db_session.commit()

    class NoFinishedDateClient(FakeClient):
        def finish_task(self, task_id, **kw):
            self.calls.append(("finish", task_id, kw))
            # 禅道置为 done 但 finishedDate 缺失（left=0 自动完成的同款数据形态）
            self._tasks[task_id] = {"status": "done", "assignedTo": {"account": "alice"}, "finishedDate": None}
            return {"id": task_id}

    client = NoFinishedDateClient()
    client.set_task(1301, {"status": "doing", "assignedTo": {"account": "alice"}})
    monkeypatch.setattr(tms, "get_system_zentao_client", lambda db: client)
    monkeypatch.setattr(tms, "get_holiday_map", lambda db, a, b: {})
    monkeypatch.setattr(tms, "local_now", lambda: datetime(2026, 7, 17, 18, 0))
    res = ZentaoTaskMirrorService(db_session).operate_task(
        task_id=1301, action="finish", current_user=alice, consumed=0.5
    )
    assert res["ok"] is True
    assert row.finished_date == datetime(2026, 7, 17, 18, 0)


def test_sync_keeps_finished_date_when_zentao_returns_empty(db_session, monkeypatch):
    """后台同步：完成态任务 finishedDate 为空时不得冲掉镜像里已兜底的完成时间。"""
    from datetime import datetime

    alice = _user(db_session, "alice_keepfd", account="alice")
    major = _major(db_session, "V-TW-KFD")
    row = _mirror(db_session, 1302, alice.id, account="alice", status="done")
    row.finished_date = datetime(2026, 7, 17, 18, 0)
    db_session.commit()

    class ListClient:
        def __init__(self, tasks):
            self.tasks = tasks

        def list_execution_tasks(self, exec_id, limit=500):
            return self.tasks

    svc = ZentaoTaskMirrorService(db_session)
    base_task = {
        "id": 1302, "name": "任务1302", "status": "done", "parent": 0, "isParent": 0,
        "assignedTo": {"account": "alice"},
    }
    n = svc._sync_execution(ListClient([{**base_task, "finishedDate": "0000-00-00 00:00:00"}]), major, {}, {})
    assert n == 1
    assert row.finished_date == datetime(2026, 7, 17, 18, 0)   # 空值没有冲掉兜底

    # 禅道后来补上了真实完成时间 → 正常覆盖（本地化小时数依机器时区，不作精确断言）
    svc._sync_execution(ListClient([{**base_task, "finishedDate": "2026-07-17T10:12:31Z"}]), major, {}, {})
    assert row.finished_date is not None
    assert row.finished_date != datetime(2026, 7, 17, 18, 0)
    assert row.finished_date.second == 31
