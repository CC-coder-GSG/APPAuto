from fastapi import HTTPException

from app.models import BugSourceType, BugTracking, Requirement, RequirementStatus, User, UserRole, Version, VersionType
from app.services.bug_service import BugService


def _create_major(db_session, version_no: str = "V4000") -> Version:
    major = Version(version_no=version_no, version_type=VersionType.MAJOR)
    db_session.add(major)
    db_session.commit()
    db_session.refresh(major)
    return major


def _create_user(db_session, username: str = "bug_user") -> User:
    user = User(username=username, password_hash=User.hash_password("pass123"), role=UserRole.USER)
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


def _create_requirement(db_session, major_id: int, owner_id: int | None = None, test_completed: bool = False) -> Requirement:
    req = Requirement(
        zentao_req_id="r#4001",
        title="Bug需求",
        major_version_id=major_id,
        owner_id=owner_id,
        test_completed=test_completed,
        status=RequirementStatus.TEST_DONE if test_completed else RequirementStatus.PENDING,
    )
    db_session.add(req)
    db_session.commit()
    db_session.refresh(req)
    return req


def test_create_execution_bug_blocks_non_retest_when_test_completed(db_session):
    major = _create_major(db_session)
    user = _create_user(db_session)
    req = _create_requirement(db_session, major.id, owner_id=user.id, test_completed=True)
    service = BugService(db_session)

    try:
        service.create_execution_bug(
            bug_id="b#4001",
            minor_version_id=1,
            requirement_id=req.id,
            source_type=BugSourceType.MANUAL,
            actor=user,
        )
        assert False, "expected HTTPException"
    except HTTPException as exc:
        assert exc.status_code == 400
        assert "测试已封板" in exc.detail


def test_create_execution_bug_allows_retest_when_test_completed(db_session):
    major = _create_major(db_session, "V4001")
    user = _create_user(db_session, "bug_user2")
    req = _create_requirement(db_session, major.id, owner_id=user.id, test_completed=True)
    service = BugService(db_session)

    result = service.create_execution_bug(
        bug_id="b#4002",
        minor_version_id=1,
        requirement_id=req.id,
        source_type=BugSourceType.RETEST,
        actor=user,
    )

    bug = db_session.query(BugTracking).filter(BugTracking.id == result["id"]).first()
    assert bug is not None
    assert bug.source_type == BugSourceType.RETEST


def test_dispatch_bug_updates_target_user(db_session):
    major = _create_major(db_session, "V4002")
    creator = _create_user(db_session, "creator")
    target = _create_user(db_session, "target")
    req = _create_requirement(db_session, major.id)
    bug = BugTracking(
        major_version_id=major.id,
        requirement_id=req.id,
        source_type=BugSourceType.MANUAL,
        bug_id="b#4003",
        found_minor_version_id=1,
        created_by_id=creator.id,
    )
    db_session.add(bug)
    db_session.commit()
    db_session.refresh(bug)

    service = BugService(db_session)
    result, user, updated_bug = service.dispatch_bug(bug.id, target.id, actor_id=creator.id)

    assert result["message"] == "特派成功"
    assert user.id == target.id
    assert updated_bug.dispatched_to_id == target.id
