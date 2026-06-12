from __future__ import annotations

import json
import uuid

from fastapi.testclient import TestClient

from app.api.deps import get_db
from app.core.security import create_access_token
from app.main import app
from app.models import BugSourceType
from app.models.enums import UserRole
from app.models.user import User
from app.services.sse_service import sse_pull_since, sse_publish
from app.services.stage5_service import Stage5Service


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


def test_stage5_add_issue_publishes_overall_bug_created(db_session):
    user = User(
        username=f"stage5_tester_{uuid.uuid4().hex[:8]}",
        display_name="Stage5 SSE Tester",
        password_hash=User.hash_password("pass"),
        role=UserRole.USER,
        session_token=uuid.uuid4().hex,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    service = Stage5Service(db_session)
    cursor = sse_publish("stage5_cursor", {"ok": True}, channels=["global"])
    out = service.add_issue(
        major_version_id=1,
        requirement_id=None,
        source_type=BugSourceType.MANUAL,
        source_ref="stage5-manual",
        bug_id=f"b#{uuid.uuid4().hex[:6]}",
        minor_version_id=2,
        current_user=user,
    )
    assert out.get("id")

    events = sse_pull_since(cursor, channels={"global"})
    created_evt = next((evt for evt in events if evt.event == "overall_bug_created"), None)
    assert created_evt is not None
    assert created_evt.data["payload"]["id"] == out["id"]
