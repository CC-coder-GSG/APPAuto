from app.models import BugSourceType, BugTracking, FeedbackStatus, Requirement, RequirementStatus, User, UserRole, Version, VersionType
from app.services.feedback_service import FeedbackService


def _create_major(db_session, no: str = "V7000") -> Version:
    row = Version(version_no=no, version_type=VersionType.MAJOR)
    db_session.add(row)
    db_session.commit()
    db_session.refresh(row)
    return row


def _create_minor(db_session, parent_id: int, no: str = "V7000.1") -> Version:
    row = Version(version_no=no, version_type=VersionType.MINOR, parent_id=parent_id)
    db_session.add(row)
    db_session.commit()
    db_session.refresh(row)
    return row


def _create_user(db_session, username: str, role: UserRole = UserRole.USER) -> User:
    row = User(username=username, password_hash=User.hash_password("pass123"), role=role)
    db_session.add(row)
    db_session.commit()
    db_session.refresh(row)
    return row


def _create_requirement(db_session, major_id: int) -> Requirement:
    row = Requirement(zentao_req_id="r#7001", title="反馈关联需求", major_version_id=major_id, status=RequirementStatus.PENDING)
    db_session.add(row)
    db_session.commit()
    db_session.refresh(row)
    return row


def test_feedback_no_is_prefixed_and_unique(db_session):
    major = _create_major(db_session)
    minor = _create_minor(db_session, major.id)
    creator = _create_user(db_session, "feedback_creator")
    service = FeedbackService(db_session)

    created = service.create_feedback(
        creator=creator,
        feedback_no_num="12",
        major_version_id=major.id,
        minor_version_id=minor.id,
        summary="首条反馈",
    )
    detail = service.get_feedback_detail(created["id"])
    assert detail["feedback_no"] == "f#12"

    try:
        service.create_feedback(
            creator=creator,
            feedback_no_num="12",
            major_version_id=major.id,
            minor_version_id=minor.id,
            summary="重复编号",
        )
        assert False, "duplicate feedback_no should fail"
    except Exception as exc:  # noqa: BLE001
        assert "已存在" in str(exc)


def test_feedback_status_transition_guard_for_non_admin(db_session):
    major = _create_major(db_session, "V7001")
    minor = _create_minor(db_session, major.id, "V7001.1")
    creator = _create_user(db_session, "feedback_creator2")
    assignee = _create_user(db_session, "feedback_handler2")
    admin = _create_user(db_session, "feedback_admin", role=UserRole.ADMIN)
    service = FeedbackService(db_session)

    created = service.create_feedback(
        creator=creator,
        feedback_no_num="701",
        major_version_id=major.id,
        minor_version_id=minor.id,
        summary="状态流转反馈",
    )
    service.assign_feedback(feedback_id=created["id"], assignee_id=assignee.id, status=FeedbackStatus.PROCESSING, actor=admin)

    # 非管理员不可直接关闭
    try:
        service.update_status(feedback_id=created["id"], status=FeedbackStatus.CLOSED, actor=assignee)
        assert False, "non-admin close should fail"
    except Exception as exc:  # noqa: BLE001
        assert "非法状态流转" in str(exc) or "仅管理员" in str(exc)


def test_feedback_create_bug_and_link(db_session):
    major = _create_major(db_session, "V7002")
    minor = _create_minor(db_session, major.id, "V7002.1")
    creator = _create_user(db_session, "feedback_creator3")
    service = FeedbackService(db_session)

    created = service.create_feedback(
        creator=creator,
        feedback_no_num="702",
        major_version_id=major.id,
        minor_version_id=minor.id,
        summary="新建并关联 bug",
    )
    result = service.create_bug_and_link(feedback_id=created["id"], bug_no="b#7021", actor=creator)
    assert result["message"] == "已新建 Bug 并关联"

    linked = service.list_linked_bugs(created["id"])
    assert len(linked) == 1
    assert linked[0]["bug_id"] == "b#7021"

    bug = db_session.query(BugTracking).filter(BugTracking.bug_id == "b#7021").first()
    assert bug is not None
    assert bug.source_type == BugSourceType.MANUAL

