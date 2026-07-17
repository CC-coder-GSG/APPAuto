"""能力 C：开始/完成/重新激活 + 工时回算（用假客户端，无网络）。"""
from __future__ import annotations

from datetime import datetime

import pytest

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


def test_finish_falls_back_when_no_start(db_session, monkeypatch, req):
    client = FakeClient()
    monkeypatch.setattr(tss, "get_system_zentao_client", lambda db: client)
    req.task_started_at = None
    db_session.commit()
    svc = ZentaoTaskSyncService(db_session)
    res = svc.finish_requirement_task(req)
    # 无开始时间 → consumed 兜底用 estimated_test_hours(4)
    assert res["consumed"] == 4.0


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
    """所有候选都未生效 → errors 上报、缓存按回读落真实状态（不误标 doing）。"""
    client = FakeClient(effective=False)
    monkeypatch.setattr(tss, "get_system_zentao_client", lambda db: client)
    res = ZentaoTaskSyncService(db_session).start_requirement_task(req)
    assert res["ok"] is False
    assert any("未生效" in e for e in res["errors"])
    assert req.zentao_task_status_cache == "wait"


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
    # 勾选完成
    svc._mark_test_completed_transition(req, True)
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
