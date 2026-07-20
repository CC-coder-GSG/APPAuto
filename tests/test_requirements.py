from app.models import Requirement, RequirementStatus, TestExecution, User, UserRole, Version, VersionType
from app.services.requirement_service import RequirementService


def _create_major(db_session, version_no: str = "V1000") -> Version:
    major = Version(version_no=version_no, version_type=VersionType.MAJOR)
    db_session.add(major)
    db_session.commit()
    db_session.refresh(major)
    return major


def _create_minor(db_session, parent_id: int, version_no: str = "V1000.1") -> Version:
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


def _create_requirement(db_session, major_id: int, req_no: str = "r#1001") -> Requirement:
    req = Requirement(zentao_req_id=req_no, title="需求A", major_version_id=major_id, status=RequirementStatus.PENDING)
    db_session.add(req)
    db_session.commit()
    db_session.refresh(req)
    return req


def test_recalculate_requirement_status_progression(db_session):
    major = _create_major(db_session)
    req = _create_requirement(db_session, major.id)
    service = RequirementService(db_session)

    service.recalculate_requirement_status(req)
    assert req.status == RequirementStatus.PENDING

    req.owner_id = 1
    service.recalculate_requirement_status(req)
    assert req.status == RequirementStatus.ASSIGNED

    req.case_completed = True
    service.recalculate_requirement_status(req)
    assert req.status == RequirementStatus.CASE_DONE

    req.test_completed = True
    service.recalculate_requirement_status(req)
    assert req.status == RequirementStatus.TEST_DONE


def test_assign_and_publish_resets_case_and_test_status(db_session):
    major = _create_major(db_session)
    old_user = _create_user(db_session, "old_owner")
    new_user = _create_user(db_session, "new_owner")
    req = _create_requirement(db_session, major.id, "r#1002")
    req.owner_id = old_user.id
    req.case_completed = True
    req.test_completed = True
    req.status = RequirementStatus.TEST_DONE
    db_session.commit()

    service = RequirementService(db_session)
    result = service.assign_and_publish(
        major.id,
        [{"requirement_id": req.id, "owner_id": new_user.id}],
        {old_user.id: old_user.username, new_user.id: new_user.username},
        actor_id=999,
    )
    db_session.refresh(req)

    assert req.owner_id == new_user.id
    assert req.case_completed is False
    assert req.test_completed is False
    assert req.status == RequirementStatus.ASSIGNED
    assert len(result["change_msgs"]) == 1


def test_patch_requirement_status_updates_flags_and_status(db_session):
    major = _create_major(db_session)
    owner = _create_user(db_session, "owner1")
    req = _create_requirement(db_session, major.id, "r#1003")
    req.owner_id = owner.id
    db_session.commit()

    service = RequirementService(db_session)
    result = service.patch_requirement_status(req.id, owner, case_completed=True, test_completed=False)
    db_session.refresh(req)

    assert result["case_completed"] is True
    assert req.status == RequirementStatus.CASE_DONE


def test_assign_and_publish_change_message_is_readable(db_session):
    major = _create_major(db_session, "V2000")
    old_user = _create_user(db_session, "alice")
    new_user = _create_user(db_session, "bob")
    req = _create_requirement(db_session, major.id, "r#2001")
    req.owner_id = old_user.id
    db_session.commit()

    service = RequirementService(db_session)
    result = service.assign_and_publish(
        major.id,
        [{"requirement_id": req.id, "owner_id": new_user.id}],
        {old_user.id: old_user.username, new_user.id: new_user.username},
    )

    assert result["change_msgs"] == [f"> **{req.zentao_req_id}** ({req.title}) 已从 @alice 移交给了 @bob"]



def test_progress_messages_are_readable(db_session):
    major = _create_major(db_session, "V3000")
    user = _create_user(db_session, "tester")
    req = _create_requirement(db_session, major.id, "r#3001")
    req.owner_id = user.id
    req.case_completed = True
    req.test_completed = True
    db_session.commit()

    service = RequirementService(db_session)
    case_msg = service.build_case_progress_message(major.id, user)
    test_msg = service.build_test_progress_message(major.id, major.id, user)

    assert "### 需求测试进度" in case_msg
    assert "当前人员填写用例" in case_msg
    assert "需求用例已完成" in case_msg
    assert "### 需求测试进度" in test_msg
    assert "当前人员已完成测试需求" in test_msg
    assert "需求测试未完成" in test_msg


def test_upsert_test_execution_sets_requirement_completed(db_session):
    major = _create_major(db_session, "V4000")
    minor = _create_minor(db_session, major.id, "V4000.1")
    owner = _create_user(db_session, "owner_exec_1")
    req = _create_requirement(db_session, major.id, "r#4001")
    req.owner_id = owner.id
    db_session.commit()

    service = RequirementService(db_session)
    result = service.upsert_test_execution(
        requirement_id=req.id,
        minor_version_id=minor.id,
        bug_id=None,
        source_case_id=None,
        result_status="passed",
        notes="第一次执行",
        test_completed=True,
        actor_id=owner.id,
    )

    db_session.refresh(req)
    execution = db_session.query(TestExecution).filter(TestExecution.requirement_id == req.id, TestExecution.minor_version_id == minor.id).first()
    assert req.test_completed is True
    assert execution is not None
    assert execution.result_status == "passed"
    assert result["test_completed"] is True
    assert result["minor_version_id"] == minor.id


def test_upsert_test_execution_invalid_result_status_rejected(db_session):
    major = _create_major(db_session, "V4100")
    minor = _create_minor(db_session, major.id, "V4100.1")
    owner = _create_user(db_session, "owner_exec_2")
    req = _create_requirement(db_session, major.id, "r#4101")
    req.owner_id = owner.id
    db_session.commit()

    service = RequirementService(db_session)
    try:
        service.upsert_test_execution(
            requirement_id=req.id,
            minor_version_id=minor.id,
            bug_id=None,
            source_case_id=None,
            result_status="unknown",
            notes=None,
            test_completed=True,
            actor_id=owner.id,
        )
        assert False, "expected HTTPException"
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 400


def test_upsert_test_execution_invalid_minor_rejected(db_session):
    major = _create_major(db_session, "V4200")
    owner = _create_user(db_session, "owner_exec_3")
    req = _create_requirement(db_session, major.id, "r#4201")
    req.owner_id = owner.id
    db_session.commit()

    service = RequirementService(db_session)
    try:
        service.upsert_test_execution(
            requirement_id=req.id,
            minor_version_id=999999,
            bug_id=None,
            source_case_id=None,
            result_status="passed",
            notes=None,
            test_completed=True,
            actor_id=owner.id,
        )
        assert False, "expected HTTPException"
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 400


def test_upsert_test_execution_major_minor_mismatch_rejected(db_session):
    major_a = _create_major(db_session, "V4300")
    major_b = _create_major(db_session, "V4301")
    minor_b = _create_minor(db_session, major_b.id, "V4301.1")
    owner = _create_user(db_session, "owner_exec_4")
    req = _create_requirement(db_session, major_a.id, "r#4301")
    req.owner_id = owner.id
    db_session.commit()

    service = RequirementService(db_session)
    try:
        service.upsert_test_execution(
            requirement_id=req.id,
            minor_version_id=minor_b.id,
            bug_id=None,
            source_case_id=None,
            result_status="passed",
            notes=None,
            test_completed=True,
            actor_id=owner.id,
        )
        assert False, "expected HTTPException"
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 400


def test_upsert_test_execution_permission_rejected_for_non_owner(db_session):
    major = _create_major(db_session, "V4400")
    minor = _create_minor(db_session, major.id, "V4400.1")
    owner = _create_user(db_session, "owner_exec_5")
    other = _create_user(db_session, "other_exec_5")
    req = _create_requirement(db_session, major.id, "r#4401")
    req.owner_id = owner.id
    db_session.commit()

    service = RequirementService(db_session)
    try:
        service.upsert_test_execution(
            requirement_id=req.id,
            minor_version_id=minor.id,
            bug_id=None,
            source_case_id=None,
            result_status="passed",
            notes=None,
            test_completed=True,
            actor_id=other.id,
        )
        assert False, "expected HTTPException"
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 403


def test_update_test_notes_owner_allowed(db_session):
    major = _create_major(db_session, "V4500")
    owner = _create_user(db_session, "owner_notes_1")
    req = _create_requirement(db_session, major.id, "r#4501")
    req.owner_id = owner.id
    db_session.commit()

    service = RequirementService(db_session)
    result = service.update_test_notes(req.id, "核心场景+边界校验", owner)
    db_session.refresh(req)

    assert result["message"] == "测试要点保存成功"
    assert req.test_notes == "核心场景+边界校验"
    assert req.test_notes_updated_by_id == owner.id
    assert req.test_notes_updated_at is not None


def test_update_test_notes_admin_allowed(db_session):
    major = _create_major(db_session, "V4600")
    owner = _create_user(db_session, "owner_notes_2")
    admin = User(username="admin_notes", password_hash=User.hash_password("pass123"), role=UserRole.ADMIN)
    db_session.add(admin)
    db_session.commit()
    db_session.refresh(admin)
    req = _create_requirement(db_session, major.id, "r#4601")
    req.owner_id = owner.id
    db_session.commit()

    service = RequirementService(db_session)
    service.update_test_notes(req.id, "管理员补充测试策略", admin)
    db_session.refresh(req)
    assert req.test_notes == "管理员补充测试策略"
    assert req.test_notes_updated_by_id == admin.id


def test_update_test_notes_other_user_allowed_for_review_collaboration(db_session, monkeypatch):
    major = _create_major(db_session, "V4700")
    owner = _create_user(db_session, "owner_notes_3")
    other = _create_user(db_session, "other_notes_3")
    req = _create_requirement(db_session, major.id, "r#4701")
    req.owner_id = owner.id
    db_session.commit()

    published = []
    monkeypatch.setattr(
        "app.services.requirement_service.sse_publish",
        lambda event, payload, channels=None: published.append((event, payload, channels)),
    )

    service = RequirementService(db_session)
    result = service.update_test_notes(
        req.id,
        "协作补充边界场景",
        other,
        test_notes_html="<b>协作补充</b>边界场景",
    )
    db_session.refresh(req)

    assert result["message"] == "测试要点保存成功"
    assert req.test_notes == "协作补充边界场景"
    assert req.test_notes_html == "<b>协作补充</b>边界场景"
    assert req.test_notes_updated_by_id == other.id
    assert published == [
        (
            "requirement_test_notes_updated",
            {
                "requirement_id": req.id,
                "major_version_id": major.id,
                "updated_by_id": other.id,
            },
            ["global"],
        )
    ]


def test_list_requirements_contains_test_notes_fields(db_session):
    major = _create_major(db_session, "V4800")
    owner = _create_user(db_session, "owner_notes_4")
    req = _create_requirement(db_session, major.id, "r#4801")
    req.owner_id = owner.id
    req.test_notes = "需要重点关注边界输入"
    req.test_notes_updated_by_id = owner.id
    req.test_notes_updated_at = req.updated_at
    db_session.commit()

    service = RequirementService(db_session)
    rows = service.list_requirements(major.id)
    assert len(rows) == 1
    assert rows[0]["test_notes"] == "需要重点关注边界输入"
    assert "test_notes_updated_at" in rows[0]
    assert rows[0]["test_notes_updated_by_name"] == owner.username
