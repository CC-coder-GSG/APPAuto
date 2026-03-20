from __future__ import annotations

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.api.deps import get_current_user, get_db
from app.api.routes.zentao_sync import router as zentao_router
from app.core.config import settings
from app.models import BrowserSyncEvent, BugTracking, Requirement, SoftwareProduct, TestCase, User, UserRole, Version, VersionType
from app.schemas.zentao_sync import ZentaoBrowserSyncPayload
from app.services.zentao_sync_service import ZentaoSyncService


def _create_user(db_session, username: str, role: UserRole = UserRole.USER, display_name: str | None = None) -> User:
    row = User(username=username, password_hash="x", role=role, display_name=display_name)
    db_session.add(row)
    db_session.commit()
    db_session.refresh(row)
    return row


def _create_major(db_session, software_id: int, version_no: str) -> Version:
    row = Version(version_no=version_no, version_type=VersionType.MAJOR, software_id=software_id)
    db_session.add(row)
    db_session.commit()
    db_session.refresh(row)
    return row


def _create_minor(db_session, parent_id: int, version_no: str) -> Version:
    row = Version(version_no=version_no, version_type=VersionType.MINOR, parent_id=parent_id)
    db_session.add(row)
    db_session.commit()
    db_session.refresh(row)
    return row


def _create_requirement(db_session, major_id: int, req_no: str, title: str = "sync requirement") -> Requirement:
    row = Requirement(zentao_req_id=req_no, title=title, major_version_id=major_id)
    db_session.add(row)
    db_session.commit()
    db_session.refresh(row)
    return row


def _base_bug_payload(client_record_id: str = "bug_29332_1774000000000") -> dict:
    return {
        "entityType": "bug",
        "action": "create",
        "source": "tampermonkey",
        "clientRecordId": client_record_id,
        "capturedAt": 1774000000000,
        "topHref": "http://zentao/bug-browse",
        "draft": {
            "productName": "Survey Master App",
            "projectName": "Survey Master 5.0",
            "openedBuildIds": ["4416"],
            "bugTitle": "register failed",
            "executionId": "s4030",
            "executionName": "s4030(V4.0.3.0)",
            "requirementId": "2992",
            "requirementName": "SR 2992",
            "creatorName": "Alice",
        },
        "result": {"zentaoBugId": "29332", "zentaoBugUrl": "http://zentao/bug-view-29332.html"},
    }


def _base_case_payload(client_record_id: str = "testcase_19617_1774000000001") -> dict:
    return {
        "entityType": "testcase",
        "action": "create",
        "source": "tampermonkey",
        "clientRecordId": client_record_id,
        "capturedAt": 1774000000001,
        "topHref": "http://zentao/testcase-browse",
        "draft": {
            "productName": "Survey Master App",
            "caseTitle": "import line file",
            "requirementId": "535",
            "requirementName": "SR 535",
            "creatorName": "Bob",
        },
        "result": {"zentaoCaseId": "19617", "zentaoCaseUrl": "http://zentao/testcase-view-19617.html"},
    }


def _make_client(db_session, admin_user: User):
    app = FastAPI()
    app.include_router(zentao_router)

    def _get_db():
        yield db_session

    app.dependency_overrides[get_db] = _get_db
    app.dependency_overrides[get_current_user] = lambda: admin_user
    return TestClient(app)


def test_api_receive_testcase_and_bug_with_api_key(db_session):
    software = SoftwareProduct(name="Survey Master")
    db_session.add(software)
    db_session.commit()
    db_session.refresh(software)
    major = _create_major(db_session, software.id, "s4030")
    _create_requirement(db_session, major.id, "r#2992")
    _create_requirement(db_session, major.id, "r#535")
    admin = _create_user(db_session, "admin_sync", role=UserRole.ADMIN)

    old_enabled = settings.zentao_sync_enabled
    old_key = settings.zentao_sync_api_key
    settings.zentao_sync_enabled = True
    settings.zentao_sync_api_key = "abc-key"
    try:
        client = _make_client(db_session, admin)
        try:
            r1 = client.post("/api/integrations/zentao/browser-events", json=_base_case_payload(), headers={"X-Zentao-Sync-Key": "abc-key"})
            r2 = client.post("/api/integrations/zentao/browser-events", json=_base_bug_payload(), headers={"X-Zentao-Sync-Key": "abc-key"})
            assert r1.status_code == 200
            assert r2.status_code == 200
            assert db_session.query(BrowserSyncEvent).count() == 2
        finally:
            client.close()
    finally:
        settings.zentao_sync_enabled = old_enabled
        settings.zentao_sync_api_key = old_key


def test_api_key_missing_rejected(db_session):
    admin = _create_user(db_session, "admin_sync2", role=UserRole.ADMIN)
    old_enabled = settings.zentao_sync_enabled
    old_key = settings.zentao_sync_api_key
    settings.zentao_sync_enabled = True
    settings.zentao_sync_api_key = "abc-key"
    try:
        client = _make_client(db_session, admin)
        try:
            r = client.post("/api/integrations/zentao/browser-events", json=_base_case_payload())
            assert r.status_code == 401
        finally:
            client.close()
    finally:
        settings.zentao_sync_enabled = old_enabled
        settings.zentao_sync_api_key = old_key


def test_client_record_id_idempotent_and_legacy_endpoint_works(db_session):
    software = SoftwareProduct(name="Survey Master")
    db_session.add(software)
    db_session.commit()
    db_session.refresh(software)
    major = _create_major(db_session, software.id, "s4030")
    _create_requirement(db_session, major.id, "r#535")
    admin = _create_user(db_session, "admin_sync3", role=UserRole.ADMIN)

    old_enabled = settings.zentao_sync_enabled
    old_key = settings.zentao_sync_api_key
    settings.zentao_sync_enabled = True
    settings.zentao_sync_api_key = "abc-key"
    try:
        client = _make_client(db_session, admin)
        try:
            payload = _base_case_payload(client_record_id="testcase_19617_same")
            r1 = client.post("/api/zentao/browser-sync", json=payload, headers={"X-Zentao-Sync-Key": "abc-key"})
            r2 = client.post("/api/zentao/browser-sync", json=payload, headers={"X-Zentao-Sync-Key": "abc-key"})
            assert r1.status_code == 200
            assert r2.status_code == 200
            assert db_session.query(BrowserSyncEvent).count() == 1
            assert r2.json()["mode"] in {"update", "duplicate"}
        finally:
            client.close()
    finally:
        settings.zentao_sync_enabled = old_enabled
        settings.zentao_sync_api_key = old_key


def test_mapping_unique_and_pending_cases(db_session):
    software = SoftwareProduct(name="Survey Master")
    db_session.add(software)
    db_session.commit()
    db_session.refresh(software)
    major = _create_major(db_session, software.id, "s4030")
    req_unique = _create_requirement(db_session, major.id, "r#535", "SR 535 unique")
    _create_requirement(db_session, major.id, "r#536", "SR duplicate")
    _create_requirement(db_session, major.id, "r#537", "SR duplicate")
    service = ZentaoSyncService(db_session)

    p1 = _base_case_payload("case_unique")
    p1["draft"]["requirementId"] = "535"
    result1 = service.receive_event(ZentaoBrowserSyncPayload(**p1))
    event1 = db_session.query(BrowserSyncEvent).filter(BrowserSyncEvent.id == result1["event_id"]).first()
    assert event1.mapped_requirement_id == req_unique.id
    assert event1.status in {"ready_to_apply", "applied"}

    p2 = _base_case_payload("case_pending")
    p2["result"]["zentaoCaseId"] = "19618"
    p2["draft"]["requirementId"] = None
    p2["draft"]["requirementName"] = "SR duplicate"
    result2 = service.receive_event(ZentaoBrowserSyncPayload(**p2))
    event2 = db_session.query(BrowserSyncEvent).filter(BrowserSyncEvent.id == result2["event_id"]).first()
    assert event2.status == "pending_mapping"


def test_apply_testcase_success_and_no_duplicate(db_session):
    software = SoftwareProduct(name="Survey Master")
    db_session.add(software)
    db_session.commit()
    db_session.refresh(software)
    major = _create_major(db_session, software.id, "s4030")
    req = _create_requirement(db_session, major.id, "r#535")
    service = ZentaoSyncService(db_session)
    rec = service.receive_event(ZentaoBrowserSyncPayload(**_base_case_payload("case_apply")))
    event = db_session.query(BrowserSyncEvent).filter(BrowserSyncEvent.id == rec["event_id"]).first()
    event.mapped_requirement_id = req.id
    event.status = "ready_to_apply"
    db_session.commit()

    out1 = service.apply_event(event.id, actor_id=None)
    out2 = service.apply_event(event.id, actor_id=None)
    assert out1["ok"] is True
    assert out2["ok"] is True
    assert db_session.query(TestCase).filter(TestCase.zentao_case_id == "u#19617", TestCase.requirement_id == req.id).count() == 1


def test_apply_bug_success_and_block_when_requirement_test_completed(db_session):
    software = SoftwareProduct(name="Survey Master")
    db_session.add(software)
    db_session.commit()
    db_session.refresh(software)
    major = _create_major(db_session, software.id, "s4030")
    minor = _create_minor(db_session, major.id, "4.0.3.0")
    req_ok = _create_requirement(db_session, major.id, "r#2992")
    req_block = _create_requirement(db_session, major.id, "r#2993")
    req_block.test_completed = True
    db_session.commit()
    _create_user(db_session, "sync_actor", role=UserRole.ADMIN)
    service = ZentaoSyncService(db_session)

    payload_ok = _base_bug_payload("bug_apply_ok")
    payload_ok["draft"]["requirementId"] = "2992"
    ok_rec = service.receive_event(ZentaoBrowserSyncPayload(**payload_ok))
    ok_event = db_session.query(BrowserSyncEvent).filter(BrowserSyncEvent.id == ok_rec["event_id"]).first()
    ok_event.mapped_requirement_id = req_ok.id
    ok_event.mapped_major_version_id = req_ok.major_version_id
    ok_event.mapped_minor_version_id = minor.id
    ok_event.status = "ready_to_apply"
    db_session.commit()
    out = service.apply_event(ok_event.id, actor_id=None)
    assert out["ok"] is True
    assert db_session.query(BugTracking).filter(BugTracking.bug_id == "b#29332").count() == 1

    payload_block = _base_bug_payload("bug_apply_block")
    payload_block["result"]["zentaoBugId"] = "29333"
    payload_block["draft"]["requirementId"] = "2993"
    block_rec = service.receive_event(ZentaoBrowserSyncPayload(**payload_block))
    block_event = db_session.query(BrowserSyncEvent).filter(BrowserSyncEvent.id == block_rec["event_id"]).first()
    block_event.mapped_requirement_id = req_block.id
    block_event.mapped_major_version_id = req_block.major_version_id
    block_event.mapped_minor_version_id = minor.id
    block_event.status = "ready_to_apply"
    db_session.commit()
    try:
        service.apply_event(block_event.id, actor_id=None)
        assert False, "expected requirement test_completed guard"
    except HTTPException as exc:
        assert exc.status_code == 400


def test_list_filters_status_entity_keyword(db_session):
    service = ZentaoSyncService(db_session)
    service.receive_event(ZentaoBrowserSyncPayload(**_base_case_payload("case_list_1")))
    service.receive_event(ZentaoBrowserSyncPayload(**_base_bug_payload("bug_list_1")))

    all_data = service.list_events(entity_type=None, status=None, keyword=None, date_from=None, date_to=None, only_unapplied=False, page=1, page_size=20)
    case_data = service.list_events(entity_type="testcase", status=None, keyword=None, date_from=None, date_to=None, only_unapplied=False, page=1, page_size=20)
    kw_data = service.list_events(entity_type=None, status=None, keyword="29332", date_from=None, date_to=None, only_unapplied=False, page=1, page_size=20)
    assert all_data["total"] >= 2
    assert all(item["entity_type"] == "testcase" for item in case_data["items"])
    assert any((item.get("zentao_bug_id") == "29332") for item in kw_data["items"])
