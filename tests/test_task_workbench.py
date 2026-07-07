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


def test_operate_task_set_time_validates(db_session, monkeypatch):
    alice = _user(db_session, "alice_tw5", account="alice")
    major = _major(db_session, "V-TW-5")
    _mirror(db_session, 401, alice.id, account="alice")
    monkeypatch.setattr(tms, "get_system_zentao_client", lambda db: FakeClient())
    svc = ZentaoTaskMirrorService(db_session)
    with pytest.raises(HTTPException) as ei:
        svc.operate_task(task_id=401, action="set_time", current_user=alice, hours=0)
    assert ei.value.status_code == 400
