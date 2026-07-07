from datetime import date, datetime, timedelta

from fastapi import HTTPException

from app.models import BugSourceType, BugTracking, FieldTestPurposeType, FieldTestResultStatus, Requirement, TestCase, TestExecution, TestResultStatus, User, UserRole, Version, VersionType
from app.services.report_service import ReportService
from app.services.field_test_service import FieldTestService
from app.services.requirement_service import RequirementService
from scripts.backfill_test_executions import backfill_test_executions


def _create_user(db_session, username: str, role: UserRole = UserRole.USER, is_team_member: bool = True) -> User:
    user = User(username=username, password_hash=User.hash_password("pass123"), role=role, is_team_member=is_team_member)
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


def _create_major(db_session, version_no: str = "V7000") -> Version:
    major = Version(version_no=version_no, version_type=VersionType.MAJOR)
    db_session.add(major)
    db_session.commit()
    db_session.refresh(major)
    return major


def _create_minor(db_session, parent_id: int, version_no: str = "V7000.1") -> Version:
    minor = Version(version_no=version_no, version_type=VersionType.MINOR, parent_id=parent_id)
    db_session.add(minor)
    db_session.commit()
    db_session.refresh(minor)
    return minor


def test_summary_blocks_non_admin_viewing_other_user(db_session):
    current_user = _create_user(db_session, "report_user")
    other_user = _create_user(db_session, "report_other")
    service = ReportService(db_session)

    try:
        service.summary(date(2026, 1, 1), date(2026, 1, 2), current_user, other_user.id)
        assert False, "expected HTTPException"
    except HTTPException as exc:
        assert exc.status_code == 403
        assert "无权限查看其他人的报表" in exc.detail


def test_summary_returns_expected_structure(db_session):
    user = _create_user(db_session, "report_user2")
    major = _create_major(db_session)
    minor = _create_minor(db_session, major.id)
    req = Requirement(zentao_req_id="r#7001", title="报表需求", major_version_id=major.id, owner_id=user.id)
    db_session.add(req)
    db_session.commit()
    db_session.refresh(req)
    db_session.add(TestCase(requirement_id=req.id, zentao_case_id="u#7001", creator_id=user.id, created_at=datetime(2026, 1, 1, 10, 0, 0)))
    db_session.add(TestExecution(requirement_id=req.id, minor_version_id=minor.id, executed_by_id=user.id, executed_at=datetime(2026, 1, 1, 11, 0, 0), result_status=TestResultStatus.PASSED))
    db_session.add(BugTracking(major_version_id=major.id, requirement_id=req.id, source_type=BugSourceType.MANUAL, bug_id="b#7001", found_minor_version_id=minor.id, created_by_id=user.id, created_at=datetime(2026, 1, 1, 12, 0, 0)))
    db_session.commit()

    service = ReportService(db_session)
    result = service.summary(date(2026, 1, 1), date(2026, 1, 1), user)

    assert "overview" in result
    assert "trend" in result
    assert "bug_source_dist" in result
    assert "bug_major_dist" in result
    assert result["overview"]["created_cases"] == 1
    assert result["overview"]["created_bugs"] == 1
    major_map = {x["major_version_no"]: x["count"] for x in result["bug_major_dist"]}
    assert major_map.get(major.version_no) == 1


def test_advanced_and_version_bugs_return_expected_shapes(db_session):
    user = _create_user(db_session, "report_user3", role=UserRole.ADMIN)
    major = _create_major(db_session, "V7001")
    minor = _create_minor(db_session, major.id, "V7001.1")
    req = Requirement(zentao_req_id="r#7002", title="高级报表需求", major_version_id=major.id, owner_id=user.id)
    db_session.add(req)
    db_session.commit()
    db_session.refresh(req)
    db_session.add(BugTracking(major_version_id=major.id, requirement_id=req.id, source_type=BugSourceType.MANUAL, bug_id="b#7002", found_minor_version_id=minor.id, created_by_id=user.id, created_at=datetime(2026, 1, 2, 9, 0, 0), closed=True, resolution="fixed"))
    db_session.add(TestExecution(requirement_id=req.id, minor_version_id=minor.id, executed_by_id=user.id, executed_at=datetime(2026, 1, 2, 10, 0, 0), result_status=TestResultStatus.PASSED))
    db_session.commit()

    service = ReportService(db_session)
    advanced = service.advanced(date(2026, 1, 2), date(2026, 1, 2))
    version_bugs = service.version_bugs()

    assert set(advanced.keys()) == {"top_reqs", "leakage", "funnel", "executions"}
    assert isinstance(version_bugs, list)
    assert version_bugs[0]["bug_count"] >= 1


def test_summary_executed_requirements_not_increased_by_requirement_flag_only(db_session):
    user = _create_user(db_session, "report_user4")
    major = _create_major(db_session, "V7100")
    req = Requirement(zentao_req_id="r#7101", title="仅勾选测试完成", major_version_id=major.id, owner_id=user.id, test_completed=True)
    db_session.add(req)
    db_session.commit()

    service = ReportService(db_session)
    result = service.summary(date(2026, 1, 1), date(2026, 12, 31), user)
    assert result["overview"]["executed_requirements"] == 0


def test_summary_executed_requirements_increased_after_upsert_execution(db_session):
    user = _create_user(db_session, "report_user5")
    major = _create_major(db_session, "V7200")
    minor = _create_minor(db_session, major.id, "V7200.1")
    req = Requirement(zentao_req_id="r#7201", title="执行写入后统计", major_version_id=major.id, owner_id=user.id)
    db_session.add(req)
    db_session.commit()

    RequirementService(db_session).upsert_test_execution(
        requirement_id=req.id,
        minor_version_id=minor.id,
        bug_id=None,
        source_case_id=None,
        result_status="passed",
        notes="报表统计测试",
        test_completed=True,
        actor_id=user.id,
    )

    service = ReportService(db_session)
    result = service.summary(date(2026, 1, 1), date(2026, 12, 31), user)
    assert result["overview"]["executed_requirements"] == 1


def test_summary_executed_requirements_increased_after_backfill(db_session):
    user = _create_user(db_session, "report_user6")
    major = _create_major(db_session, "V7300")
    req = Requirement(zentao_req_id="r#7301", title="历史补录统计", major_version_id=major.id, owner_id=user.id, test_completed=True)
    db_session.add(req)
    db_session.commit()

    # 回填前统计为 0
    service = ReportService(db_session)
    before = service.summary(date(2026, 1, 1), date(2026, 12, 31), user)
    assert before["overview"]["executed_requirements"] == 0

    backfill_result = backfill_test_executions(db_session, apply=True, major_id=major.id, verbose=False)
    assert backfill_result["failed"] == 0

    after = service.summary(date(2026, 1, 1), date(2026, 12, 31), user)
    assert after["overview"]["executed_requirements"] == 1


def test_weekly_task_report_groups_by_version_and_person(db_session):
    from app.models.zentao_task_mirror import ZentaoTaskMirror
    from app.utils.time_utils import local_now

    major = _create_major(db_session, "V4.0.4.0")
    major.zentao_execution_id = 2100
    db_session.commit()

    today = local_now().date()
    monday = today - timedelta(days=today.weekday())

    def mk(task_id, name, *, parent=0, is_parent=0, status="done", person="陈文博",
           start=None, finish=None, consumed=None, finisher=None):
        row = ZentaoTaskMirror(
            task_id=task_id, execution_id=2100, execution_name_cache="V4.0.4.0",
            parent=parent, is_parent=is_parent, name=name, type="test", status=status,
            assigned_to="acct_" + person, assigned_to_realname=person,
            finished_by=("acct_" + finisher) if finisher else None,
            finished_by_realname=finisher,
            est_started=start, real_started=datetime.combine(start, datetime.min.time()) if start else None,
            finished_date=datetime.combine(finish, datetime.min.time()) if finish else None,
            consumed=consumed,
        )
        db_session.add(row)
        return row

    mk(16776, "父任务描述", is_parent=1, status="done", start=monday, finish=monday + timedelta(days=2))
    mk(16777, "子任务A", parent=16776, status="done", start=monday, finish=monday + timedelta(days=1), consumed=4)
    mk(16800, "独立任务", status="doing", person="张三", start=monday + timedelta(days=1))
    # 上周就完成的任务：不应出现在本周报告
    mk(15000, "上周任务", status="done", start=monday - timedelta(days=7), finish=monday - timedelta(days=5))
    # 已取消的任务：排除
    mk(15001, "取消任务", status="cancel", start=monday)
    db_session.commit()

    result = ReportService(db_session).weekly_task_report()
    text = result["text"]
    assert result["week_start"] == monday.isoformat()
    assert "V4.0.4.0：" in text
    assert "t#16776 父任务描述" in text
    assert "└ t#16777 子任务A" in text
    assert "(工时4h)" in text
    assert "t#16800 独立任务" in text and "进行中" in text
    assert "张三" in text and "陈文博" in text
    assert "t#15000" not in text
    assert "t#15001" not in text
    # 子任务行缩进在父任务行之后
    assert text.index("t#16776") < text.index("t#16777")
    # 每行都带人员标注：done/doing 标完成者（无 finishedBy 回退指派人），设置弹窗全集
    assert "[完成者:陈文博]" in text
    assert "[完成者:张三]" in text
    assert result["available_versions"] == ["V4.0.4.0"]
    assert set(result["available_persons"]) == {"陈文博", "张三"}


def test_weekly_task_report_finisher_and_filters(db_session):
    from app.models.zentao_task_mirror import ZentaoTaskMirror
    from app.utils.time_utils import local_now

    major = _create_major(db_session, "V4.0.5.0")
    major.zentao_execution_id = 2200
    db_session.commit()
    today = local_now().date()
    monday = today - timedelta(days=today.weekday())

    def mk(task_id, name, *, status, person, finisher=None, start=None):
        row = ZentaoTaskMirror(
            task_id=task_id, execution_id=2200, execution_name_cache="V4.0.5.0",
            parent=0, is_parent=0, name=name, type="test", status=status,
            assigned_to="acct_" + person, assigned_to_realname=person,
            finished_by=("acct_" + finisher) if finisher else None,
            finished_by_realname=finisher,
            est_started=start,
            real_started=datetime.combine(start, datetime.min.time()) if start and status != "wait" else None,
            finished_date=datetime.combine(start, datetime.min.time()) if status in ("done", "closed") else None,
        )
        db_session.add(row)

    # 完成后被流转：assigned_to=李四（下一环节），finishedBy=王五（真正完成者）
    mk(20001, "已完成任务", status="done", person="李四", finisher="王五", start=monday)
    # 未开始：显示指派人
    mk(20002, "未开始任务", status="wait", person="赵六", start=monday + timedelta(days=2))
    db_session.commit()

    svc = ReportService(db_session)
    full = svc.weekly_task_report()
    # done 显示完成者王五（而非流转后的指派人李四）；wait 显示指派人
    assert "[完成者:王五]" in full["text"]
    assert "[指派:赵六]" in full["text"]
    assert "王五" in full["available_persons"] and "赵六" in full["available_persons"]

    # 人员筛选：只勾王五 → 赵六的任务不导出
    picked = svc.weekly_task_report(persons=["王五"])
    assert "t#20001" in picked["text"]
    assert "t#20002" not in picked["text"]
    # 全集清单不受筛选影响（弹窗数据源）
    assert "赵六" in picked["available_persons"]

    # 版本筛选：勾一个不存在的版本 → 空
    none = svc.weekly_task_report(versions=["不存在的版本"])
    assert "本周暂无任务活动记录" in none["text"]


def test_bug_source_distribution_includes_field_test(db_session):
    user = _create_user(db_session, "report_user7")
    major = _create_major(db_session, "V7400")
    minor = _create_minor(db_session, major.id, "V7400.1")

    ft = FieldTestService(db_session)
    ft.create_record(
        major_version_id=major.id,
        minor_version_id=minor.id,
        purpose_type=FieldTestPurposeType.FEATURE,
        requirement_id=None,
        test_content="外业功能点",
        start_time=datetime(2026, 1, 10, 10, 0, 0),
        end_time=datetime(2026, 1, 10, 11, 0, 0),
        result_status=FieldTestResultStatus.FAILED,
        bug_ids=["b#7401"],
        notes=None,
        actor=user,
    )

    service = ReportService(db_session)
    # _attach_bugs 创建的 BugTracking.created_at = local_now()，因此查询窗口必须覆盖今天，
    # 否则在 2026-02 之后跑这条测试就会得 0。
    result = service.summary(date(2026, 1, 1), date.today() + timedelta(days=1), user)
    source_map = {x["source_type"]: x["count"] for x in result["bug_source_dist"]}
    assert source_map.get("field_test", 0) >= 1
