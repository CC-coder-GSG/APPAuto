from datetime import date, datetime

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
    assert result["overview"]["created_cases"] == 1
    assert result["overview"]["created_bugs"] == 1


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
    result = service.summary(date(2026, 1, 1), date(2026, 1, 31), user)
    source_map = {x["source_type"]: x["count"] for x in result["bug_source_dist"]}
    assert source_map.get("field_test", 0) >= 1
