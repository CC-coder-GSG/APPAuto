from app.models import BugSourceType, BugStage5Record, BugTracking, Requirement, RequirementStatus, User, UserRole, Version, VersionType
from app.services import overall_test_service
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

    assert result["message"] == "整体测试结果已更新"
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
    assert row["my_minor_version_id"] == minor.id
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
    def __init__(self, payload_map=None):
        self.payload_map = payload_map or {}

    def get(self, path, params=None):
        return self.payload_map.get(path)

    def list_projects(self):
        return (self.get('projects') or {}).get('projects', [])

    def list_project_executions(self, project_id):
        return (self.get(f'projects/{project_id}/executions') or {}).get('executions', [])

    def list_execution_builds(self, execution_id):
        return (self.get(f'executions/{execution_id}/builds') or {}).get('builds', [])


class _Fake401ZentaoClient(_FakeZentaoClient):
    def get(self, path, params=None):
        raise overall_test_service.ZentaoAPIError(401, '{"error":"Unauthorized"}')


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


def test_stage5_sync_zentao_major_bugs_retries_once_when_zentao_token_expired(db_session, monkeypatch):
    major = _create_major(db_session, "V4.0.3.1")
    user = _create_user(db_session, "stage5_sync_user4")
    clients = [
        (_Fake401ZentaoClient(), "http://zentao"),
        (_FakeZentaoClient({
            "projects": {"projects": [{"id": 9, "name": "Survey"}]},
            "projects/9/executions": {"executions": [{"id": 4031, "name": "s4031"}]},
            "executions/4031/bugs": {
                "bugs": [
                    {"id": 7301, "title": "401 重试成功的 Bug", "openedBuild": {}},
                ]
            },
            "executions/4031/builds": {"builds": []},
        }), "http://zentao"),
    ]
    invalidated = []

    service = Stage5Service(db_session)
    service._get_zentao_client_ctx = lambda user_id: clients.pop(0)  # type: ignore[method-assign]
    monkeypatch.setattr(overall_test_service, "invalidate_token", lambda user_id, db: invalidated.append(user_id))

    result = service.sync_zentao_major_bugs(major_version_id=major.id, current_user=user)

    rows = db_session.query(BugTracking).filter(BugTracking.major_version_id == major.id).all()
    assert result["remote_total"] == 1
    assert result["created"] == 1
    assert len(rows) == 1
    assert rows[0].bug_id == "b#7301"
    assert invalidated == [user.id]


def test_stage5_sync_zentao_major_bugs_keeps_bugs_resolved_in_our_build_under_their_own_major(db_session):
    """
    builds/{id}/bugs 返回的是「在该构建里解决」的 Bug。其他版本发现、修进本版本构建的 Bug
    （线上 34186：执行 s40416、发现于 40416008、解决于 4.0.5.0 的构建）不能被改挂到当前同步的大版本。
    """
    old_major = _create_major(db_session, "V4.0.4.16")
    old_major.zentao_execution_id = 2033
    old_minor = _create_minor(db_session, old_major.id, "4.0.4.16.261124_fm(40416008)")
    old_minor.zentao_build_id = 5329
    major = _create_major(db_session, "V4.0.5.0")
    major.zentao_execution_id = 1875
    db_session.commit()
    user = _create_user(db_session, "stage5_sync_user_cross")
    # 之前被错挂到 4.0.5.0 的存量记录，同步后应回到 4.0.4.16
    stale = BugTracking(major_version_id=major.id, requirement_id=None, source_type=BugSourceType.MANUAL, bug_id="b#34186",
                        created_by_id=user.id, zentao_bug_id="34186", zentao_execution_id="1875")
    db_session.add(stale)
    db_session.commit()

    service = Stage5Service(db_session)
    fake_client = _FakeZentaoClient({
        "executions/1875/bugs": {"bugs": [
            {"id": 9001, "title": "本版本发现的 Bug", "execution": 1875, "openedBuild": {}},
        ]},
        "executions/1875/builds": {"builds": [{"id": 5432, "name": "4.0.5.0.261127(40500040)"}]},
        "builds/5432/bugs": {"bugs": [
            {"id": 34186, "title": "林业大师分享功能无法使用", "execution": 2033, "openedBuild": "5329", "status": "resolved"},
            {"id": 9002, "title": "只挂在本版本构建、无执行信息的 Bug", "openedBuild": {}},
        ]},
    })
    service._get_zentao_client_ctx = lambda user_id: (fake_client, "http://zentao")  # type: ignore[method-assign]

    result = service.sync_zentao_major_bugs(major_version_id=major.id, current_user=user)

    by_id = {row.zentao_bug_id: row for row in db_session.query(BugTracking).all()}
    assert result["remote_total"] == 3
    assert by_id["34186"].major_version_id == old_major.id
    assert by_id["34186"].found_minor_version_id == old_minor.id
    assert by_id["34186"].zentao_execution_id == "2033"
    assert by_id["9001"].major_version_id == major.id
    assert by_id["9002"].major_version_id == major.id
    overview_ids = {b["zentao_bug_id"] for b in service.overview(major.id, user)["bug_pool"]}
    assert overview_ids == {"9001", "9002"}
