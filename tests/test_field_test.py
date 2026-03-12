from datetime import date, datetime, timedelta

import pytest
from fastapi import HTTPException

from app.models import (
    BugSourceType,
    BugTracking,
    FieldTestPurposeType,
    FieldTestRecord,
    FieldTestResultStatus,
    Requirement,
    RequirementStatus,
    User,
    UserRole,
    Version,
    VersionType,
)
from app.services.field_test_service import FieldTestService


def _create_user(db_session, username: str, role: UserRole = UserRole.USER, is_team_member: bool = True) -> User:
    user = User(username=username, password_hash=User.hash_password('pass123'), role=role, is_team_member=is_team_member)
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


def _create_major(db_session, no='VFT1') -> Version:
    major = Version(version_no=no, version_type=VersionType.MAJOR)
    db_session.add(major)
    db_session.commit()
    db_session.refresh(major)
    return major


def _create_minor(db_session, parent_id: int, no='VFT1.1') -> Version:
    minor = Version(version_no=no, version_type=VersionType.MINOR, parent_id=parent_id)
    db_session.add(minor)
    db_session.commit()
    db_session.refresh(minor)
    return minor


def _create_requirement(db_session, major_id: int, rid='r#ft01') -> Requirement:
    req = Requirement(zentao_req_id=rid, title='外业需求', major_version_id=major_id, status=RequirementStatus.ASSIGNED)
    db_session.add(req)
    db_session.commit()
    db_session.refresh(req)
    return req


def test_create_field_test_failed_with_multi_bugs_and_source_type(db_session):
    user = _create_user(db_session, 'ft_user1')
    major = _create_major(db_session, 'VFT10')
    minor = _create_minor(db_session, major.id, 'VFT10.1')
    req = _create_requirement(db_session, major.id, 'r#ft10')

    service = FieldTestService(db_session)
    result = service.create_record(
        major_version_id=major.id,
        minor_version_id=minor.id,
        purpose_type=FieldTestPurposeType.REQUIREMENT,
        requirement_id=req.id,
        test_content=None,
        start_time=datetime.utcnow() - timedelta(hours=2),
        end_time=datetime.utcnow() - timedelta(hours=1),
        result_status=FieldTestResultStatus.FAILED,
        bug_ids=['b#91001', 'b#91002', 'b#91001'],
        notes='外业失败',
        actor=user,
    )

    assert result['message'] == '外业测试记录已创建'
    rows = db_session.query(BugTracking).filter(BugTracking.source_type == BugSourceType.FIELD_TEST).all()
    assert len(rows) == 2
    assert all(b.requirement_id == req.id for b in rows)


def test_feature_mode_requires_content(db_session):
    user = _create_user(db_session, 'ft_user2')
    major = _create_major(db_session, 'VFT20')
    minor = _create_minor(db_session, major.id, 'VFT20.1')

    service = FieldTestService(db_session)
    with pytest.raises(HTTPException) as exc:
        service.create_record(
            major_version_id=major.id,
            minor_version_id=minor.id,
            purpose_type=FieldTestPurposeType.FEATURE,
            requirement_id=None,
            test_content=' ',
            start_time=datetime.utcnow() - timedelta(minutes=90),
            end_time=datetime.utcnow() - timedelta(minutes=30),
            result_status=FieldTestResultStatus.PASSED,
            bug_ids=[],
            notes=None,
            actor=user,
        )
    assert exc.value.status_code == 400


def test_update_field_test_forbidden_for_non_owner(db_session):
    owner = _create_user(db_session, 'ft_owner')
    other = _create_user(db_session, 'ft_other')
    major = _create_major(db_session, 'VFT30')
    minor = _create_minor(db_session, major.id, 'VFT30.1')
    req = _create_requirement(db_session, major.id, 'r#ft30')

    service = FieldTestService(db_session)
    created = service.create_record(
        major_version_id=major.id,
        minor_version_id=minor.id,
        purpose_type=FieldTestPurposeType.REQUIREMENT,
        requirement_id=req.id,
        test_content=None,
        start_time=datetime.utcnow() - timedelta(hours=3),
        end_time=datetime.utcnow() - timedelta(hours=2),
        result_status=FieldTestResultStatus.PASSED,
        bug_ids=[],
        notes=None,
        actor=owner,
    )
    rid = created['record']['id']

    with pytest.raises(HTTPException) as exc:
        service.update_record(
            rid,
            major_version_id=major.id,
            minor_version_id=minor.id,
            purpose_type=FieldTestPurposeType.REQUIREMENT,
            requirement_id=req.id,
            test_content=None,
            start_time=datetime.utcnow() - timedelta(hours=2),
            end_time=datetime.utcnow() - timedelta(hours=1),
            result_status=FieldTestResultStatus.PASSED,
            bug_ids=[],
            notes='x',
            actor=other,
        )
    assert exc.value.status_code == 403


def test_report_stats_scope_personal_and_admin_all_users(db_session):
    admin = _create_user(db_session, 'ft_admin', role=UserRole.ADMIN)
    u1 = _create_user(db_session, 'ft_u1')
    u2 = _create_user(db_session, 'ft_u2')
    major = _create_major(db_session, 'VFT40')
    minor = _create_minor(db_session, major.id, 'VFT40.1')

    service = FieldTestService(db_session)
    now = datetime.utcnow()
    for actor, mins in [(u1, 30), (u2, 60)]:
        service.create_record(
            major_version_id=major.id,
            minor_version_id=minor.id,
            purpose_type=FieldTestPurposeType.FEATURE,
            requirement_id=None,
            test_content='功能点',
            start_time=now - timedelta(minutes=mins + 30),
            end_time=now - timedelta(minutes=30),
            result_status=FieldTestResultStatus.PASSED,
            bug_ids=[],
            notes=None,
            actor=actor,
        )

    personal = service.report_stats(
        start_date=date.today() - timedelta(days=1),
        end_date=date.today(),
        current_user=u1,
    )
    assert personal['mode'] == 'personal'
    assert personal['overview']['total_records'] == 1

    all_users = service.report_stats(
        start_date=date.today() - timedelta(days=1),
        end_date=date.today(),
        current_user=admin,
        user_id=0,
    )
    assert all_users['mode'] == 'all_users'
    assert all_users['overview']['total_records'] >= 2
    assert len(all_users['by_user']) >= 2


def test_list_records_paged_and_unlink_bug(db_session):
    user = _create_user(db_session, "ft_user_page")
    major = _create_major(db_session, "VFT50")
    minor = _create_minor(db_session, major.id, "VFT50.1")
    req = _create_requirement(db_session, major.id, "r#ft50")
    service = FieldTestService(db_session)

    created = service.create_record(
        major_version_id=major.id,
        minor_version_id=minor.id,
        purpose_type=FieldTestPurposeType.REQUIREMENT,
        requirement_id=req.id,
        test_content=None,
        start_time=datetime.utcnow() - timedelta(hours=2),
        end_time=datetime.utcnow() - timedelta(hours=1),
        result_status=FieldTestResultStatus.FAILED,
        bug_ids=["b#95001", "b#95002"],
        notes="分页与解绑测试",
        actor=user,
    )
    rid = created["record"]["id"]

    paged = service.list_records_paged(current_user=user, page=1, page_size=1)
    assert paged["total"] >= 1
    assert len(paged["items"]) == 1

    detail = service.get_detail(rid, user)
    assert len(detail["bugs"]) == 2
    bug_to_unlink = detail["bugs"][0]["id"]
    unlink_res = service.unlink_bug(rid, bug_to_unlink, user)
    assert unlink_res["message"] == "已解除该 Bug 关联"
    detail2 = service.get_detail(rid, user)
    assert len(detail2["bugs"]) == 1


def test_add_bug_by_no_create_and_idempotent(db_session):
    user = _create_user(db_session, "ft_user_add_bug")
    major = _create_major(db_session, "VFT60")
    minor = _create_minor(db_session, major.id, "VFT60.1")
    req = _create_requirement(db_session, major.id, "r#ft60")
    service = FieldTestService(db_session)
    created = service.create_record(
        major_version_id=major.id,
        minor_version_id=minor.id,
        purpose_type=FieldTestPurposeType.REQUIREMENT,
        requirement_id=req.id,
        test_content=None,
        start_time=datetime.utcnow() - timedelta(hours=2),
        end_time=datetime.utcnow() - timedelta(hours=1),
        result_status=FieldTestResultStatus.PASSED,
        bug_ids=[],
        notes=None,
        actor=user,
    )
    rid = created["record"]["id"]

    r1 = service.add_bug_by_no(rid, "b#96001", user)
    assert r1["created"] is True
    assert "关联" in r1["message"]

    # 再次添加同一个，应该幂等，不重复创建/关联
    r2 = service.add_bug_by_no(rid, "b#96001", user)
    assert r2["created"] is False
    detail = service.get_detail(rid, user)
    assert len([b for b in detail["bugs"] if b["bug_id"] == "b#96001"]) == 1

    bug = db_session.query(BugTracking).filter(BugTracking.bug_id == "b#96001").first()
    assert bug is not None
    assert bug.source_type == BugSourceType.FIELD_TEST


def test_search_and_link_existing_bug(db_session):
    user = _create_user(db_session, "ft_user_link_existing")
    major = _create_major(db_session, "VFT70")
    minor = _create_minor(db_session, major.id, "VFT70.1")
    req = _create_requirement(db_session, major.id, "r#ft70")
    service = FieldTestService(db_session)

    existing_bug = BugTracking(
        major_version_id=major.id,
        requirement_id=req.id,
        source_type=BugSourceType.MANUAL,
        source_ref=None,
        bug_id="b#97001",
        found_minor_version_id=minor.id,
        created_by_id=user.id,
    )
    db_session.add(existing_bug)
    db_session.commit()
    db_session.refresh(existing_bug)

    created = service.create_record(
        major_version_id=major.id,
        minor_version_id=minor.id,
        purpose_type=FieldTestPurposeType.REQUIREMENT,
        requirement_id=req.id,
        test_content=None,
        start_time=datetime.utcnow() - timedelta(hours=2),
        end_time=datetime.utcnow() - timedelta(hours=1),
        result_status=FieldTestResultStatus.PASSED,
        bug_ids=[],
        notes=None,
        actor=user,
    )
    rid = created["record"]["id"]

    options = service.search_existing_bugs(rid, keyword="9700", current_user=user)
    assert any(x["bug_id"] == "b#97001" for x in options)

    res1 = service.link_existing_bug(rid, existing_bug.id, user)
    assert "关联" in res1["message"]
    res2 = service.link_existing_bug(rid, existing_bug.id, user)
    assert "已关联" in res2["message"]

    detail = service.get_detail(rid, user)
    assert len([b for b in detail["bugs"] if b["bug_id"] == "b#97001"]) == 1
