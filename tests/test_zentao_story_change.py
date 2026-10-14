from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.models import Requirement, User, UserRole, Version, VersionType
from app.models.zentao_task_mirror import ZentaoTaskMirror
from app.services.zentao_task_status import (
    apply_task_story_change, effective_task_status, task_story_change_pending,
)
from app.services.zentao_web_session import (
    ZentaoStoryChangeError, ZentaoWebLogin,
    confirm_task_story_change_via_web, get_task_story_change_via_web,
)
import app.services.zentao_web_session as web
import app.services.zentao_story_change_service as service
import app.services.zentao_task_sync_service as sync
import app.services.zentao_task_mirror_service as mirrors


def changed_task(**overrides):
    return {"id": 19924, "story": 7276, "storyVersion": 1, "latestStoryVersion": 3,
            "status": "changed", "rawStatus": "done", "needConfirm": True,
            "assignedTo": "alice", "name": "测试任务", "type": "test", "left": 0,
            "finishedDate": "2026-09-29 09:44:05", **overrides}


@pytest.mark.parametrize("value,expected", [(True, True), ("true", True), (1, True),
    (False, False), ("false", False), (0, False), ("0", False)])
def test_pending_boolean_shapes(value, expected):
    assert task_story_change_pending({"needConfirm": value}) is expected


def test_completion_and_acknowledgement_are_independent():
    assert effective_task_status(changed_task()) == "done"
    assert task_story_change_pending(changed_task()) is True
    assert effective_task_status(changed_task(rawStatus="wait")) == "wait"
    assert task_story_change_pending(changed_task(needConfirm=False)) is True
    assert task_story_change_pending({"status": "changed", "story": 7276}) is True


def test_sparse_payload_does_not_erase_pending_cache():
    target = SimpleNamespace(task_story_change_pending=True, task_story_version=1, task_latest_story_version=3)
    assert apply_task_story_change(target, {"status": "done"}) is False
    assert target.task_story_change_pending is True
    apply_task_story_change(target, changed_task(needConfirm=False, storyVersion=3))
    assert target.task_story_change_pending is False
    assert target.task_story_version == 3


LOGIN = ZentaoWebLogin("http://zentao.test/zentao", "alice", "unused-test-password")
CONFIRM_ARGS = {"story_id": 7276, "expected_story_version": 1, "expected_latest_story_version": 3}


def install_web_transport(monkeypatch, *, before=None, mode="success"):
    state = {"task": before or changed_task(), "writes": 0, "reads": [], "closed": False}

    def handle(request):
        path = request.url.path
        state["reads"].append(path)
        if "confirmStoryChange" in path:
            state["writes"] += 1
            assert request.method == "GET"
            if mode == "denied":
                return httpx.Response(200, json={"result": "fail", "message": "no permission"})
            version = 4 if mode == "race" else 3
            state["task"] = changed_task(status="done", rawStatus="done", needConfirm=False,
                                         storyVersion=version, latestStoryVersion=version)
            if mode in {"timeout", "ambiguous"}:
                raise httpx.ReadTimeout("response lost", request=request)
            return httpx.Response(200, json={"result": "success"})
        if "task-view" in path:
            if state["writes"] and mode == "ambiguous":
                raise httpx.ReadTimeout("readback unavailable", request=request)
            return httpx.Response(200, json={"status": "success", "data": {"task": state["task"]}})
        if "story-view" in path:
            version = int(path.rsplit("-", 1)[-1].split(".")[0])
            if version == 1 and mode == "old_missing":
                return httpx.Response(404, text="missing")
            # IPD history retains story.version=latest; the outer version is authoritative.
            return httpx.Response(200, json={"status": "success", "data": {
                "version": version, "story": {"id": 7276, "version": 3, "title": "需求",
                    "spec": f"<p>version-{version}</p>", "verify": "<p>验收标准</p>"}}})
        return httpx.Response(200, text="")

    monkeypatch.setattr(web, "_open_web_session", lambda login: httpx.Client(transport=httpx.MockTransport(handle)))
    return state


@pytest.mark.parametrize("mode", ["success", "old_missing"])
def test_preview_is_read_only_and_requests_correct_history(monkeypatch, mode):
    state = install_web_transport(monkeypatch, mode=mode)
    preview = get_task_story_change_via_web(LOGIN, 19924)
    assert state["writes"] == 0
    assert preview["story_version"] == 1
    assert preview["latest_story_version"] == 3
    assert preview["story"]["spec"] == "<p>version-3</p>"
    assert preview["can_confirm"] is True
    if mode == "old_missing":
        assert preview["previous_story"] is None and preview["previous_error"]
    else:
        assert preview["previous_story"]["spec"] == "<p>version-1</p>"


@pytest.mark.parametrize("mode", ["success", "timeout", "race"])
def test_confirmation_is_single_write_and_verified_even_after_timeout(monkeypatch, mode):
    state = install_web_transport(monkeypatch, mode=mode)
    result = confirm_task_story_change_via_web(LOGIN, 19924, **CONFIRM_ARGS)
    assert state["writes"] == 1
    assert result["task"]["storyVersion"] == (4 if mode == "race" else 3)
    assert result["version_changed_during_confirm"] is (mode == "race")


@pytest.mark.parametrize("before,status", [
    (changed_task(assignedTo="bob"), 403),
    (changed_task(story=7277), 409),
    (changed_task(latestStoryVersion=4), 409),
    (changed_task(storyVersion=2), 409),
    (changed_task(mode="multi", team=[{"account": "alice"}]), 409),
])
def test_permission_and_version_changes_prevent_any_write(monkeypatch, before, status):
    state = install_web_transport(monkeypatch, before=before)
    with pytest.raises(ZentaoStoryChangeError) as error:
        confirm_task_story_change_via_web(LOGIN, 19924, **CONFIRM_ARGS)
    assert error.value.status_code == status
    assert state["writes"] == 0


def test_already_confirmed_is_idempotent(monkeypatch):
    state = install_web_transport(monkeypatch, before=changed_task(storyVersion=3, needConfirm=False))
    result = confirm_task_story_change_via_web(LOGIN, 19924, **CONFIRM_ARGS)
    assert result["already_confirmed"] is True
    assert state["writes"] == 0


@pytest.mark.parametrize("mode", ["denied", "ambiguous"])
def test_failed_confirmation_does_not_retry_or_claim_success(monkeypatch, mode):
    state = install_web_transport(monkeypatch, mode=mode)
    with pytest.raises(ZentaoStoryChangeError):
        confirm_task_story_change_via_web(LOGIN, 19924, **CONFIRM_ARGS)
    assert state["writes"] == 1


@pytest.fixture()
def records(db_session):
    user = User(username="alice_story_change", zentao_account="alice", password_hash="x", role=UserRole.USER)
    major = Version(version_no="STORY-CHANGE", version_type=VersionType.MAJOR, zentao_execution_id=1910)
    db_session.add_all([user, major])
    db_session.flush()
    req = Requirement(zentao_req_id="r#7276", zentao_story_id=7276, title="需求",
        owner_id=user.id, major_version_id=major.id, zentao_task_id=19924,
        zentao_task_status_cache="done", zentao_task_assigned_to="alice", test_completed=True,
        test_completed_at=datetime(2026, 9, 29, 9, 44, 5), task_efforts_submitted=0.73)
    mirror = ZentaoTaskMirror(task_id=19924, execution_id=1910, name="测试任务", story=7276,
        status="done", assigned_to="alice", assignee_user_id=user.id, consumed=0.73)
    db_session.add_all([req, mirror])
    db_session.commit()
    return user, req, mirror


def test_sync_keeps_pending_for_completed_requirement_and_pending_workbench(db_session, monkeypatch, records):
    from app.services.workbench_service import WorkbenchService
    user, req, mirror = records
    client = SimpleNamespace(list_execution_tasks=lambda *args, **kwargs: [changed_task(rawStatus="changed")])
    monkeypatch.setattr(sync, "get_system_zentao_client", lambda db: client)
    sync.ZentaoTaskSyncService(db_session).sync_tasks_status_for_major(req.major_version_id)
    assert req.task_story_change_pending is True and mirror.task_story_change_pending is True
    assert req.zentao_task_status_cache == "done"
    monkeypatch.setattr(WorkbenchService, "_maybe_sync_task_status", lambda *args: None)
    rows = WorkbenchService(db_session).get_my_workbench(current_user=user, mode="all_pending")
    assert rows[0]["id"] == req.id and rows[0]["task_story_change_pending"] is True
    assert rows[0]["test_completed"] is True


def test_pending_remote_completion_does_not_mark_new_local_test_passed(db_session, monkeypatch, records):
    _, req, _ = records
    req.test_completed = False
    db_session.commit()
    client = SimpleNamespace(list_execution_tasks=lambda *args, **kwargs: [changed_task()])
    monkeypatch.setattr(sync, "get_system_zentao_client", lambda db: client)
    sync.ZentaoTaskSyncService(db_session).sync_tasks_status_for_major(req.major_version_id)
    assert req.test_completed is False
    assert req.task_story_change_pending is True


@pytest.mark.parametrize("action", ["start", "pause", "finish", "reactivate"])
def test_requirement_lifecycle_blocks_before_effort_side_effects(db_session, monkeypatch, records, action):
    user, req, _ = records
    client = SimpleNamespace(get_task=lambda task_id: changed_task())
    monkeypatch.setattr(sync, "get_system_zentao_client", lambda db: client)
    monkeypatch.setattr(sync, "get_user_zentao_client", lambda *args: client)
    with pytest.raises(HTTPException) as error:
        getattr(sync.ZentaoTaskSyncService(db_session), f"{action}_requirement_task")(req, acting_user=user)
    assert error.value.status_code == 409
    assert req.test_completed is True
    assert req.task_efforts_submitted == 0.73


@pytest.mark.parametrize("action", ["start", "pause", "finish", "reactivate", "close", "cancel"])
def test_board_lifecycle_blocks_pending(db_session, monkeypatch, records, action):
    user, _, mirror = records
    client = SimpleNamespace(get_task=lambda task_id: changed_task())
    monkeypatch.setattr(mirrors, "get_system_zentao_client", lambda db: client)
    monkeypatch.setattr(mirrors, "get_user_zentao_client", lambda *args: client)
    with pytest.raises(HTTPException) as error:
        mirrors.ZentaoTaskMirrorService(db_session).operate_task(task_id=mirror.task_id, action=action, current_user=user)
    assert error.value.status_code == 409


def test_service_confirm_updates_both_views_without_resetting_test_or_hours(db_session, monkeypatch, records):
    user, req, mirror = records
    original_completed_at = req.test_completed_at
    req.task_story_change_pending = mirror.task_story_change_pending = True
    monkeypatch.setattr(service, "get_user_zentao_web_login", lambda *args: LOGIN)
    monkeypatch.setattr(service, "confirm_task_story_change_via_web", lambda *args, **kwargs: {
        "task": changed_task(status="done", storyVersion=3, needConfirm=False, consumed=0.73),
        "already_confirmed": False, "version_changed_during_confirm": False})
    result = service.ZentaoStoryChangeService(db_session).confirm(19924, user, **CONFIRM_ARGS)
    assert result["task_story_change_pending"] is False
    assert req.task_story_change_pending is mirror.task_story_change_pending is False
    assert req.test_completed is True and req.test_completed_at == original_completed_at
    assert req.task_efforts_submitted == 0.73 and mirror.consumed == 0.73
    from app.models import AuditLog
    audit = db_session.query(AuditLog).filter(AuditLog.action == "zentao_task.confirm_story_change").one()
    assert audit.actor_id == user.id


def test_api_uses_personal_binding_and_validates_versions(db_session, monkeypatch, records):
    from app.api.deps import get_current_user, get_db
    from app.api.routes.zentao_hydrate import router
    user, _, _ = records
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_db] = lambda: db_session
    monkeypatch.setattr(service, "get_user_zentao_web_login", lambda *args: None)
    with TestClient(app) as client:
        assert client.get('/zentao/tasks/19924/story-change').status_code == 400
        assert client.post('/zentao/tasks/19924/confirm-story-change', json={**CONFIRM_ARGS, "story_id": 0}).status_code == 422
        assert client.post('/zentao/tasks/19924/confirm-story-change', json=CONFIRM_ARGS).status_code == 400


def test_existing_database_columns_migrate_idempotently_without_losing_tasks(db_session, records):
    from sqlalchemy import text
    from app.db.seed import ensure_requirement_schema_compat, ensure_zentao_task_mirror_schema_compat
    _, req, mirror = records
    fields = {"task_story_change_pending", "task_story_version", "task_latest_story_version", "task_change_checked_at"}
    for table in ("requirements", "zentao_task_mirror"):
        db_session.execute(text(f"DROP INDEX ix_{table}_task_story_change_pending"))
        for field in fields:
            db_session.execute(text(f"ALTER TABLE {table} DROP COLUMN {field}"))
    db_session.commit()
    for _ in range(2):
        ensure_requirement_schema_compat(db_session)
        ensure_zentao_task_mirror_schema_compat(db_session)
    db_session.expire_all()
    assert req.zentao_task_id == 19924 and req.test_completed is True
    assert mirror.consumed == 0.73
    for table in ("requirements", "zentao_task_mirror"):
        columns = {row[1] for row in db_session.execute(text(f"PRAGMA table_info({table})"))}
        assert fields <= columns
    assert req.task_story_change_pending is None


def test_background_mirror_sync_publishes_acknowledgement_change(db_session, monkeypatch, records):
    _, req, mirror = records
    client = SimpleNamespace(list_execution_tasks=lambda *args, **kwargs: [changed_task()])
    monkeypatch.setattr(mirrors, "get_system_zentao_client", lambda db: client)
    events = []
    monkeypatch.setattr(mirrors, "sse_publish", lambda event, payload, **kwargs: events.append((event, payload)))
    result = mirrors.ZentaoTaskMirrorService(db_session).sync_one_major(req.major_version_id)
    assert result["ok"] is True
    assert mirror.task_story_change_pending is req.task_story_change_pending is True
    assert any(event == "zentao_task_changed" and payload["task_ids"] == [19924] for event, payload in events)


def test_final_test_only_blocks_checkbox_that_drives_pending_zentao_task(db_session, monkeypatch, records):
    from app.models import FinalTestRecord
    from app.services.final_test_service import FinalTestService
    user, req, _ = records
    req.major_version.final_test_enabled = True
    other = User(username="other_final_story_change", zentao_account="bob", password_hash="x", role=UserRole.USER)
    record = FinalTestRecord(requirement_id=req.id, user_id=user.id, test_completed=False)
    db_session.add_all([record, other])
    db_session.commit()
    client = SimpleNamespace(get_task=lambda task_id: changed_task())
    monkeypatch.setattr(sync, "get_system_zentao_client", lambda db: client)
    monkeypatch.setattr(sync, "get_user_zentao_client", lambda *args: client)
    with pytest.raises(HTTPException) as error:
        FinalTestService(db_session).upsert_record(req.id, user, test_completed=True)
    assert error.value.status_code == 409
    assert record.test_completed is False
    # Another tester's personal final-test result does not drive this task.
    result = FinalTestService(db_session).upsert_record(req.id, other, test_completed=True)
    assert result["test_completed"] is True
    assert req.test_completed is True and req.task_efforts_submitted == 0.73


def test_mismatched_local_story_binding_prevents_confirmation(db_session, monkeypatch, records):
    user, req, _ = records
    req.zentao_story_id = 7277
    db_session.commit()
    monkeypatch.setattr(service, "get_user_zentao_web_login", lambda *args: LOGIN)
    def unexpected_write(*args, **kwargs):
        pytest.fail("a mismatched requirement must not be confirmed")
    monkeypatch.setattr(service, "confirm_task_story_change_via_web", unexpected_write)
    with pytest.raises(HTTPException) as error:
        service.ZentaoStoryChangeService(db_session).confirm(19924, user, **CONFIRM_ARGS)
    assert error.value.status_code == 409
