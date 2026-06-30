"""能力 C：开始/完成/重新激活 + 工时回算（用假客户端，无网络）。"""
from __future__ import annotations

from datetime import datetime

import pytest

from app.models import Requirement, Version, VersionType
import app.services.zentao_task_sync_service as tss
from app.services.zentao_task_sync_service import ZentaoTaskSyncService


class FakeClient:
    def __init__(self):
        self.calls = []
        self._task = {"id": 777, "consumed": 2.0, "status": "doing"}

    def start_task(self, task_id, *, real_started=None, left=None):
        self.calls.append(("start", task_id, real_started, left))
        return {"id": task_id, "status": "doing"}

    def finish_task(self, task_id, *, current_consumed, finished_date=None, assigned_to=None):
        self.calls.append(("finish", task_id, current_consumed, finished_date))
        return {"id": task_id, "status": "done"}

    def restart_task(self, task_id, *, consumed, left):
        self.calls.append(("restart", task_id, consumed, left))
        return {"id": task_id, "status": "doing"}

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
