"""能力 C：开始/完成/重新激活 + 工时回算（用假客户端，无网络）。"""
from __future__ import annotations

from datetime import datetime

import pytest
from fastapi import HTTPException

from app.models import Requirement, Version, VersionType
import app.services.zentao_task_sync_service as tss
from app.services.zentao_task_sync_service import ZentaoTaskSyncService


class FakeClient:
    """有状态假客户端：操作生效后 get_task 回读到新状态（回读校验依赖这一点）。

    effective=False 模拟禅道 ipd4.3 的 guest 静默失败：REST 返回 200 但状态不变。
    """

    def __init__(self, *, effective=True, status="wait"):
        self.calls = []
        self.effective = effective
        self._task = {"id": 777, "consumed": 2.0, "status": status}

    def _apply(self, status):
        if self.effective:
            self._task["status"] = status

    def start_task(self, task_id, *, real_started=None, left=None, assigned_to=None):
        self.calls.append(("start", task_id, real_started, left))
        self._apply("doing")
        return {"id": task_id, "status": self._task["status"]}

    def finish_task(self, task_id, *, current_consumed, finished_date=None, assigned_to=None):
        self.calls.append(("finish", task_id, current_consumed, finished_date))
        self._apply("done")
        return {"id": task_id, "status": self._task["status"]}

    def restart_task(self, task_id, *, consumed, left, assigned_to=None):
        self.calls.append(("restart", task_id, consumed, left))
        self._apply("doing")
        return {"id": task_id, "status": self._task["status"]}

    def pause_task(self, task_id, *, comment=None):
        self.calls.append(("pause", task_id))
        self._apply("pause")
        return {"id": task_id, "status": self._task["status"]}

    def reassign_task(self, task_id, assigned_to):
        self.calls.append(("reassign", task_id, assigned_to))
        return {"id": task_id}

    def get_task(self, task_id):
        return self._task


@pytest.fixture()
def req(db_session, monkeypatch):
    monkeypatch.setattr(tss, "get_holiday_map", lambda db, a, b: {})
    major = Version(version_no="V1", version_type=VersionType.MAJOR, zentao_execution_id=1)
    db_session.add(major)
    db_session.flush()
    r = Requirement(zentao_req_id="S1", title="需求", major_version_id=major.id,
                    estimated_test_hours=4.0, zentao_task_id=777)
    db_session.add(r)
    db_session.commit()
    return r


def test_start_records_time_and_calls_zentao(db_session, monkeypatch, req):
    client = FakeClient()
    monkeypatch.setattr(tss, "get_system_zentao_client", lambda db: client)
    svc = ZentaoTaskSyncService(db_session)
    res = svc.start_requirement_task(req, hours=4.0)
    assert res["ok"] is True
    assert req.task_started_at is not None
    assert req.zentao_task_status_cache == "doing"
    assert any(c[0] == "start" for c in client.calls)


def test_requirement_actions_update_task_board_mirror_and_publish_events(db_session, monkeypatch, req):
    """需求工作台操作成功后立即更新任务看板镜像，不等待后台定时同步。"""
    from app.models.zentao_task_mirror import ZentaoTaskMirror

    mirror = ZentaoTaskMirror(
        task_id=777,
        execution_id=1,
        execution_name_cache="V1",
        name="需求测试任务",
        status="wait",
    )
    db_session.add(mirror)
    db_session.commit()

    client = FakeClient(status="wait")
    events = []
    monkeypatch.setattr(tss, "get_system_zentao_client", lambda db: client)
    monkeypatch.setattr(tss, "get_system_zentao_web_login", lambda db: None)
    monkeypatch.setattr(
        tss,
        "sse_publish",
        lambda event, payload, channels=None: events.append((event, payload, channels)),
    )
    svc = ZentaoTaskSyncService(db_session)

    monkeypatch.setattr(tss, "local_now", lambda: datetime(2026, 7, 20, 9, 0))
    assert svc.start_requirement_task(req, hours=4.0)["ok"] is True
    assert mirror.status == "doing"
    assert mirror.real_started == datetime(2026, 7, 20, 9, 0)

    monkeypatch.setattr(tss, "local_now", lambda: datetime(2026, 7, 20, 10, 0))
    assert svc.pause_requirement_task(req)["ok"] is True
    assert mirror.status == "pause"

    monkeypatch.setattr(tss, "local_now", lambda: datetime(2026, 7, 20, 11, 0))
    assert svc.finish_requirement_task(req)["ok"] is True
    assert mirror.status == "done"
    assert mirror.finished_date == datetime(2026, 7, 20, 11, 0)

    monkeypatch.setattr(tss, "local_now", lambda: datetime(2026, 7, 20, 12, 0))
    assert svc.reactivate_requirement_task(req)["ok"] is True
    assert mirror.status == "doing"

    assert [(event, payload["status"], payload["action"]) for event, payload, _ in events] == [
        ("zentao_task_changed", "doing", "start"),
        ("zentao_task_changed", "pause", "pause"),
        ("zentao_task_changed", "done", "finish"),
        ("zentao_task_changed", "doing", "reactivate"),
    ]
    assert all(channels == ["global"] for _, _, channels in events)


def test_finish_computes_consumed_from_worktime(db_session, monkeypatch, req):
    client = FakeClient()
    monkeypatch.setattr(tss, "get_system_zentao_client", lambda db: client)
    # 开始时间设为今天 9:00，完成走 local_now；用固定 now 保证可断言
    fixed_start = datetime(2026, 6, 29, 9, 0, 0)   # Monday
    fixed_end = datetime(2026, 6, 29, 11, 0, 0)    # +2h 工作时段内
    req.task_started_at = fixed_start
    db_session.commit()
    monkeypatch.setattr(tss, "local_now", lambda: fixed_end)
    svc = ZentaoTaskSyncService(db_session)
    res = svc.finish_requirement_task(req)
    assert res["ok"] is True
    # 9:00-11:00 全在上午窗口 = 2.0h
    assert res["consumed"] == 2.0
    assert req.zentao_task_status_cache == "done"
    finish_call = next(c for c in client.calls if c[0] == "finish")
    assert finish_call[2] == 2.0  # current_consumed


def test_finish_does_not_repeat_when_rest_confirms_done_but_readback_is_stale(db_session, monkeypatch, req):
    """REST 已确认完成时，旧状态回读不能再触发相同工时的网页 finish。"""
    from app.models import User, UserRole
    from app.services.zentao_web_session import ZentaoWebLogin

    actor = User(username="tester_finish_once", password_hash="x", role=UserRole.USER)
    db_session.add(actor)
    db_session.commit()

    class StaleReadbackFinishClient(FakeClient):
        def finish_task(self, task_id, *, current_consumed, finished_date=None, assigned_to=None):
            self.calls.append(("finish", task_id, current_consumed, finished_date))
            return {"id": task_id, "status": "done"}  # 写响应成功，紧随其后的 GET 仍是 doing

    client = StaleReadbackFinishClient(status="doing")
    system = FakeClient(status="doing")
    web_calls = []
    monkeypatch.setattr(tss, "get_user_zentao_client", lambda uid, db: client)
    monkeypatch.setattr(tss, "get_system_zentao_client", lambda db: system)
    monkeypatch.setattr(
        tss,
        "get_user_zentao_web_login",
        lambda uid, db: ZentaoWebLogin(base_url="http://z", account="tester", password="p"),
    )
    monkeypatch.setattr(tss, "get_system_zentao_web_login", lambda db: None)
    monkeypatch.setattr(
        tss,
        "finish_task_via_web",
        lambda *args, **kwargs: web_calls.append((args, kwargs)),
    )
    req.task_started_at = datetime(2026, 7, 20, 9, 0)
    req.zentao_task_status_cache = "doing"
    db_session.commit()
    monkeypatch.setattr(tss, "local_now", lambda: datetime(2026, 7, 20, 11, 0))

    res = ZentaoTaskSyncService(db_session).finish_requirement_task(req, acting_user=actor)

    assert res["ok"] is True
    assert [call for call in client.calls if call[0] == "finish"] == [
        ("finish", 777, 2.0, "2026-07-20 11:00:00")
    ]
    assert web_calls == []
    assert system.calls == []
    assert req.zentao_task_status_cache == "done"


def test_finish_without_timer_requires_confirmed_consumed(db_session, monkeypatch, req):
    client = FakeClient()
    monkeypatch.setattr(tss, "get_system_zentao_client", lambda db: client)
    req.task_started_at = None
    db_session.commit()
    svc = ZentaoTaskSyncService(db_session)
    with pytest.raises(HTTPException) as exc:
        svc.finish_requirement_task(req)
    assert exc.value.status_code == 400
    assert "实际工时" in exc.value.detail
    assert not any(call[0] == "finish" for call in client.calls)

    res = svc.finish_requirement_task(req, consumed=0.75)
    assert res["ok"] is True
    assert res["consumed"] == 0.75


def test_finish_short_timed_requirement_uses_minimum_not_estimate(db_session, monkeypatch, req):
    client = FakeClient(status="doing")
    monkeypatch.setattr(tss, "get_system_zentao_client", lambda db: client)
    req.task_started_at = datetime(2026, 7, 20, 10, 0, 0)
    req.zentao_task_status_cache = "doing"
    db_session.commit()
    monkeypatch.setattr(tss, "local_now", lambda: datetime(2026, 7, 20, 10, 0, 30))

    res = ZentaoTaskSyncService(db_session).finish_requirement_task(req)

    assert res["ok"] is True
    assert res["consumed"] == 0.1
    finish_call = next(call for call in client.calls if call[0] == "finish")
    assert finish_call[2] == 0.1


def test_reactivate_calls_restart(db_session, monkeypatch, req):
    client = FakeClient()
    monkeypatch.setattr(tss, "get_system_zentao_client", lambda db: client)
    svc = ZentaoTaskSyncService(db_session)
    res = svc.reactivate_requirement_task(req)
    assert res["ok"] is True
    assert req.task_finished_at is None
    assert req.zentao_task_status_cache == "doing"
    restart = next(c for c in client.calls if c[0] == "restart")
    assert restart[2] == 2.0   # consumed from get_task
    assert restart[3] == 4.0   # left = estimated hours
    # 工时口径：重新激活从零起算新计时段（finish 的 currentConsumed 是增量）
    assert req.task_started_at is not None
    assert req.task_consumed_accum == 0.0


# ─── 工时结算：暂停期不计工时 ────────────────────────────────────────────

def test_pause_settles_segment_and_stops_clock(db_session, monkeypatch, req):
    """暂停：本段（开始→暂停）结算进累计，计时起点清空。"""
    client = FakeClient(status="doing")
    monkeypatch.setattr(tss, "get_system_zentao_client", lambda db: client)
    monkeypatch.setattr(tss, "get_system_zentao_web_login", lambda db: None)
    req.task_started_at = datetime(2026, 6, 29, 9, 0)   # 周一 9:00 开始
    req.zentao_task_status_cache = "doing"
    db_session.commit()
    monkeypatch.setattr(tss, "local_now", lambda: datetime(2026, 6, 29, 11, 0))  # 11:00 暂停
    res = ZentaoTaskSyncService(db_session).pause_requirement_task(req)
    assert res["ok"] is True
    assert req.task_consumed_accum == 2.0
    assert req.task_started_at is None


def test_resume_then_finish_sums_segments_excluding_pause(db_session, monkeypatch, req):
    """暂停中继续：累计保留、重新起算；完成 = 累计 + 最后一段（暂停期不计）。"""
    client = FakeClient(status="pause")
    monkeypatch.setattr(tss, "get_system_zentao_client", lambda db: client)
    req.task_consumed_accum = 2.0     # 周一已结算 2h
    req.task_started_at = None
    req.zentao_task_status_cache = "pause"
    db_session.commit()
    svc = ZentaoTaskSyncService(db_session)
    # 周三 9:00 继续（隔了一天半的暂停期）
    monkeypatch.setattr(tss, "local_now", lambda: datetime(2026, 7, 1, 9, 0))
    res = svc.start_requirement_task(req)
    assert res["ok"] is True
    assert req.task_consumed_accum == 2.0                 # 继续不清累计
    assert req.task_started_at == datetime(2026, 7, 1, 9, 0)
    assert any(c[0] == "restart" for c in client.calls)   # 暂停中 → restart 而非 start
    # 周三 10:30 完成：总工时 = 2 + 1.5，暂停的一天半不计入
    monkeypatch.setattr(tss, "local_now", lambda: datetime(2026, 7, 1, 10, 30))
    res = svc.finish_requirement_task(req)
    assert res["ok"] is True
    assert res["consumed"] == 3.5
    finish_call = next(c for c in client.calls if c[0] == "finish")
    assert finish_call[2] == 3.5


def test_finish_while_paused_uses_accum_only(db_session, monkeypatch, req):
    """暂停中直接完成（不先继续）：只算已结算累计，暂停期不计入。"""
    client = FakeClient(status="pause")
    monkeypatch.setattr(tss, "get_system_zentao_client", lambda db: client)
    req.task_consumed_accum = 2.0
    req.task_started_at = None
    req.zentao_task_status_cache = "pause"
    db_session.commit()
    # 暂停两天后直接完成
    monkeypatch.setattr(tss, "local_now", lambda: datetime(2026, 7, 1, 15, 0))
    res = ZentaoTaskSyncService(db_session).finish_requirement_task(req)
    assert res["consumed"] == 2.0


def test_fresh_start_resets_accum(db_session, monkeypatch, req):
    """非暂停状态的全新开始：累计清零（不带上一轮的工时）。"""
    client = FakeClient()
    monkeypatch.setattr(tss, "get_system_zentao_client", lambda db: client)
    req.task_consumed_accum = 5.0
    req.zentao_task_status_cache = "wait"
    db_session.commit()
    res = ZentaoTaskSyncService(db_session).start_requirement_task(req)
    assert res["ok"] is True
    assert req.task_consumed_accum == 0.0
    assert req.task_started_at is not None


def test_start_guest_token_falls_back_to_system_client(db_session, monkeypatch, req):
    """本人 token 被当 guest（200 但未生效）→ 回读发现没切 doing → 系统账号兜底。"""
    from app.models import User, UserRole
    actor = User(username="tester_lc", password_hash="x", role=UserRole.USER)
    db_session.add(actor)
    db_session.commit()
    guest = FakeClient(effective=False)
    system = FakeClient()
    monkeypatch.setattr(tss, "get_user_zentao_client", lambda uid, db: guest)
    monkeypatch.setattr(tss, "get_system_zentao_client", lambda db: system)
    res = ZentaoTaskSyncService(db_session).start_requirement_task(req, acting_user=actor)
    assert res["ok"] is True
    assert req.zentao_task_status_cache == "doing"
    assert any(c[0] == "start" for c in guest.calls)
    assert any(c[0] == "start" for c in system.calls)


def test_start_all_candidates_ineffective_reports_and_keeps_truth(db_session, monkeypatch, req):
    """所有候选都未生效 → errors 上报，状态与计时均保持操作前值。"""
    before_started = datetime(2026, 7, 1, 8, 0)
    req.zentao_task_status_cache = "wait"
    req.task_consumed_accum = 5.0
    req.task_started_at = before_started
    db_session.commit()
    # 回读甚至出现了与目标无关的 pause，也不能由这次失败的 start 写入本地。
    client = FakeClient(effective=False, status="pause")
    monkeypatch.setattr(tss, "get_system_zentao_client", lambda db: client)
    res = ZentaoTaskSyncService(db_session).start_requirement_task(req)
    assert res["ok"] is False
    assert any("未生效" in e for e in res["errors"])
    assert req.zentao_task_status_cache == "wait"
    assert req.task_consumed_accum == 5.0
    assert req.task_started_at == before_started


def test_start_rest_raises_but_actually_applied(db_session, monkeypatch, req):
    """REST 抛错（如超时）但禅道已生效 → 回读判定成功，不误报失败。"""
    client = FakeClient()

    orig = client.start_task
    def _flaky(*args, **kwargs):
        orig(*args, **kwargs)  # 生效
        raise TimeoutError("read timed out")
    client.start_task = _flaky

    monkeypatch.setattr(tss, "get_system_zentao_client", lambda db: client)
    res = ZentaoTaskSyncService(db_session).start_requirement_task(req)
    assert res["ok"] is True
    assert req.zentao_task_status_cache == "doing"


def test_lifecycle_via_status_choke_point(db_session, monkeypatch, req):
    """通过 RequirementService 勾选 test_completed 也应驱动禅道 finish/restart。"""
    from app.services.requirement_service import RequirementService
    client = FakeClient()
    monkeypatch.setattr(tss, "get_system_zentao_client", lambda db: client)
    svc = RequirementService(db_session)
    # 没有计时依据时先要求人工确认，且不得提前改本地完成状态。
    with pytest.raises(HTTPException):
        svc._mark_test_completed_transition(req, True)
    assert req.test_completed is False
    assert not any(c[0] == "finish" for c in client.calls)
    # 提供人工确认工时后勾选完成
    svc._mark_test_completed_transition(req, True, task_consumed_hours=0.5)
    assert any(c[0] == "finish" for c in client.calls)
    # 取消完成
    svc._mark_test_completed_transition(req, False)
    assert any(c[0] == "restart" for c in client.calls)


# ─── 分段提交禅道工时记录（2026-07-17 工时口径升级）──────────────────────────


def _fake_login():
    from app.services.zentao_web_session import ZentaoWebLogin
    return ZentaoWebLogin(base_url="http://z", account="tester", password="p")


def test_req_pause_submits_day_split_efforts(db_session, monkeypatch, req):
    """有本人网页凭据：暂停把本段按天拆分提交为工时记录，本地累计保持 0。"""
    from datetime import date
    from app.models import User, UserRole

    actor = User(username="tester_eff", password_hash="x", role=UserRole.USER)
    db_session.add(actor)
    db_session.commit()
    client = FakeClient(status="doing")
    monkeypatch.setattr(tss, "get_system_zentao_client", lambda db: client)
    monkeypatch.setattr(tss, "get_user_zentao_client", lambda uid, db: client)
    monkeypatch.setattr(tss, "get_user_zentao_web_login", lambda uid, db: _fake_login())
    submitted = []

    def fake_submit(login, task_id, day_rows, *, left_before, note=""):
        submitted.append((login.account, task_id, day_rows))
        return round(sum(h for _, h in day_rows), 2)

    monkeypatch.setattr(tss, "submit_day_efforts", fake_submit)
    req.task_started_at = datetime(2026, 6, 29, 15, 0)  # 周一 15:00 开始
    req.zentao_task_status_cache = "doing"
    db_session.commit()
    # 周三 10:30 暂停（2026-07-17 起取消工作时段窗口：工作日自然时间全计）
    monkeypatch.setattr(tss, "local_now", lambda: datetime(2026, 7, 1, 10, 30))
    res = ZentaoTaskSyncService(db_session).pause_requirement_task(req, acting_user=actor)
    assert res["ok"] is True
    assert submitted == [("tester", 777, [
        (date(2026, 6, 29), 9.0),
        (date(2026, 6, 30), 24.0),
        (date(2026, 7, 1), 10.5),
    ])]
    assert req.task_consumed_accum == 0.0
    assert req.task_efforts_submitted == 43.5
    assert req.task_started_at is None


def test_req_finish_presubmits_prev_days_reports_today(db_session, monkeypatch, req):
    """完成：今天之前的段先按天提交，finish 只带今天的部分。"""
    from datetime import date
    from app.models import User, UserRole

    actor = User(username="tester_fin", password_hash="x", role=UserRole.USER)
    db_session.add(actor)
    db_session.commit()
    client = FakeClient(status="doing")
    monkeypatch.setattr(tss, "get_system_zentao_client", lambda db: client)
    monkeypatch.setattr(tss, "get_user_zentao_client", lambda uid, db: client)
    monkeypatch.setattr(tss, "get_user_zentao_web_login", lambda uid, db: _fake_login())
    submitted = []

    def fake_submit(login, task_id, day_rows, *, left_before, note=""):
        submitted.append(day_rows)
        return round(sum(h for _, h in day_rows), 2)

    monkeypatch.setattr(tss, "submit_day_efforts", fake_submit)
    req.task_started_at = datetime(2026, 6, 29, 15, 0)
    db_session.commit()
    monkeypatch.setattr(tss, "local_now", lambda: datetime(2026, 7, 1, 10, 30))
    res = ZentaoTaskSyncService(db_session).finish_requirement_task(req, acting_user=actor)
    assert res["ok"] is True
    assert submitted == [[(date(2026, 6, 29), 9.0), (date(2026, 6, 30), 24.0)]]
    assert res["consumed"] == 10.5
    finish_call = next(c for c in client.calls if c[0] == "finish")
    assert finish_call[2] == 10.5
    assert req.task_consumed_accum == 0.0
    assert req.task_efforts_submitted == round(33.0 + 10.5, 2)


def test_req_finish_after_segments_submitted_uses_min_floor(db_session, monkeypatch, req):
    """暂停时已分段提交过 → 暂停中直接完成给最小值 0.1，不再拿预计工时兜底。"""
    client = FakeClient(status="pause")
    monkeypatch.setattr(tss, "get_system_zentao_client", lambda db: client)
    req.task_started_at = None
    req.task_consumed_accum = 0.0
    req.task_efforts_submitted = 2.0
    req.zentao_task_status_cache = "pause"
    db_session.commit()
    monkeypatch.setattr(tss, "local_now", lambda: datetime(2026, 7, 1, 15, 0))
    res = ZentaoTaskSyncService(db_session).finish_requirement_task(req)
    assert res["consumed"] == 0.1


def test_req_pause_without_login_falls_back_to_local_accum(db_session, monkeypatch, req):
    """无本人网页凭据：退回旧口径本地累计（原有行为不变）。"""
    client = FakeClient(status="doing")
    monkeypatch.setattr(tss, "get_system_zentao_client", lambda db: client)
    monkeypatch.setattr(tss, "get_system_zentao_web_login", lambda db: None)
    monkeypatch.setattr(tss, "get_user_zentao_web_login", lambda uid, db: None)
    req.task_started_at = datetime(2026, 6, 29, 9, 0)
    req.zentao_task_status_cache = "doing"
    db_session.commit()
    monkeypatch.setattr(tss, "local_now", lambda: datetime(2026, 6, 29, 11, 0))
    res = ZentaoTaskSyncService(db_session).pause_requirement_task(req)
    assert res["ok"] is True
    assert req.task_consumed_accum == 2.0
    assert req.task_efforts_submitted == 0.0


def test_req_short_pause_preserves_minimum_tracking(db_session, monkeypatch, req):
    """不足一分钟即暂停且无法直提工时时，保留 0.1 小时，后续完成不误判为未计时。"""
    client = FakeClient(status="doing")
    monkeypatch.setattr(tss, "get_system_zentao_client", lambda db: client)
    monkeypatch.setattr(tss, "get_system_zentao_web_login", lambda db: None)
    monkeypatch.setattr(tss, "get_user_zentao_web_login", lambda uid, db: None)
    req.task_started_at = datetime(2026, 7, 20, 10, 0, 0)
    req.zentao_task_status_cache = "doing"
    db_session.commit()
    monkeypatch.setattr(tss, "local_now", lambda: datetime(2026, 7, 20, 10, 0, 30))

    result = ZentaoTaskSyncService(db_session).pause_requirement_task(req)

    assert result["ok"] is True
    assert req.task_consumed_accum == 0.1
    assert req.task_started_at is None


# ─── 操作人归属：本人 REST 失效 → 本人网页会话兜底（2026-07-17）──────────────


def test_req_start_falls_back_to_self_web_not_system(db_session, monkeypatch, req):
    """需求任务开始：本人 token guest 失效 → 本人网页会话兜底，系统账号不出场。"""
    from app.models import User, UserRole
    from app.services.zentao_web_session import ZentaoWebLogin

    actor = User(username="tester_sw", password_hash="x", role=UserRole.USER)
    db_session.add(actor)
    db_session.commit()
    guest = FakeClient(effective=False)   # REST 200 但不生效
    system = FakeClient()
    web_calls = []

    def fake_web_start(login, task_id, *, left, real_started=None, comment=None):
        web_calls.append((login.account, task_id))
        guest._task["status"] = "doing"   # 网页会话真正生效
        return {"result": "success"}

    monkeypatch.setattr(tss, "get_user_zentao_client", lambda uid, db: guest)
    monkeypatch.setattr(tss, "get_system_zentao_client", lambda db: system)
    monkeypatch.setattr(
        tss, "get_user_zentao_web_login",
        lambda uid, db: ZentaoWebLogin(base_url="http://z", account="tester", password="p"),
    )
    monkeypatch.setattr(tss, "get_system_zentao_web_login", lambda db: None)
    monkeypatch.setattr(tss, "start_task_via_web", fake_web_start)

    res = ZentaoTaskSyncService(db_session).start_requirement_task(req, acting_user=actor)
    assert res["ok"] is True
    assert web_calls == [("tester", 777)]
    assert system.calls == []             # 不再由系统账号代开始
    assert req.zentao_task_status_cache == "doing"


def test_req_pause_submits_efforts_before_pausing(db_session, monkeypatch, req):
    """需求链路同理：分段提交必须在暂停之前（防禅道对 pause 任务记工时自动激活）。"""
    from app.models import User, UserRole
    from app.services.zentao_web_session import ZentaoWebLogin

    actor = User(username="tester_ord", password_hash="x", role=UserRole.USER)
    db_session.add(actor)
    db_session.commit()
    seq = []

    class SeqClient(FakeClient):
        def pause_task(self, task_id, **kw):
            seq.append("pause")
            return super().pause_task(task_id, **kw)

    client = SeqClient(status="doing")
    monkeypatch.setattr(tss, "get_system_zentao_client", lambda db: client)
    monkeypatch.setattr(tss, "get_user_zentao_client", lambda uid, db: client)
    monkeypatch.setattr(
        tss, "get_user_zentao_web_login",
        lambda uid, db: ZentaoWebLogin(base_url="http://z", account="tester", password="p"),
    )

    def fake_submit(login, task_id, day_rows, *, left_before, note=""):
        seq.append("submit")
        return round(sum(h for _, h in day_rows), 2)

    monkeypatch.setattr(tss, "submit_day_efforts", fake_submit)
    req.task_started_at = datetime(2026, 7, 1, 9, 0)
    req.zentao_task_status_cache = "doing"
    db_session.commit()
    monkeypatch.setattr(tss, "local_now", lambda: datetime(2026, 7, 1, 11, 0))

    res = ZentaoTaskSyncService(db_session).pause_requirement_task(req, acting_user=actor)
    assert res["ok"] is True
    assert seq == ["submit", "pause"]
    assert req.task_started_at is None
    assert req.task_consumed_accum == 0.0
    assert req.task_efforts_submitted == 2.0


def test_req_pause_401_after_web_success_is_verified_by_system_client(db_session, monkeypatch, req):
    """A broken user REST token must not hide a successful web pause or invite a retry."""
    from app.models import User, UserRole
    from app.models.zentao_task_mirror import ZentaoTaskMirror
    from app.services.zentao_web_session import ZentaoWebLogin

    actor = User(username="pause_401", password_hash="x", role=UserRole.USER)
    db_session.add(actor)
    mirror = ZentaoTaskMirror(task_id=777, execution_id=1, name="task", status="doing")
    db_session.add(mirror)
    req.task_started_at = datetime(2026, 7, 21, 9, 0)
    req.zentao_task_status_cache = "doing"
    db_session.commit()

    class UnauthorizedClient:
        def get_task(self, task_id):
            raise RuntimeError("401 Unauthorized")

        def pause_task(self, task_id, **kwargs):
            raise RuntimeError("401 Unauthorized")

    broken = UnauthorizedClient()
    system = FakeClient(status="doing")
    web_calls = []
    submitted = []

    def fake_web_pause(login, task_id, *, comment=None):
        web_calls.append((login.account, task_id))
        system._task["status"] = "pause"
        return {"result": "success"}

    def fake_submit(login, task_id, day_rows, *, left_before, note=""):
        submitted.append(day_rows)
        return round(sum(hours for _, hours in day_rows), 2)

    monkeypatch.setattr(tss, "get_user_zentao_client", lambda uid, db: broken)
    monkeypatch.setattr(tss, "get_system_zentao_client", lambda db: system)
    monkeypatch.setattr(
        tss,
        "get_user_zentao_web_login",
        lambda uid, db: ZentaoWebLogin(base_url="http://z", account="pause_401", password="p"),
    )
    monkeypatch.setattr(tss, "get_system_zentao_web_login", lambda db: None)
    monkeypatch.setattr(tss, "pause_task_via_web", fake_web_pause)
    monkeypatch.setattr(tss, "submit_day_efforts", fake_submit)
    monkeypatch.setattr(tss, "local_now", lambda: datetime(2026, 7, 21, 11, 0))

    result = ZentaoTaskSyncService(db_session).pause_requirement_task(req, acting_user=actor)

    assert result["ok"] is True
    assert result["errors"] == []
    assert len(submitted) == 1
    assert web_calls == [("pause_401", 777)]
    assert not any(call[0] == "pause" for call in system.calls)
    assert req.zentao_task_status_cache == "pause"
    assert mirror.status == "pause"
    assert mirror.efforts_submitted == req.task_efforts_submitted
    assert mirror.local_started_at is None
