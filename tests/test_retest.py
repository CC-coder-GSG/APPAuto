from fastapi import HTTPException

from app.models import BugSourceType, BugTracking, Requirement, RequirementRetestRecord, RequirementStatus, User, UserRole, Version, VersionType
from app.services.retest_service import RetestService


def _create_major(db_session, version_no: str = "V5000") -> Version:
    major = Version(version_no=version_no, version_type=VersionType.MAJOR)
    db_session.add(major)
    db_session.commit()
    db_session.refresh(major)
    return major


def _create_minor(db_session, parent_id: int, version_no: str = "V5000.1") -> Version:
    minor = Version(version_no=version_no, version_type=VersionType.MINOR, parent_id=parent_id)
    db_session.add(minor)
    db_session.commit()
    db_session.refresh(minor)
    return minor


def _create_user(db_session, username: str) -> User:
    user = User(username=username, password_hash=User.hash_password("pass123"), role=UserRole.USER)
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


def _create_requirement(db_session, major_id: int, owner_id: int) -> Requirement:
    req = Requirement(
        zentao_req_id="r#5001",
        title="复测需求",
        major_version_id=major_id,
        owner_id=owner_id,
        test_completed=True,
        status=RequirementStatus.TEST_DONE,
    )
    db_session.add(req)
    db_session.commit()
    db_session.refresh(req)
    return req


def test_submit_retest_rejects_without_evidence(db_session):
    major = _create_major(db_session)
    minor = _create_minor(db_session, major.id)
    owner = _create_user(db_session, "owner_r")
    retester = _create_user(db_session, "retester_r")
    req = _create_requirement(db_session, major.id, owner.id)
    service = RetestService(db_session)

    try:
        service.submit_retest(
            req.id,
            retest_completed=True,
            retest_passed=False,
            retest_minor_version_id=minor.id,
            current_user=retester,
        )
        assert False, "expected HTTPException"
    except HTTPException as exc:
        assert exc.status_code == 400
        assert "打回无效" in exc.detail


def test_submit_retest_accepts_with_failed_old_bug(db_session):
    major = _create_major(db_session, "V5001")
    minor = _create_minor(db_session, major.id, "V5001.1")
    owner = _create_user(db_session, "owner_r2")
    retester = _create_user(db_session, "retester_r2")
    req = _create_requirement(db_session, major.id, owner.id)
    db_session.add(BugTracking(
        major_version_id=major.id,
        requirement_id=req.id,
        source_type=BugSourceType.MANUAL,
        bug_id="b#5001",
        found_minor_version_id=minor.id,
        is_retest_failed=True,
        created_by_id=owner.id,
    ))
    db_session.commit()

    service = RetestService(db_session)
    result = service.submit_retest(
        req.id,
        retest_completed=True,
        retest_passed=False,
        retest_minor_version_id=minor.id,
        current_user=retester,
    )
    db_session.refresh(req)

    assert result["message"] == "Retest status updated"
    assert req.retest_completed is True
    assert req.retest_passed is False
    assert req.retested_by_id == retester.id


def test_build_retest_push_message_contains_passed_and_failed_rows(db_session):
    major = _create_major(db_session, "V5002")
    minor = _create_minor(db_session, major.id, "V5002.1")
    owner = _create_user(db_session, "owner_r3")
    retester = _create_user(db_session, "retester_r3")

    req1 = Requirement(zentao_req_id="r#5002", title="需求1", major_version_id=major.id, owner_id=owner.id, test_completed=True, status=RequirementStatus.TEST_DONE)
    req2 = Requirement(zentao_req_id="r#5003", title="需求2", major_version_id=major.id, owner_id=owner.id, test_completed=True, status=RequirementStatus.TEST_DONE)
    db_session.add_all([req1, req2])
    db_session.commit()
    db_session.refresh(req1)
    db_session.refresh(req2)
    db_session.add_all([
        RequirementRetestRecord(requirement_id=req1.id, user_id=retester.id, passed=True, minor_version_id=minor.id),
        RequirementRetestRecord(requirement_id=req2.id, user_id=retester.id, passed=False, minor_version_id=minor.id),
    ])
    db_session.commit()

    service = RetestService(db_session)
    message, count = service.build_retest_push_message(major.id, retester)

    assert count == 2
    assert "复测结果专项通报" in message
    assert "[通过]" in message
    assert "[打回]" in message


def test_retest_status_is_per_user(db_session):
    """同一需求被多人复测时，每个人各自记录；通过/打回不互相覆盖显示。"""
    major = _create_major(db_session, "V5004")
    minor = _create_minor(db_session, major.id, "V5004.1")
    owner = _create_user(db_session, "owner_r4")
    retester_a = _create_user(db_session, "retester_a")
    retester_b = _create_user(db_session, "retester_b")
    req = _create_requirement(db_session, major.id, owner.id)
    db_session.refresh(req)
    req.zentao_req_id = "r#5004"
    db_session.commit()

    service = RetestService(db_session)
    # A 复测通过（无未闭环问题）
    service.submit_retest(req.id, retest_completed=True, retest_passed=True, retest_minor_version_id=minor.id, current_user=retester_a)
    # B 也复测通过
    service.submit_retest(req.id, retest_completed=True, retest_passed=True, retest_minor_version_id=minor.id, current_user=retester_b)

    records = db_session.query(RequirementRetestRecord).filter(RequirementRetestRecord.requirement_id == req.id).all()
    assert len(records) == 2
    assert {r.user_id for r in records} == {retester_a.id, retester_b.id}

    # 共享聚合字段仍被维护（供状态机/报表）
    db_session.refresh(req)
    assert req.retest_completed is True
