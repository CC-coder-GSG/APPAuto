from app.models import BugSourceType, BugStage5Record, BugTracking, Requirement, RequirementStatus, User, UserRole, Version, VersionType
from app.services.stage5_service import Stage5Service


def _create_major(db_session, version_no: str = "V6000") -> Version:
    major = Version(version_no=version_no, version_type=VersionType.MAJOR)
    db_session.add(major)
    db_session.commit()
    db_session.refresh(major)
    return major


def _create_minor(db_session, parent_id: int, version_no: str = "V6000.1") -> Version:
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


def _create_requirement(db_session, major_id: int) -> Requirement:
    req = Requirement(zentao_req_id="r#6001", title="Stage5需求", major_version_id=major_id, status=RequirementStatus.PENDING)
    db_session.add(req)
    db_session.commit()
    db_session.refresh(req)
    return req


def test_submit_stage5_result_marks_bug_closed_when_any_record_done(db_session):
    major = _create_major(db_session)
    minor = _create_minor(db_session, major.id)
    user = _create_user(db_session, "stage5_user")
    req = _create_requirement(db_session, major.id)
    bug = BugTracking(
        major_version_id=major.id,
        requirement_id=req.id,
        source_type=BugSourceType.MANUAL,
        bug_id="b#6001",
        found_minor_version_id=minor.id,
        created_by_id=user.id,
        closed=False,
    )
    db_session.add(bug)
    db_session.commit()
    db_session.refresh(bug)

    service = Stage5Service(db_session)
    result = service.submit_result(
        bug.id,
        minor_version_id=minor.id,
        test_done=True,
        newly_found_bug_id=None,
        resolution="fixed",
        current_user=user,
    )
    db_session.refresh(bug)

    assert result["message"] == "Stage5 result updated"
    assert bug.closed is True
    assert bug.fixed_minor_version_id == minor.id


def test_submit_stage5_result_creates_derived_bug_and_dispatches_to_self(db_session):
    major = _create_major(db_session, "V6001")
    minor = _create_minor(db_session, major.id, "V6001.1")
    user = _create_user(db_session, "stage5_user2")
    req = _create_requirement(db_session, major.id)
    bug = BugTracking(
        major_version_id=major.id,
        requirement_id=req.id,
        source_type=BugSourceType.MANUAL,
        bug_id="b#6002",
        found_minor_version_id=minor.id,
        created_by_id=user.id,
    )
    db_session.add(bug)
    db_session.commit()
    db_session.refresh(bug)

    service = Stage5Service(db_session)
    result = service.submit_result(
        bug.id,
        minor_version_id=minor.id,
        test_done=False,
        newly_found_bug_id="b#6003,b#6004",
        resolution="fixed",
        current_user=user,
    )

    derived = db_session.query(BugTracking).filter(BugTracking.source_ref == "b#6002").order_by(BugTracking.bug_id.asc()).all()
    assert result["created_bug_ids"] == ["b#6003", "b#6004"]
    assert [b.bug_id for b in derived] == ["b#6003", "b#6004"]
    assert all(b.dispatched_to_id == user.id for b in derived)


def test_stage5_overview_separates_my_record_and_other_records(db_session):
    major = _create_major(db_session, "V6002")
    minor = _create_minor(db_session, major.id, "V6002.1")
    me = _create_user(db_session, "stage5_me")
    other = _create_user(db_session, "stage5_other")
    req = _create_requirement(db_session, major.id)
    bug = BugTracking(
        major_version_id=major.id,
        requirement_id=req.id,
        source_type=BugSourceType.MANUAL,
        bug_id="b#6005",
        found_minor_version_id=minor.id,
        created_by_id=me.id,
        dispatched_to_id=other.id,
    )
    db_session.add(bug)
    db_session.commit()
    db_session.refresh(bug)
    db_session.add_all([
        BugStage5Record(bug_tracking_id=bug.id, user_id=me.id, minor_version_id=minor.id, test_done=False, resolution="fixed"),
        BugStage5Record(bug_tracking_id=bug.id, user_id=other.id, minor_version_id=minor.id, test_done=True, resolution="rejected"),
    ])
    db_session.commit()

    service = Stage5Service(db_session)
    data = service.overview(major.id, me)
    row = data["bug_pool"][0]

    assert row["my_test_done"] is False
    assert row["dispatched_to_name"] == other.username
    assert len(row["other_records"]) == 1
    assert row["other_records"][0]["username"] == other.username
    assert row["other_records"][0]["resolution"] == "rejected"


def test_stage5_overview_contains_field_test_source_type(db_session):
    major = _create_major(db_session, "V6003")
    minor = _create_minor(db_session, major.id, "V6003.1")
    me = _create_user(db_session, "stage5_field_user")
    req = _create_requirement(db_session, major.id)
    bug = BugTracking(
        major_version_id=major.id,
        requirement_id=req.id,
        source_type=BugSourceType.FIELD_TEST,
        source_ref="field_test:1",
        bug_id="b#6006",
        found_minor_version_id=minor.id,
        created_by_id=me.id,
    )
    db_session.add(bug)
    db_session.commit()

    service = Stage5Service(db_session)
    data = service.overview(major.id, me)
    assert data["bug_pool"][0]["source_type"] == "field_test"


class _FakeZentaoClient:
    def __init__(self, payload_map):
        self.payload_map = payload_map

    def get(self, path, params=None):
        return self.payload_map.get(path)


def test_stage5_sync_zentao_major_bugs_creates_remote_bugs(db_session):
    major = _create_major(db_session, "V4.0.3.1")
    minor = _create_minor(db_session, major.id, "4.0.3.1.260413(40311001)")
    minor.zentao_build_id = 4416
    db_session.commit()
    user = _create_user(db_session, "stage5_sync_user")

    service = Stage5Service(db_session)
    fake_client = _FakeZentaoClient({
        "projects": {"projects": [{"id": 9, "name": "Survey"}]},
        "projects/9/executions": {"executions": [{"id": 4031, "name": "s4031"}]},
        "executions/4031/bugs": {
            "bugs": [
                {"id": 7001, "title": "同步进来的 Bug 1", "openedBuild": {"4416": "build-4416"}},
                {"id": 7002, "title": "同步进来的 Bug 2", "openedBuild": {"4416": "build-4416"}},
            ]
        },
        "builds/4416/bugs": None,
    })
    service._get_zentao_client_ctx = lambda user_id: (fake_client, "http://zentao")  # type: ignore[method-assign]

    result = service.sync_zentao_major_bugs(major_version_id=major.id, current_user=user)

    rows = db_session.query(BugTracking).filter(BugTracking.major_version_id == major.id).order_by(BugTracking.bug_id.asc()).all()
    assert result["remote_total"] == 2
    assert result["created"] == 2
    assert [row.bug_id for row in rows] == ["b#7001", "b#7002"]
    assert all(row.zentao_bug_url == f"http://zentao/bug-view-{row.zentao_bug_id}.html" for row in rows)
    assert all(row.found_minor_version_id == minor.id for row in rows)


def test_stage5_sync_zentao_major_bugs_updates_existing_bug_without_duplicate(db_session):
    major = _create_major(db_session, "V4.0.3.1")
    user = _create_user(db_session, "stage5_sync_user2")
    existing = BugTracking(
        major_version_id=major.id,
        requirement_id=None,
        source_type=BugSourceType.MANUAL,
        bug_id="b#7101",
        created_by_id=user.id,
        zentao_bug_id="7101",
        zentao_bug_title="旧标题",
    )
    db_session.add(existing)
    db_session.commit()

    service = Stage5Service(db_session)
    fake_client = _FakeZentaoClient({
        "projects": {"projects": [{"id": 9, "name": "Survey"}]},
        "projects/9/executions": {"executions": [{"id": 4031, "name": "s4031"}]},
        "executions/4031/bugs": {
            "bugs": [
                {"id": 7101, "title": "新标题", "openedBuild": {}},
            ]
        },
    })
    service._get_zentao_client_ctx = lambda user_id: (fake_client, "http://zentao")  # type: ignore[method-assign]

    result = service.sync_zentao_major_bugs(major_version_id=major.id, current_user=user)

    rows = db_session.query(BugTracking).filter(BugTracking.zentao_bug_id == "7101").all()
    assert len(rows) == 1
    assert rows[0].zentao_bug_title == "新标题"
    assert result["created"] == 0
    assert result["updated"] == 1


def test_stage5_sync_zentao_major_bugs_fetches_remote_execution_builds_without_local_minor(db_session):
    major = _create_major(db_session, "V4.0.3.1")
    user = _create_user(db_session, "stage5_sync_user3")

    service = Stage5Service(db_session)
    fake_client = _FakeZentaoClient({
        "projects": {"projects": [{"id": 9, "name": "Survey"}]},
        "projects/9/executions": {"executions": [{"id": 4031, "name": "s4031"}]},
        "executions/4031/bugs": {"bugs": []},
        "executions/4031/builds": {"builds": [{"id": 5522, "name": "4031-build"}]},
        "builds/5522/bugs": {
            "bugs": [
                {"id": 7201, "title": "仅挂在远端 build 下的 Bug", "openedBuild": {"5522": "build-5522"}},
            ]
        },
    })
    service._get_zentao_client_ctx = lambda user_id: (fake_client, "http://zentao")  # type: ignore[method-assign]

    result = service.sync_zentao_major_bugs(major_version_id=major.id, current_user=user)

    rows = db_session.query(BugTracking).filter(BugTracking.major_version_id == major.id).all()
    assert result["remote_total"] == 1
    assert result["created"] == 1
    assert len(rows) == 1
    assert rows[0].bug_id == "b#7201"
    assert rows[0].zentao_bug_title == "仅挂在远端 build 下的 Bug"
