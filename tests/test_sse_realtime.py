from __future__ import annotations

import json
import uuid

from fastapi.testclient import TestClient

from app.api.deps import get_db
from app.core.security import create_access_token
from app.main import app
from app.models import BrowserSyncEvent
from app.models.enums import UserRole
from app.models.user import User
from app.schemas.zentao_sync import ZentaoBrowserSyncPayload
from app.services.sse_service import sse_pull_since, sse_publish
from app.services.zentao_sync_service import ZentaoSyncService


def _bug_payload(client_record_id: str) -> dict:
    return {
        "entityType": "bug",
        "action": "create",
        "source": "tampermonkey",
        "clientRecordId": client_record_id,
        "capturedAt": 1774000000000,
        "topHref": "http://zentao/bug-browse",
        "draft": {
            "bugTitle": "sse bug",
            "executionId": "s4030",
            "executionName": "s4030(V4.0.3.0)",
            "creatorName": "sse-user",
        },
        "result": {"zentaoBugId": "98001", "zentaoBugUrl": "http://zentao/bug-view-98001.html"},
    }


def _make_auth_headers(db_session) -> dict[str, str]:
    username = f"sse_tester_{uuid.uuid4().hex[:8]}"
    session_token = uuid.uuid4().hex
    user = User(
        username=username,
        display_name="SSE Tester",
        password_hash=User.hash_password("pass"),
        role=UserRole.USER,
        session_token=session_token,
    )
    db_session.add(user)
    db_session.commit()
    token = create_access_token({"sub": username, "session": session_token})
    return {"Authorization": f"Bearer {token}"}


def _build_client_with_db_override(db_session) -> TestClient:
    def _override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = _override_get_db
    return TestClient(app)


def _collect_stream_text(response, *, max_chunks: int = 8) -> str:
    chunks: list[str] = []
    for idx, chunk in enumerate(response.iter_text()):
        if chunk:
            chunks.append(chunk)
        if idx + 1 >= max_chunks:
            break
        merged = "".join(chunks)
        if "\n\n" in merged and "data:" in merged:
            break
    return "".join(chunks)


def test_sse_bus_channel_filtering():
    base = sse_publish("dummy_base", {"ok": True}, channels=["global"])
    sse_publish("user_only", {"ok": True}, channels=["user:100"])
    sse_publish("global_evt", {"ok": True}, channels=["global"])

    global_events = sse_pull_since(base, channels={"global"})
    user_events = sse_pull_since(base, channels={"user:100"})
    assert any(evt.event == "global_evt" for evt in global_events)
    assert not any(evt.event == "user_only" for evt in global_events)
    assert any(evt.event == "user_only" for evt in user_events)


def test_sse_stream_requires_token(db_session):
    client = _build_client_with_db_override(db_session)
    try:
        resp = client.get("/api/sse/stream")
        assert resp.status_code == 401
    finally:
        app.dependency_overrides.clear()
        client.close()


def test_sse_stream_with_valid_token_reads_event(db_session):
    client = _build_client_with_db_override(db_session)
    try:
        headers = _make_auth_headers(db_session)
        marker = sse_publish("stream_marker", {"ok": True}, channels=["global"])
        evt_id = sse_publish("zentao_sync_created", {"item": {"id": 321}}, channels=["global"])

        with client.stream("GET", f"/api/sse/stream?last_event_id={marker}", headers=headers) as resp:
            assert resp.status_code == 200
            text = _collect_stream_text(resp)

        assert f"id: {evt_id}" in text
        assert "event: zentao_sync_created" in text
        assert "data:" in text
        data_line = next((ln for ln in text.splitlines() if ln.startswith("data: ")), "")
        payload = json.loads(data_line[6:])
        assert payload["id"] == evt_id
        assert payload["type"] == "zentao_sync_created"
        assert payload["payload"]["item"]["id"] == 321
    finally:
        app.dependency_overrides.clear()
        client.close()


def test_sse_stream_last_event_id_only_returns_newer(db_session):
    client = _build_client_with_db_override(db_session)
    try:
        headers = _make_auth_headers(db_session)
        first = sse_publish("evt_first", {"index": 1}, channels=["global"])
        second = sse_publish("evt_second", {"index": 2}, channels=["global"])

        with client.stream("GET", f"/api/sse/stream?last_event_id={first}", headers=headers) as resp:
            assert resp.status_code == 200
            text = _collect_stream_text(resp)

        assert f"id: {second}" in text
        assert "event: evt_second" in text
        assert f"id: {first}" not in text
        assert "event: evt_first" not in text
    finally:
        app.dependency_overrides.clear()
        client.close()


def test_sse_stream_user_channel_filtering(db_session):
    client = _build_client_with_db_override(db_session)
    try:
        headers = _make_auth_headers(db_session)

        # Resolve user id from token-created account for user channel test.
        auth_user = db_session.query(User).order_by(User.id.desc()).first()
        assert auth_user is not None

        cursor = sse_publish("cursor_marker", {"ok": True}, channels=["global"])
        sse_publish("user_private_event", {"scope": "mine"}, channels=[f"user:{auth_user.id}"])
        sse_publish("other_user_private", {"scope": "other"}, channels=["user:999999"])

        with client.stream("GET", f"/api/sse/stream?last_event_id={cursor}", headers=headers) as resp:
            assert resp.status_code == 200
            text = _collect_stream_text(resp, max_chunks=10)

        assert "event: user_private_event" in text
        assert "event: other_user_private" not in text
    finally:
        app.dependency_overrides.clear()
        client.close()


def test_zentao_sync_publish_created_updated_deleted(db_session):
    service = ZentaoSyncService(db_session)
    cursor = sse_publish("dummy_cursor", {"cursor": True}, channels=["global"])
    rec = service.receive_event(ZentaoBrowserSyncPayload(**_bug_payload("bug_sse_created")))
    created_events = sse_pull_since(cursor, channels={"global"})
    assert any(evt.event == "zentao_sync_created" for evt in created_events)

    row = db_session.query(BrowserSyncEvent).filter(BrowserSyncEvent.id == rec["event_id"]).first()
    service.map_event(
        row.id,
        requirement_id=None,
        minor_version_id=None,
        source_type="manual",
        source_ref="sse-ref",
        note=None,
        actor_id=None,
        display_bucket="overall",
        linked_case_id=None,
    )
    updated_events = sse_pull_since(cursor, channels={"global"})
    assert any(evt.event == "zentao_sync_updated" for evt in updated_events)

    service.delete_event(row.id, actor_id=None)
    deleted_events = sse_pull_since(cursor, channels={"global"})
    assert any(evt.event == "zentao_sync_deleted" for evt in deleted_events)
