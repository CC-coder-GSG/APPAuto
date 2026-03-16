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
