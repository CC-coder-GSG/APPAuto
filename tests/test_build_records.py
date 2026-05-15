from app.models import BuildRecord
from app.schemas.admin import JenkinsBuildReportPayload
from app.services.build_record_service import BuildRecordService


def test_upsert_build_report_creates_record(db_session):
    service = BuildRecordService(db_session)

    record, action = service.upsert_report(
        job_name="s40311",
        build_number="6",
        build_status="SUCCESS",
        version_name="4.0.3.11.260316_alpha.3(40311003)",
        branch="dev/dubhe",
        build_url="http://jenkins/job/s40311/6/",
        change_log="build report from jenkins",
    )

    assert action == "created"
    assert record.id is not None
    assert record.job_name == "s40311"
    assert record.build_number == "6"
    assert record.version_name == "4.0.3.11.260316_alpha.3(40311003)"


def test_upsert_build_report_updates_existing_record(db_session):
    service = BuildRecordService(db_session)
    first, first_action = service.upsert_report(
        job_name="s40311",
        build_number="6",
        build_status="FAILURE",
        version_name=None,
        branch=None,
        build_url=None,
        change_log="first",
    )

    second, second_action = service.upsert_report(
        job_name="s40311",
        build_number=6,
        build_status="SUCCESS",
        version_name="4.0.3.11.260316_alpha.3(40311003)",
        branch="release/s40311",
        build_url="http://jenkins/job/s40311/6/",
        change_log="second",
    )

    rows = db_session.query(BuildRecord).filter(BuildRecord.job_name == "s40311", BuildRecord.build_number == "6").all()
    assert first_action == "created"
    assert second_action == "updated"
    assert first.id == second.id
    assert len(rows) == 1
    assert second.build_status == "SUCCESS"
    assert second.version_name == "4.0.3.11.260316_alpha.3(40311003)"
    assert second.change_log == "second"


def test_list_build_records_supports_filters(db_session):
    service = BuildRecordService(db_session)
    service.upsert_report(job_name="s40311", build_number="6", build_status="SUCCESS")
    service.upsert_report(job_name="s40311", build_number="7", build_status="FAILURE")
    service.upsert_report(job_name="wosense-build", build_number="8", build_status="SUCCESS")

    result = service.list_records(limit=20, offset=0, job_name="s40311", build_status="SUCCESS")

    assert result["total"] == 1
    assert len(result["items"]) == 1
    assert result["items"][0]["job_name"] == "s40311"
    assert result["items"][0]["build_status"] == "SUCCESS"


def test_build_report_payload_accepts_optional_version_name():
    payload = JenkinsBuildReportPayload(
        job_name=" s40311 ",
        build_number=6,
        build_status="success",
        version_name=" 4.0.3.11.260316_alpha.3(40311003) ",
        branch=" ",
        build_url=" http://jenkins/job/s40311/6/ ",
        change_log=" build report from jenkins ",
    )

    assert payload.job_name == "s40311"
    assert payload.build_number == "6"
    assert payload.build_status == "SUCCESS"
    assert payload.version_name == "4.0.3.11.260316_alpha.3(40311003)"
    assert payload.branch is None
    assert payload.build_url == "http://jenkins/job/s40311/6/"


def test_retry_zentao_push_re_runs_push_and_returns_status(db_session):
    service = BuildRecordService(db_session)
    record, _ = service.upsert_report(
        job_name="s40311",
        build_number="9",
        build_status="SUCCESS",
        version_name="4.0.3.11.260316_alpha.3(40311003)",
    )
    # 没绑定大版本 → 首跑就是 no_execution；重试应当复现同一结果而不抛错
    initial_status = record.zentao_push_status

    item, status = service.retry_zentao_push(record.id)

    assert item["id"] == record.id
    # 状态可能是 no_execution / no_binding / not_success，但必须是确定的字符串
    assert status
    assert status == item["zentao_push_status"]
    # 重试不应该把成功状态翻成 None
    if initial_status:
        assert item["zentao_push_status"] is not None


def test_retry_zentao_push_raises_on_missing_record(db_session):
    import pytest
    service = BuildRecordService(db_session)
    with pytest.raises(ValueError):
        service.retry_zentao_push(99999)


def test_reassign_major_moves_zentao_build_via_put(db_session, monkeypatch):
    """禅道 IPD 4.3 不允许 API DELETE build。切归属时必须走 PUT 把原 build 直接
    搬到目标执行下，而不是"新建 + 删除"。这条 test 锁死这个行为，防止以后回退。"""
    from app.models import Version
    from app.models.enums import VersionType
    from app.services import zentao_version_diff_service as svc
    from app.services.build_record_service import BuildRecordService

    source_major = Version(version_no="V4.0.3.1", version_type=VersionType.MAJOR,
                           software_id=1, zentao_execution_id=1647)
    target_major = Version(version_no="V4.0.3.20", version_type=VersionType.MAJOR,
                           software_id=1, zentao_execution_id=1822)
    db_session.add_all([source_major, target_major])
    db_session.commit()

    minor = Version(version_no="4.0.3.1.260514(40301051)", version_type=VersionType.MINOR,
                    parent_id=source_major.id, software_id=1,
                    zentao_build_id=4591, zentao_build_name_cache="4.0.3.1.260514(40301051)")
    db_session.add(minor)
    db_session.commit()

    record, _ = BuildRecordService(db_session).upsert_report(
        job_name="s4031", build_number="51", build_status="SUCCESS",
        version_name="4.0.3.1.260514(40301051)",
    )
    record.auto_archive_minor_version_id = minor.id
    db_session.commit()

    calls = []

    class _StubClient:
        def update_build(self, build_id, **kwargs):
            calls.append(("update_build", build_id, kwargs))
            return {"id": build_id, **kwargs}

        def delete_build(self, build_id):
            calls.append(("delete_build", build_id))
            raise AssertionError("reassign must NOT call delete_build — DELETE is forbidden on Zentao IPD 4.3")

        def create_execution_build(self, *args, **kwargs):
            calls.append(("create_execution_build", args, kwargs))
            raise AssertionError("reassign must NOT create a new build when one already exists — use PUT move instead")

    monkeypatch.setattr(svc, "get_system_zentao_client", lambda db: _StubClient())

    out = svc.build_record_reassign_major(db_session, record.id, target_major.id)

    assert out["ok"] is True
    assert out["moved_zentao_build_id"] == 4591
    assert out["new_zentao_build_id"] is None
    # PUT carried execution=1822 (the target)
    assert any(c[0] == "update_build" and c[2].get("execution_id") == 1822 for c in calls)

    db_session.refresh(minor)
    assert minor.parent_id == target_major.id
    assert minor.zentao_build_id == 4591  # same build, just moved


def test_reassign_major_creates_when_no_existing_build(db_session, monkeypatch):
    """如果 minor 从来没有禅道 build，move 没东西可移 → 退回到"在目标执行下新建"。"""
    from app.models import Version
    from app.models.enums import VersionType
    from app.services import zentao_version_diff_service as svc
    from app.services.build_record_service import BuildRecordService

    source_major = Version(version_no="V4.0.3.1", version_type=VersionType.MAJOR,
                           software_id=1, zentao_execution_id=1647)
    target_major = Version(version_no="V4.0.3.20", version_type=VersionType.MAJOR,
                           software_id=1, zentao_execution_id=1822)
    db_session.add_all([source_major, target_major])
    db_session.commit()

    minor = Version(version_no="4.0.3.1.260514(40301051)", version_type=VersionType.MINOR,
                    parent_id=source_major.id, software_id=1,
                    zentao_build_id=None)
    db_session.add(minor)
    db_session.commit()

    record, _ = BuildRecordService(db_session).upsert_report(
        job_name="s4031", build_number="52", build_status="SUCCESS",
        version_name="4.0.3.1.260514(40301051)",
    )
    record.auto_archive_minor_version_id = minor.id
    db_session.commit()

    class _StubClient:
        def get(self, path):
            assert path == "executions/1822"
            return {"id": 1822, "project": 134, "products": [{"id": 15}]}

        def update_build(self, *args, **kwargs):
            raise AssertionError("no existing build → should NOT call update_build")

        def create_execution_build(self, exec_id, name, **kwargs):
            assert exec_id == 1822
            assert kwargs.get("project_id") == 134
            return {"id": 9001, "name": name}

    monkeypatch.setattr(svc, "get_system_zentao_client", lambda db: _StubClient())

    out = svc.build_record_reassign_major(db_session, record.id, target_major.id)

    assert out["ok"] is True
    assert out["new_zentao_build_id"] == 9001
    assert out["moved_zentao_build_id"] is None

    db_session.refresh(minor)
    assert minor.zentao_build_id == 9001


def test_get_major_log_aggregates_change_logs_in_created_order(db_session):
    service = BuildRecordService(db_session)
    service.upsert_report(
        job_name="s40311",
        build_number="5",
        build_status="SUCCESS",
        version_name="4.0.3.11.260316_alpha.1(40311001)",
        change_log="修复 A",
    )
    service.upsert_report(
        job_name="s40311",
        build_number="6",
        build_status="SUCCESS",
        version_name="4.0.3.11.260316_alpha.2(40311002)",
        change_log="修复 B",
    )
    service.upsert_report(
        job_name="s40311",
        build_number="7",
        build_status="SUCCESS",
        version_name="4.0.3.11.260316_alpha.3(40311003)",
        change_log="",
    )

    result = service.get_major_log("s40311")

    assert result["job_name"] == "s40311"
    assert result["record_count"] == 3
    assert result["latest_build_number"] == "7"
    assert "## 4.0.3.11.260316_alpha.1(40311001)\n修复 A" in result["major_log"]
    assert "## 4.0.3.11.260316_alpha.2(40311002)\n修复 B" in result["major_log"]
    assert "alpha.3" not in result["major_log"]
