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


def _mapped_bug_payload(
    client_record_id: str,
    *,
    requirement_id: str | None = None,
    requirement_name: str | None = None,
    execution_id: str | None = "s4030",
    execution_name: str | None = "s4030(V4.0.3.0)",
    affected_version: str | None = "4.0.3.0.260312(40300109)",
) -> dict:
    payload = _base_bug_payload(client_record_id)
    payload["draft"]["requirementId"] = requirement_id
    payload["draft"]["requirementName"] = requirement_name
    payload["draft"]["executionId"] = execution_id
    payload["draft"]["executionName"] = execution_name
    payload["draft"]["affectedVersion"] = affected_version
    return payload


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


def _create_sync_event_with_status(db_session, *, status: str, entity_type: str = "bug") -> BrowserSyncEvent:
    service = ZentaoSyncService(db_session)
    if entity_type == "bug":
        payload = _base_bug_payload(f"bug_delete_{status}")
    else:
        payload = _base_case_payload(f"case_delete_{status}")
    result = service.receive_event(ZentaoBrowserSyncPayload(**payload))
    row = db_session.query(BrowserSyncEvent).filter(BrowserSyncEvent.id == result["event_id"]).first()
    row.status = status
    db_session.commit()
    return row


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


def test_delete_pending_mapping_event_success(db_session):
    admin = _create_user(db_session, "admin_delete_pending", role=UserRole.ADMIN)
    row = _create_sync_event_with_status(db_session, status="pending_mapping")
    client = _make_client(db_session, admin)
    try:
        resp = client.delete(f"/api/integrations/zentao/browser-events/{row.id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert db_session.query(BrowserSyncEvent).filter(BrowserSyncEvent.id == row.id).first() is None
    finally:
        client.close()


def test_delete_ready_to_apply_event_success(db_session):
    admin = _create_user(db_session, "admin_delete_ready", role=UserRole.ADMIN)
    row = _create_sync_event_with_status(db_session, status="ready_to_apply")
    client = _make_client(db_session, admin)
    try:
        resp = client.delete(f"/api/integrations/zentao/browser-events/{row.id}")
        assert resp.status_code == 200
        assert db_session.query(BrowserSyncEvent).filter(BrowserSyncEvent.id == row.id).first() is None
    finally:
        client.close()


def test_delete_duplicate_event_success(db_session):
    admin = _create_user(db_session, "admin_delete_dup", role=UserRole.ADMIN)
    row = _create_sync_event_with_status(db_session, status="duplicate")
    client = _make_client(db_session, admin)
    try:
        resp = client.delete(f"/api/integrations/zentao/browser-events/{row.id}")
        assert resp.status_code == 200
        assert db_session.query(BrowserSyncEvent).filter(BrowserSyncEvent.id == row.id).first() is None
    finally:
        client.close()


def test_delete_nonexistent_event_returns_404(db_session):
    admin = _create_user(db_session, "admin_delete_404", role=UserRole.ADMIN)
    client = _make_client(db_session, admin)
    try:
        resp = client.delete("/api/integrations/zentao/browser-events/999999")
        assert resp.status_code == 404
        assert "不存在" in resp.json()["detail"]
    finally:
        client.close()


def test_deleted_event_not_in_list_api(db_session):
    admin = _create_user(db_session, "admin_delete_list", role=UserRole.ADMIN)
    row = _create_sync_event_with_status(db_session, status="pending_mapping")
    client = _make_client(db_session, admin)
    try:
        del_resp = client.delete(f"/api/integrations/zentao/browser-events/{row.id}")
        assert del_resp.status_code == 200

        list_resp = client.get("/api/integrations/zentao/browser-events?page=1&page_size=20")
        assert list_resp.status_code == 200
        ids = [it["id"] for it in list_resp.json()["items"]]
        assert row.id not in ids
    finally:
        client.close()


def test_service_delete_sync_event_behavior(db_session):
    row = _create_sync_event_with_status(db_session, status="pending_mapping")
    service = ZentaoSyncService(db_session)
    out = service.delete_event(row.id, actor_id=None)
    assert out["ok"] is True
    assert db_session.query(BrowserSyncEvent).filter(BrowserSyncEvent.id == row.id).first() is None


def test_delete_testcase_sync_event_success(db_session):
    admin = _create_user(db_session, "admin_delete_case", role=UserRole.ADMIN)
    row = _create_sync_event_with_status(db_session, status="pending_mapping", entity_type="testcase")
    client = _make_client(db_session, admin)
    try:
        resp = client.delete(f"/api/integrations/zentao/browser-events/{row.id}")
        assert resp.status_code == 200
        assert db_session.query(BrowserSyncEvent).filter(BrowserSyncEvent.id == row.id).first() is None
    finally:
        client.close()


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


def test_bug_requirement_id_numeric_matches_prefixed_req(db_session):
    software = SoftwareProduct(name="Survey Master")
    db_session.add(software)
    db_session.commit()
    db_session.refresh(software)
    major = _create_major(db_session, software.id, "V4.0.3.0")
    minor = _create_minor(db_session, major.id, "4.0.3.0.260312(40300109)")
    req = _create_requirement(db_session, major.id, "r#4252", "删除google，china 定制，删除个推")
    service = ZentaoSyncService(db_session)

    payload = _mapped_bug_payload(
        "bug_req_numeric",
        requirement_id="4252",
        requirement_name="需求 4252",
    )
    rec = service.receive_event(ZentaoBrowserSyncPayload(**payload))
    row = db_session.query(BrowserSyncEvent).filter(BrowserSyncEvent.id == rec["event_id"]).first()
    assert row.mapped_requirement_id == req.id
    assert row.mapped_major_version_id == major.id
    assert row.mapped_minor_version_id == minor.id


def test_bug_requirement_name_normalized_unique_match(db_session):
    software = SoftwareProduct(name="Survey Master")
    db_session.add(software)
    db_session.commit()
    db_session.refresh(software)
    major = _create_major(db_session, software.id, "V4.0.3.0")
    _create_minor(db_session, major.id, "4.0.3.0.260312(40300109)")
    req = _create_requirement(db_session, major.id, "r#4252", "删除google，china 定制，删除个推")
    service = ZentaoSyncService(db_session)

    payload = _mapped_bug_payload(
        "bug_req_title_unique",
        requirement_id="9999",
        requirement_name="SR 4252:删除google，china 定制，删除个推",
    )
    rec = service.receive_event(ZentaoBrowserSyncPayload(**payload))
    row = db_session.query(BrowserSyncEvent).filter(BrowserSyncEvent.id == rec["event_id"]).first()
    assert row.mapped_requirement_id == req.id


def test_bug_requirement_name_multi_candidates_keeps_pending(db_session):
    software = SoftwareProduct(name="Survey Master")
    db_session.add(software)
    db_session.commit()
    db_session.refresh(software)
    major = _create_major(db_session, software.id, "V4.0.3.0")
    _create_minor(db_session, major.id, "4.0.3.0.260312(40300109)")
    _create_requirement(db_session, major.id, "r#4252", "删除google，china 定制，删除个推")
    _create_requirement(db_session, major.id, "r#4253", "删除google，china 定制，删除个推")
    service = ZentaoSyncService(db_session)

    payload = _mapped_bug_payload(
        "bug_req_title_multi",
        requirement_id=None,
        requirement_name="SR 4252:删除google，china 定制，删除个推",
    )
    rec = service.receive_event(ZentaoBrowserSyncPayload(**payload))
    row = db_session.query(BrowserSyncEvent).filter(BrowserSyncEvent.id == rec["event_id"]).first()
    assert row.mapped_requirement_id is None
    assert row.status == "pending_mapping"


def test_bug_execution_s_token_maps_to_v_major(db_session):
    software = SoftwareProduct(name="Survey Master")
    db_session.add(software)
    db_session.commit()
    db_session.refresh(software)
    major = _create_major(db_session, software.id, "V4.0.3.0")
    service = ZentaoSyncService(db_session)

    payload = _mapped_bug_payload(
        "bug_exec_to_v_major",
        requirement_id=None,
        requirement_name=None,
        affected_version=None,
        execution_name="执行版本：s4030(V4.0.3.0)",
    )
    rec = service.receive_event(ZentaoBrowserSyncPayload(**payload))
    row = db_session.query(BrowserSyncEvent).filter(BrowserSyncEvent.id == rec["event_id"]).first()
    assert row.mapped_major_version_id == major.id


def test_bug_major_can_fallback_from_affected_version(db_session):
    software = SoftwareProduct(name="Survey Master")
    db_session.add(software)
    db_session.commit()
    db_session.refresh(software)
    major = _create_major(db_session, software.id, "V4.0.3.0")
    service = ZentaoSyncService(db_session)

    payload = _mapped_bug_payload(
        "bug_major_from_affected",
        requirement_id=None,
        requirement_name=None,
        execution_id="invalid",
        execution_name="unknown-execution",
    )
    rec = service.receive_event(ZentaoBrowserSyncPayload(**payload))
    row = db_session.query(BrowserSyncEvent).filter(BrowserSyncEvent.id == rec["event_id"]).first()
    assert row.mapped_major_version_id == major.id


def test_bug_minor_prefers_build_no_after_major_matched(db_session):
    software = SoftwareProduct(name="Survey Master")
    db_session.add(software)
    db_session.commit()
    db_session.refresh(software)
    major = _create_major(db_session, software.id, "V4.0.3.0")
    _create_minor(db_session, major.id, "4.0.3.0.260311(40300108)")
    minor_hit = _create_minor(db_session, major.id, "4.0.3.0.260312(40300109)")
    service = ZentaoSyncService(db_session)

    payload = _mapped_bug_payload(
        "bug_minor_build_hit",
        requirement_id=None,
        requirement_name=None,
        execution_name="s4030(V4.0.3.0)",
    )
    rec = service.receive_event(ZentaoBrowserSyncPayload(**payload))
    row = db_session.query(BrowserSyncEvent).filter(BrowserSyncEvent.id == rec["event_id"]).first()
    assert row.mapped_major_version_id == major.id
    assert row.mapped_minor_version_id == minor_hit.id


def test_bug_all_recommendations_success_status_ready_to_apply(db_session):
    software = SoftwareProduct(name="Survey Master")
    db_session.add(software)
    db_session.commit()
    db_session.refresh(software)
    major = _create_major(db_session, software.id, "V4.0.3.0")
    minor = _create_minor(db_session, major.id, "4.0.3.0.260312(40300109)")
    req = _create_requirement(db_session, major.id, "r#4252", "删除google，china 定制，删除个推")
    service = ZentaoSyncService(db_session)

    payload = _mapped_bug_payload(
        "bug_ready_to_apply",
        requirement_id="4252",
        requirement_name="SR 4252:删除google，china 定制，删除个推",
    )
    rec = service.receive_event(ZentaoBrowserSyncPayload(**payload))
    row = db_session.query(BrowserSyncEvent).filter(BrowserSyncEvent.id == rec["event_id"]).first()
    assert row.mapped_requirement_id == req.id
    assert row.mapped_major_version_id == major.id
    assert row.mapped_minor_version_id == minor.id
    assert row.display_bucket == "requirement"
    assert row.status in {"ready_to_apply", "applied"}


def test_detail_includes_bug_title_field(db_session):
    service = ZentaoSyncService(db_session)
    payload = _base_bug_payload("bug_detail_title")
    payload["draft"]["bugTitle"] = "支付流程校验失败"
    rec = service.receive_event(ZentaoBrowserSyncPayload(**payload))
    detail = service.get_event_detail(rec["event_id"])
    assert detail["zentao_bug_title"] == "支付流程校验失败"
    assert detail["title"] == "支付流程校验失败"


def test_detail_handles_empty_bug_title(db_session):
    service = ZentaoSyncService(db_session)
    payload = _base_bug_payload("bug_detail_empty_title")
    payload["draft"]["bugTitle"] = ""
    rec = service.receive_event(ZentaoBrowserSyncPayload(**payload))
    detail = service.get_event_detail(rec["event_id"])
    assert detail["zentao_bug_title"] in {"", None}
    assert detail["id"] == rec["event_id"]


def test_bug_auto_apply_when_major_and_title_present(db_session):
    software = SoftwareProduct(name="Survey Master")
    db_session.add(software)
    db_session.commit()
    db_session.refresh(software)
    major = _create_major(db_session, software.id, "V4.0.3.0")
    service = ZentaoSyncService(db_session)

    payload = _mapped_bug_payload(
        "bug_auto_apply_hit",
        requirement_id=None,
        requirement_name=None,
        execution_name="s4030(V4.0.3.0)",
    )
    payload["draft"]["bugTitle"] = "自动应用命中标题"
    rec = service.receive_event(ZentaoBrowserSyncPayload(**payload))
    row = db_session.query(BrowserSyncEvent).filter(BrowserSyncEvent.id == rec["event_id"]).first()
    assert row.mapped_major_version_id == major.id
    assert row.status == "applied"
    assert row.applied_bug_tracking_id is not None


def test_bug_not_auto_apply_when_major_but_title_empty(db_session):
    software = SoftwareProduct(name="Survey Master")
    db_session.add(software)
    db_session.commit()
    db_session.refresh(software)
    _create_major(db_session, software.id, "V4.0.3.0")
    service = ZentaoSyncService(db_session)

    payload = _mapped_bug_payload(
        "bug_auto_apply_title_empty",
        requirement_id=None,
        requirement_name=None,
        execution_name="s4030(V4.0.3.0)",
    )
    payload["draft"]["bugTitle"] = ""
    rec = service.receive_event(ZentaoBrowserSyncPayload(**payload))
    row = db_session.query(BrowserSyncEvent).filter(BrowserSyncEvent.id == rec["event_id"]).first()
    assert row.mapped_major_version_id is not None
    assert row.status != "applied"


def test_bug_not_auto_apply_when_title_present_but_no_major(db_session):
    service = ZentaoSyncService(db_session)
    payload = _mapped_bug_payload(
        "bug_auto_apply_no_major",
        requirement_id=None,
        requirement_name=None,
        execution_id="unknown",
        execution_name="unknown",
        affected_version=None,
    )
    payload["draft"]["bugTitle"] = "有标题但没版本"
    rec = service.receive_event(ZentaoBrowserSyncPayload(**payload))
    row = db_session.query(BrowserSyncEvent).filter(BrowserSyncEvent.id == rec["event_id"]).first()
    assert row.mapped_major_version_id is None
    assert row.status != "applied"


def test_bug_auto_apply_failure_keeps_event_and_reason(db_session, monkeypatch):
    software = SoftwareProduct(name="Survey Master")
    db_session.add(software)
    db_session.commit()
    db_session.refresh(software)
    _create_major(db_session, software.id, "V4.0.3.0")

    def _boom(*_args, **_kwargs):
        raise RuntimeError("mock auto apply fail")

    monkeypatch.setattr(ZentaoSyncService, "_apply_bug", _boom)
    service = ZentaoSyncService(db_session)
    payload = _mapped_bug_payload(
        "bug_auto_apply_fail",
        requirement_id=None,
        requirement_name=None,
        execution_name="s4030(V4.0.3.0)",
    )
    payload["draft"]["bugTitle"] = "触发失败路径"
    rec = service.receive_event(ZentaoBrowserSyncPayload(**payload))
    row = db_session.query(BrowserSyncEvent).filter(BrowserSyncEvent.id == rec["event_id"]).first()
    assert row.status != "applied"
    assert "自动应用失败" in str(row.failure_reason or "")


def test_non_admin_can_access_zentao_sync_list(db_session):
    normal_user = _create_user(db_session, "normal_sync_user", role=UserRole.USER)
    ZentaoSyncService(db_session).receive_event(ZentaoBrowserSyncPayload(**_base_bug_payload("bug_non_admin_list")))
    client = _make_client(db_session, normal_user)
    try:
        resp = client.get("/api/integrations/zentao/browser-events?page=1&page_size=20")
        assert resp.status_code == 200
    finally:
        client.close()


def test_non_admin_can_access_zentao_sync_detail(db_session):
    normal_user = _create_user(db_session, "normal_sync_detail", role=UserRole.USER)
    rec = ZentaoSyncService(db_session).receive_event(ZentaoBrowserSyncPayload(**_base_bug_payload("bug_non_admin_detail")))
    client = _make_client(db_session, normal_user)
    try:
        resp = client.get(f"/api/integrations/zentao/browser-events/{rec['event_id']}")
        assert resp.status_code == 200
    finally:
        client.close()


def test_bug_major_match_keeps_legacy_s4030(db_session):
    software = SoftwareProduct(name="Survey Master")
    db_session.add(software)
    db_session.commit()
    db_session.refresh(software)
    major = _create_major(db_session, software.id, "s4030")
    service = ZentaoSyncService(db_session)

    payload = _mapped_bug_payload(
        "bug_legacy_s4030",
        requirement_id=None,
        requirement_name=None,
        affected_version=None,
        execution_name="s4030(V4.0.3.0)",
    )
    rec = service.receive_event(ZentaoBrowserSyncPayload(**payload))
    row = db_session.query(BrowserSyncEvent).filter(BrowserSyncEvent.id == rec["event_id"]).first()
    assert row.mapped_major_version_id == major.id


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
