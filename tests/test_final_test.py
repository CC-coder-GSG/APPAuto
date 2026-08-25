from datetime import date

from app.models import FinalTestRecord, Requirement, RequirementStatus, TestExecution, User, UserRole, Version, VersionType
from app.services.final_test_service import FinalTestService
from app.services.report_service import ReportService
from app.services.workbench_service import WorkbenchService


def _major(db, version_no="V9000"):
    v = Version(version_no=version_no, version_type=VersionType.MAJOR)
    db.add(v)
    db.commit()
    db.refresh(v)
    return v


def _minor(db, parent_id, version_no="V9000.1"):
    v = Version(version_no=version_no, version_type=VersionType.MINOR, parent_id=parent_id)
    db.add(v)
    db.commit()
    db.refresh(v)
    return v


def _user(db, username, role=UserRole.USER, team=True):
    u = User(username=username, password_hash=User.hash_password("pass123"), role=role, is_team_member=team)
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def _req(db, major_id, req_no, owner_id=None):
    r = Requirement(zentao_req_id=req_no, title="需求-" + req_no, major_version_id=major_id, owner_id=owner_id, status=RequirementStatus.PENDING)
    db.add(r)
    db.commit()
    db.refresh(r)
    return r


def test_toggle_enables_and_status(db_session):
    major = _major(db_session, "V9001")
    admin = _user(db_session, "ft_admin", role=UserRole.ADMIN)
    svc = FinalTestService(db_session)

    status = svc.toggle(major.id, True, admin)
    assert status["enabled"] is True
    assert status["started_at"] is not None
    db_session.refresh(major)
    assert major.final_test_enabled is True

    status = svc.toggle(major.id, False, admin)
    assert status["enabled"] is False


def test_upsert_record_isolated_per_user(db_session):
    major = _major(db_session, "V9002")
    admin = _user(db_session, "ft_admin2", role=UserRole.ADMIN)
    u1 = _user(db_session, "ft_u1")
    u2 = _user(db_session, "ft_u2")
    req = _req(db_session, major.id, "r#9002", owner_id=u1.id)
    FinalTestService(db_session).toggle(major.id, True, admin)

    svc = FinalTestService(db_session)
    svc.upsert_record(req.id, u1, test_completed=True)
    svc.upsert_record(req.id, u2, case_completed=True)

    recs = db_session.query(FinalTestRecord).filter(FinalTestRecord.requirement_id == req.id).all()
    by_user = {r.user_id: r for r in recs}
    assert by_user[u1.id].test_completed is True
    assert by_user[u1.id].case_completed is False
    assert by_user[u2.id].test_completed is False
    assert by_user[u2.id].case_completed is True
    # 原需求行未被污染
    db_session.refresh(req)
    assert req.test_completed is False
    assert req.case_completed is False


def test_upsert_record_rejected_when_disabled(db_session):
    major = _major(db_session, "V9003")
    u1 = _user(db_session, "ft_u3")
    req = _req(db_session, major.id, "r#9003", owner_id=u1.id)
    try:
        FinalTestService(db_session).upsert_record(req.id, u1, test_completed=True)
        assert False, "expected HTTPException"
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 400


def test_workbench_final_test_shows_all_reqs_with_user_state(db_session):
    major = _major(db_session, "V9004")
    admin = _user(db_session, "ft_admin4", role=UserRole.ADMIN)
    owner = _user(db_session, "ft_owner4")
    viewer = _user(db_session, "ft_viewer4")  # 非负责人
    r1 = _req(db_session, major.id, "r#9101", owner_id=owner.id)
    r2 = _req(db_session, major.id, "r#9102", owner_id=owner.id)
    FinalTestService(db_session).toggle(major.id, True, admin)
    FinalTestService(db_session).upsert_record(r1.id, viewer, test_completed=True)

    rows = WorkbenchService(db_session).get_my_workbench(
        current_user=viewer, major_version_id=major.id, mode="version"
    )
    # 非负责人也能看到该版本全部需求
    assert {row["id"] for row in rows} == {r1.id, r2.id}
    assert all(row["final_test"] is True for row in rows)
    by_id = {row["id"]: row for row in rows}
    assert by_id[r1.id]["test_completed"] is True
    assert by_id[r2.id]["test_completed"] is False  # 未勾选默认 False


def test_report_executed_requirements_includes_final_test(db_session):
    major = _major(db_session, "V9005")
    minor = _minor(db_session, major.id, "V9005.1")
    admin = _user(db_session, "ft_admin5", role=UserRole.ADMIN)
    user = _user(db_session, "ft_rep5")
    r1 = _req(db_session, major.id, "r#9201", owner_id=user.id)
    r2 = _req(db_session, major.id, "r#9202", owner_id=user.id)
    FinalTestService(db_session).toggle(major.id, True, admin)
    FinalTestService(db_session).upsert_record(r1.id, user, test_completed=True)
    FinalTestService(db_session).upsert_record(r2.id, user, test_completed=True)
    # 同一人同一需求也有一条普通执行记录 —— 应去重不重复计数
    db_session.add(TestExecution(requirement_id=r1.id, minor_version_id=minor.id, executed_by_id=user.id))
    db_session.commit()

    today = date.today()
    result = ReportService(db_session).summary(today, today, current_user=user, user_id=user.id, major_version_id=major.id)
    assert result["overview"]["executed_requirements"] == 2


def test_workbench_mine_exposes_zentao_task_linkage(db_session):
    """回归：/workbench/mine 必须返回禅道子任务关联字段，否则已建任务的需求
    会在需求工作台错误显示为「未关联禅道任务」。"""
    major = _major(db_session, "V9007")
    owner = _user(db_session, "ft_owner7")
    linked = _req(db_session, major.id, "r#9401", owner_id=owner.id)
    unlinked = _req(db_session, major.id, "r#9402", owner_id=owner.id)
    linked.zentao_task_id = 17196
    linked.zentao_parent_task_id = 17195
    linked.zentao_task_status_cache = "wait"
    linked.estimated_test_hours = 4.0
    db_session.commit()

    rows = WorkbenchService(db_session).get_my_workbench(
        current_user=owner, major_version_id=major.id, mode="version"
    )
    by_id = {row["id"]: row for row in rows}
    # 关联了禅道任务的需求应带出 task id / 状态 / 预计用时
    assert by_id[linked.id]["zentao_task_id"] == 17196
    assert by_id[linked.id]["zentao_task_status"] == "wait"
    assert by_id[linked.id]["estimated_test_hours"] == 4.0
    assert "task_started_at" in by_id[linked.id]
    # 未关联的需求返回 None，前端据此显示「未关联禅道任务」
    assert by_id[unlinked.id]["zentao_task_id"] is None


def test_workbench_mine_task_assignee_flag(db_session):
    """指派人判定：task_assigned_to_me 依据禅道账号匹配子任务指派人。"""
    major = _major(db_session, "V9008")
    owner = _user(db_session, "ft_owner8")
    owner.zentao_account = "owner8acc"
    r = _req(db_session, major.id, "r#9501", owner_id=owner.id)
    r.zentao_task_id = 800
    r.zentao_task_assigned_to = "owner8acc"
    db_session.commit()

    rows = WorkbenchService(db_session).get_my_workbench(
        current_user=owner, major_version_id=major.id, mode="version"
    )
    row = {x["id"]: x for x in rows}[r.id]
    assert row["task_assigned_to_me"] is True
    assert row["zentao_task_assigned_to"] == "owner8acc"

    # 指派给别人 → False（前端据此隐藏开始/预计用时）
    r.zentao_task_assigned_to = "someoneelse"
    db_session.commit()
    rows = WorkbenchService(db_session).get_my_workbench(
        current_user=owner, major_version_id=major.id, mode="version"
    )
    assert {x["id"]: x for x in rows}[r.id]["task_assigned_to_me"] is False


def test_final_test_finish_only_for_task_assignee(db_session, monkeypatch):
    """最终测试勾选完成：仅子任务指派人本人才联动禅道 finish，其他人只留本地记录。"""
    major = _major(db_session, "V9009")
    admin = _user(db_session, "ft_admin9", role=UserRole.ADMIN)
    assignee = _user(db_session, "ft_assignee9")
    assignee.zentao_account = "acc9"
    other = _user(db_session, "ft_other9")
    other.zentao_account = "other9"
    r = _req(db_session, major.id, "r#9601", owner_id=assignee.id)
    r.zentao_task_id = 900
    r.zentao_task_assigned_to = "acc9"
    db_session.commit()
    FinalTestService(db_session).toggle(major.id, True, admin)

    calls = []
    monkeypatch.setattr(
        "app.services.zentao_task_sync_service.ZentaoTaskSyncService.finish_requirement_task",
        lambda self, requirement, **kw: (calls.append(requirement.id), {"ok": True, "errors": []})[1],
    )
    # 非指派人勾选完成 → 只本地，不联动禅道
    FinalTestService(db_session).upsert_record(r.id, other, test_completed=True)
    assert calls == []
    # 指派人勾选完成 → 联动禅道 finish
    FinalTestService(db_session).upsert_record(r.id, assignee, test_completed=True)
    assert calls == [r.id]


def test_final_test_cancel_keeps_record_done_when_zentao_reactivate_fails(db_session, monkeypatch):
    major = _major(db_session, "V9013")
    admin = _user(db_session, "ft_admin13", role=UserRole.ADMIN)
    assignee = _user(db_session, "ft_assignee13")
    assignee.zentao_account = "acc13"
    req = _req(db_session, major.id, "r#9713", owner_id=assignee.id)
    req.zentao_task_id = 1300
    req.zentao_task_assigned_to = "acc13"
    db_session.commit()
    FinalTestService(db_session).toggle(major.id, True, admin)
    record = FinalTestRecord(
        requirement_id=req.id,
        user_id=assignee.id,
        test_completed=True,
    )
    db_session.add(record)
    db_session.commit()
    monkeypatch.setattr(
        "app.services.zentao_task_sync_service.ZentaoTaskSyncService.reactivate_requirement_task",
        lambda self, requirement, **kwargs: {
            "ok": False,
            "errors": ["禅道重新激活任务未生效"],
        },
    )

    import pytest
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc_info:
        FinalTestService(db_session).upsert_record(req.id, assignee, test_completed=False)

    assert exc_info.value.status_code == 502
    db_session.refresh(record)
    assert record.test_completed is True


def test_start_task_blocked_for_non_assignee(db_session):
    """开始任务：即便是负责人，若不是子任务指派人也被拒绝（仅指派人可开始）。"""
    import pytest
    from fastapi import HTTPException
    from app.services.requirement_service import RequirementService

    major = _major(db_session, "V9010")
    owner_not_assignee = _user(db_session, "ft_ot10")
    owner_not_assignee.zentao_account = "ot10"
    r = _req(db_session, major.id, "r#9701", owner_id=owner_not_assignee.id)
    r.zentao_task_id = 1000
    r.zentao_task_assigned_to = "acc10"  # 指派给别人
    db_session.commit()

    with pytest.raises(HTTPException) as ei:
        RequirementService(db_session).start_requirement_task(r.id, owner_not_assignee)
    assert ei.value.status_code == 403


def test_progress_for_major_aggregates(db_session):
    major = _major(db_session, "V9006")
    admin = _user(db_session, "ft_admin6", role=UserRole.ADMIN)
    u1 = _user(db_session, "ft_p1")
    u2 = _user(db_session, "ft_p2")
    r1 = _req(db_session, major.id, "r#9301", owner_id=u1.id)
    r2 = _req(db_session, major.id, "r#9302", owner_id=u1.id)
    svc = FinalTestService(db_session)
    svc.toggle(major.id, True, admin)
    svc.upsert_record(r1.id, u1, case_completed=True, test_completed=True)
    svc.upsert_record(r2.id, u1, case_completed=True)
    svc.upsert_record(r1.id, u2, test_completed=True)

    prog = svc.progress_for_major(major.id)
    assert prog["enabled"] is True
    assert prog["total_requirements"] == 2
    people = {p["user_id"]: p for p in prog["people"]}
    assert people[u1.id]["case_done"] == 2
    assert people[u1.id]["test_done"] == 1
    assert people[u2.id]["test_done"] == 1
