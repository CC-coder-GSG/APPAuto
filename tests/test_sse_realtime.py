from __future__ import annotations

from app.models import BrowserSyncEvent
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


def test_sse_bus_channel_filtering():
    base = sse_publish("dummy_base", {"ok": True}, channels=["global"])
    sse_publish("user_only", {"ok": True}, channels=["user:100"])
    sse_publish("global_evt", {"ok": True}, channels=["global"])

    global_events = sse_pull_since(base, channels={"global"})
    user_events = sse_pull_since(base, channels={"user:100"})
    assert any(evt.event == "global_evt" for evt in global_events)
    assert not any(evt.event == "user_only" for evt in global_events)
    assert any(evt.event == "user_only" for evt in user_events)


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
