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


def test_submit_retest_fail_conclusion_is_retired(db_session):
    """2026-07 改版：打回按钮下线，显式提交 retest_passed=False 一律拒绝。"""
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
        assert "已下线" in exc.detail


def test_activated_bug_blocks_pass_and_dismiss_restores(db_session):
    """复测激活 Bug → 结论自动未通过、阻止通过；标记误报后恢复可通过。"""
    major = _create_major(db_session, "V5001")
    minor = _create_minor(db_session, major.id, "V5001.1")
    owner = _create_user(db_session, "owner_r2")
    retester = _create_user(db_session, "retester_r2")
    req = _create_requirement(db_session, major.id, owner.id)
    bug = BugTracking(
        major_version_id=major.id,
        requirement_id=req.id,
        source_type=BugSourceType.MANUAL,
        bug_id="b#5001",
        found_minor_version_id=minor.id,
        closed=True,
        created_by_id=owner.id,
    )
    db_session.add(bug)
    db_session.commit()
    db_session.refresh(bug)

    service = RetestService(db_session)
    # 复测激活 → 需求自动转为复测未通过
    service.mark_bug_activated(bug.id, req.id, retester)
    db_session.refresh(req)
    db_session.refresh(bug)
    assert bug.retest_activated is True
    assert bug.closed is False
    assert req.retest_completed is True
    assert req.retest_passed is False
    assert service.compute_conclusion(req) == "failed"

    # 存在有效复测问题时不允许点通过
    try:
        service.submit_retest(req.id, retest_completed=True, retest_passed=True, retest_minor_version_id=minor.id, current_user=retester)
        assert False, "expected HTTPException"
    except HTTPException as exc:
        assert exc.status_code == 400
        assert "取消复测结论" in exc.detail

    # 标记误报 → 全部问题失效 → 系统判定复测通过
    service.toggle_bug_dismissed(bug.id, req.id, True, retester)
    db_session.refresh(req)
    assert req.retest_passed is True
    assert service.compute_conclusion(req) == "passed"

    # 此时点通过也不再被拦截
    result = service.submit_retest(req.id, retest_completed=True, retest_passed=True, retest_minor_version_id=minor.id, current_user=retester)
    assert result["message"] == "Retest status updated"


def test_collected_bug_by_other_marks_failed_but_owner_own_bug_does_not(db_session):
    """测后归集：提出人≠原测试人才算复测问题；原测试人自己补录的不算。"""
    from datetime import timedelta

    from app.utils.time_utils import local_now

    major = _create_major(db_session, "V5005")
    minor = _create_minor(db_session, major.id, "V5005.1")
    owner = _create_user(db_session, "owner_r5")
    owner.zentao_account = "owner_acct"
    retester = _create_user(db_session, "retester_r5")
    req = _create_requirement(db_session, major.id, owner.id)
    req.zentao_story_id = 601
    req.test_completed_at = local_now() - timedelta(days=1)
    db_session.commit()

    service = RetestService(db_session)
    # 原测试人自己在测后提出的 Bug：不算复测问题
    db_session.add(BugTracking(
        major_version_id=major.id, source_type=BugSourceType.MANUAL, bug_id="b#5601",
        zentao_bug_id="5601", zentao_story_id=601,
        zentao_opened_at=local_now(), zentao_opened_by_account="owner_acct", zentao_opened_by_name="原测试人",
    ))
    db_session.commit()
    assert service.compute_conclusion(req) == "pending"

    # 他人在测后提出并归集的 Bug：自动判定复测未通过
    db_session.add(BugTracking(
        major_version_id=major.id, source_type=BugSourceType.MANUAL, bug_id="b#5602",
        zentao_bug_id="5602", zentao_story_id=601,
        zentao_opened_at=local_now(), zentao_opened_by_account="other_acct", zentao_opened_by_name="别人",
    ))
    db_session.commit()
    problems = service.compute_retest_problems(req)
    assert [p for p in problems if p["kind"] == "collected" and not p["dismissed"]]
    assert service.compute_conclusion(req) == "failed"


def test_pass_then_activation_auto_downgrades(db_session):
    """先通过、后激活 Bug → 聚合自动降级为复测未通过（问题覆盖通过记录）。"""
    major = _create_major(db_session, "V5006")
    minor = _create_minor(db_session, major.id, "V5006.1")
    owner = _create_user(db_session, "owner_r6")
    retester = _create_user(db_session, "retester_r6")
    req = _create_requirement(db_session, major.id, owner.id)
    bug = BugTracking(
        major_version_id=major.id, requirement_id=req.id, source_type=BugSourceType.MANUAL,
        bug_id="b#5603", found_minor_version_id=minor.id, closed=True,
    )
    db_session.add(bug)
    db_session.commit()
    db_session.refresh(bug)

    service = RetestService(db_session)
    service.submit_retest(req.id, retest_completed=True, retest_passed=True, retest_minor_version_id=minor.id, current_user=retester)
    db_session.refresh(req)
    assert req.retest_passed is True

    service.mark_bug_activated(bug.id, req.id, retester)
    db_session.refresh(req)
    assert req.retest_passed is False
    assert service.compute_conclusion(req) == "failed"
    # 通过记录仍保留（撤销激活/误报后可自动回到通过）
    records = db_session.query(RequirementRetestRecord).filter(RequirementRetestRecord.requirement_id == req.id).all()
    assert len(records) == 1 and records[0].passed


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
    # 历史打回记录（passed=False）保留原义 → 未通过
    assert "[未通过]" in message
    assert "retester_r3" in message  # 通过行列出复测人


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
