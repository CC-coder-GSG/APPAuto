from __future__ import annotations

from datetime import timedelta

from app.models import User, UserRole
import app.services.workbench_refresh_service as wrs
from app.services.workbench_refresh_service import WorkbenchRefreshService
from app.utils.time_utils import local_now


def _user(db):
    u = User(username="wb_refresh_user", password_hash="x", role=UserRole.USER)
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def _service_with_stubs(db, calls):
    svc = WorkbenchRefreshService(db)

    class StubTestcaseService:
        def sync_recent_software_testcases(self, *, software_id, current_user, sync_source):
            calls.append(("recent", software_id, sync_source))
            return {"software_id": software_id, "cached": False, "sync_mode": "recent", "created": 1, "updated": 0}

        def sync_software_testcases(self, *, software_id, current_user, force, sync_source):
            calls.append(("full", software_id, sync_source))
            return {"software_id": software_id, "cached": False, "created": 0, "updated": 0}

    class StubBugService:
        def sync_recent_zentao_bugs_by_software(self, **kw):
            calls.append(("bugs", kw.get("software_id")))
            return {"ok": True}

    svc.testcase_service = StubTestcaseService()
    svc.overall_test_service = StubBugService()
    return svc


def test_preflight_testcases_recent_uses_fast_path(db_session):
    wrs._testcase_recent_last_run.clear()
    user = _user(db_session)
    calls = []
    svc = _service_with_stubs(db_session, calls)

    res = svc.preflight_refresh(software_id=1, current_user=user, include_bugs=False, testcases_recent=True)
    assert res["testcases"]["sync_mode"] == "recent"
    assert ("recent", 1, "case_done_refresh") in calls

    # 60s 最小间隔内再次触发 → 返回 cached，不再打禅道
    calls.clear()
    res2 = svc.preflight_refresh(software_id=1, current_user=user, include_bugs=False, testcases_recent=True)
    assert res2["testcases"]["cached"] is True
    assert calls == []

    # 间隔过期后恢复真实同步
    wrs._testcase_recent_last_run[1] = local_now() - timedelta(seconds=61)
    res3 = svc.preflight_refresh(software_id=1, current_user=user, include_bugs=False, testcases_recent=True)
    assert res3["testcases"]["cached"] is False
    assert ("recent", 1, "case_done_refresh") in calls


def test_preflight_default_path_unchanged(db_session):
    wrs._testcase_recent_last_run.clear()
    user = _user(db_session)
    calls = []
    svc = _service_with_stubs(db_session, calls)
    res = svc.preflight_refresh(software_id=2, current_user=user, include_bugs=True)
    assert ("full", 2, "on_enter_refresh") in calls
    assert ("bugs", 2) in calls
    assert res["testcases"]["cached"] is False


def test_recent_failure_does_not_consume_interval(db_session):
    wrs._testcase_recent_last_run.clear()
    user = _user(db_session)
    svc = WorkbenchRefreshService(db_session)

    class FailingService:
        def sync_recent_software_testcases(self, **kw):
            raise RuntimeError("zentao down")

    svc.testcase_service = FailingService()
    res = svc.preflight_refresh(software_id=3, current_user=user, include_bugs=False, testcases_recent=True)
    assert res["testcases"]["ok"] is False
    # 失败不占用间隔窗口：时间戳被回滚，下次可立即重试
    assert 3 not in wrs._testcase_recent_last_run
