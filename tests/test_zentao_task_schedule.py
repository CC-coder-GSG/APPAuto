from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi.responses import JSONResponse

from app.api.deps import get_current_user, get_db
from app.api.routes import requirements as routes
from app.models import AuditLog, Requirement, User, UserRole, Version, VersionType
from app.models.zentao_task_mirror import ZentaoTaskMirror
from app.core.exceptions import AppError
from app.services.zentao_client_service import ZentaoClient
from app.services.zentao_task_schedule_service import ZentaoTaskScheduleService
import app.services.zentao_task_schedule_service as schedules

START, END = "2026-10-15", "2026-10-20"


class ScheduleClient:
    def __init__(self, tasks):
        self.tasks = deepcopy(tasks)
        self.writes = []
        self.reads = []
        self.fail_read = set()
        self.modes = {}

    def get_task(self, tid):
        self.reads.append(tid)
        if tid in self.fail_read or self.modes.get(tid) == "unverified" and any(w[0] == tid for w in self.writes):
            raise httpx.ReadTimeout("simulated read failure")
        return deepcopy(self.tasks.get(tid))

    def update_task_dates(self, tid, *, est_started, deadline):
        self.writes.append((tid, {"estStarted": est_started, "deadline": deadline}))
        mode = self.modes.get(tid)
        if mode not in {"refused", "timeout_before"}:
            self.tasks[tid].update(estStarted=est_started, deadline=deadline)
        if mode in {"timeout_after", "timeout_before", "unverified"}:
            raise httpx.ReadTimeout("simulated write timeout")
        return {}  # Empty successful PUT is never sufficient proof by itself.


@pytest.fixture()
def setup(db_session, monkeypatch):
    major = Version(version_no="V-schedule", version_type=VersionType.MAJOR, zentao_execution_id=1910, software_id=1)
    other = Version(version_no="V-other", version_type=VersionType.MAJOR, zentao_execution_id=1911, software_id=2)
    actor = User(username="scheduler", password_hash="x", role=UserRole.ADMIN)
    owner = User(username="owner", password_hash="x", zentao_account="owner")
    db_session.add_all([major, other, actor, owner])
    db_session.flush()
    reqs = [Requirement(zentao_req_id=f"r#{7276+i}", title=f"需求{i}", major_version_id=major.id,
                        zentao_story_id=7276+i, zentao_task_id=19924+i if i != 1 else None,
                        zentao_parent_task_id=19123 if i != 1 else None,
                        owner_id=owner.id if i else None, case_completed=True, test_completed=True,
                        task_story_change_pending=True, task_story_version=1, task_latest_story_version=3,
                        task_consumed_accum=2, task_efforts_submitted=4, task_started_at=datetime(2026, 10, 1, 9))
            for i in range(3)]
    foreign = Requirement(zentao_req_id="r#8888", title="其他产品需求", major_version_id=other.id)
    mirror = ZentaoTaskMirror(task_id=19924, execution_id=1910, name="测试需求", parent=19123,
                              est_started=date(2026, 9, 14), deadline=date(2026, 9, 18),
                              local_started_at=datetime(2026, 10, 1, 9), consumed_accum=2, efforts_submitted=4)
    parent = ZentaoTaskMirror(task_id=19123, execution_id=1910, name="父任务", is_parent=True)
    db_session.add_all([*reqs, foreign, mirror, parent])
    db_session.commit()
    tasks = {r.zentao_task_id: {"id": r.zentao_task_id, "execution": 1910, "parent": 19123,
             "story": r.zentao_story_id, "type": "test", "isParent": 0, "name": r.title,
             "estStarted": "2026-09-14", "deadline": "2026-09-18", "status": "changed", "rawStatus": "done",
             "needConfirm": True, "storyVersion": 1, "latestStoryVersion": 3, "assignedTo": "owner",
             "estimate": 8, "consumed": 8, "left": 0} for r in reqs if r.zentao_task_id}
    tasks[19123] = {"id": 19123, "execution": 1910, "estStarted": START, "deadline": END}
    client = ScheduleClient(tasks)
    monkeypatch.setattr(schedules, "get_system_zentao_client", lambda db: client)
    app = FastAPI()
    @app.exception_handler(AppError)
    async def app_error_handler(request, exc):
        return JSONResponse({"detail": exc.message}, status_code=exc.status_code)
    app.include_router(routes.router)
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_current_user] = lambda: actor
    return {"major": major, "other": other, "actor": actor, "owner": owner, "reqs": reqs,
            "foreign": foreign, "mirror": mirror, "parent": parent, "client": client, "api": TestClient(app)}


def _payload(setup, ids=None, start=START, end=END):
    return {"major_version_id": setup["major"].id,
            "requirement_ids": ids if ids is not None else [r.id for r in setup["reqs"]],
            "task_start_date": start, "task_deadline": end}


def test_selected_update_skips_unbuilt_and_preserves_lifecycle(db_session, setup):
    req = setup["reqs"][0]
    before = deepcopy(setup["client"].tasks[19924])
    response = setup["api"].post("/requirements/update-zentao-task-dates", json=_payload(setup, [req.id, setup["reqs"][1].id]))
    assert response.status_code == 200
    data = response.json()
    assert data["counts"] == {"updated": 1, "unchanged": 0, "skipped": 1, "failed": 0, "unverified": 0}
    assert setup["client"].writes == [(19924, {"estStarted": START, "deadline": END})]
    assert setup["client"].tasks[19926]["estStarted"] == "2026-09-14"  # unselected
    for key in ("status", "rawStatus", "needConfirm", "storyVersion", "latestStoryVersion", "assignedTo", "estimate", "consumed", "left"):
        assert setup["client"].tasks[19924][key] == before[key]
    db_session.refresh(req)
    assert req.owner_id is None  # no owner is required for dates
    assert req.test_completed and req.case_completed and req.task_story_change_pending
    assert (req.task_consumed_accum, req.task_efforts_submitted, req.task_started_at) == (2, 4, datetime(2026, 10, 1, 9))
    db_session.refresh(setup["mirror"])
    assert (setup["mirror"].est_started, setup["mirror"].deadline) == (date.fromisoformat(START), date.fromisoformat(END))
    assert (setup["mirror"].consumed_accum, setup["mirror"].efforts_submitted) == (2, 4)
    db_session.refresh(setup["parent"])
    assert setup["parent"].deadline == date.fromisoformat(END)
    assert db_session.query(AuditLog).filter(AuditLog.action == "zentao_task.update_dates").count() == 1


def test_identical_dates_no_write_but_refreshes_cache(db_session, setup):
    task = setup["client"].tasks[19924]
    task.update(estStarted=START, deadline=END)
    data = setup["api"].post("/requirements/update-zentao-task-dates", json=_payload(setup, [setup["reqs"][0].id])).json()
    assert data["counts"]["unchanged"] == 1
    assert setup["client"].writes == []
    db_session.refresh(setup["mirror"])
    assert setup["mirror"].est_started == date.fromisoformat(START)


@pytest.mark.parametrize("start,end", [(None, END), (START, None), ("", END), ("2026-02-30", END),
    ("2026-1-1", END), ("2026-10-15T00:00:00", END), ("2026-10-21", END), ("0000-01-01", END)])
def test_invalid_dates_block_whole_batch(db_session, setup, start, end):
    response = setup["api"].post("/requirements/update-zentao-task-dates", json=_payload(setup, start=start, end=end))
    assert response.status_code == 400
    assert not setup["client"].reads and not setup["client"].writes
    assert db_session.query(AuditLog).filter(AuditLog.action == "zentao_task.update_dates").count() == 0


def test_same_day_is_valid(setup):
    data = setup["api"].post("/requirements/update-zentao-task-dates", json=_payload(setup, start=START, end=START)).json()
    assert data["counts"]["updated"] == 2


@pytest.mark.parametrize("kind", ["empty", "missing", "foreign"])
def test_invalid_selection_has_no_remote_writes(setup, kind):
    ids = {"empty": [], "missing": [setup["reqs"][0].id, 99999], "foreign": [setup["reqs"][0].id, setup["foreign"].id]}[kind]
    response = setup["api"].post("/requirements/update-zentao-task-dates", json=_payload(setup, ids))
    assert response.status_code == 400
    assert not setup["client"].reads


@pytest.mark.parametrize("field,value", [("id", 3), ("execution", 1911), ("story", 999), ("parent", 0),
    ("parent", 999), ("isParent", 1), ("type", "devel"), ("deleted", "1")])
def test_wrong_task_bindings_are_skipped(setup, field, value):
    setup["client"].tasks[19924][field] = value
    data = setup["api"].post("/requirements/update-zentao-task-dates", json=_payload(setup, [setup["reqs"][0].id])).json()
    assert data["counts"]["skipped"] == 1
    assert not setup["client"].writes


@pytest.mark.parametrize("mode,status", [("refused", "failed"), ("timeout_after", "updated"),
    ("timeout_before", "unverified"), ("unverified", "unverified")])
def test_readback_determines_partial_outcome(setup, mode, status):
    setup["client"].modes[19924] = mode
    data = setup["api"].post("/requirements/update-zentao-task-dates", json=_payload(setup)).json()
    assert data["results"][0]["status"] == status
    assert data["results"][2]["status"] == "updated"
    assert sum(data["counts"].values()) == 3
    assert data["ok"] == (status == "updated")


def test_unavailable_task_is_failure_not_unbuilt_skip(setup):
    setup["client"].fail_read.add(19924)
    data = setup["api"].post("/requirements/update-zentao-task-dates", json=_payload(setup)).json()
    assert data["counts"]["failed"] == 1 and data["counts"]["skipped"] == 1
    assert [w[0] for w in setup["client"].writes] == [19926]


def test_shared_task_written_once_and_conflicting_bindings_skip(db_session, setup):
    first, _, third = setup["reqs"]
    third.zentao_task_id = first.zentao_task_id
    third.zentao_story_id = first.zentao_story_id
    db_session.commit()
    data = setup["api"].post("/requirements/update-zentao-task-dates", json=_payload(setup, [first.id, first.id, third.id])).json()
    assert data["total"] == 2 and data["counts"]["updated"] == 2
    assert len(setup["client"].writes) == 1
    third.zentao_story_id = 8888
    db_session.commit()
    setup["client"].writes.clear()
    data = setup["api"].post("/requirements/update-zentao-task-dates", json=_payload(setup, [first.id, third.id])).json()
    assert data["counts"]["skipped"] == 2 and not setup["client"].writes


def test_cache_failure_keeps_verified_remote_success(monkeypatch, setup):
    monkeypatch.setattr(ZentaoTaskScheduleService, "_cache", lambda *a: (_ for _ in ()).throw(RuntimeError("cache unavailable")))
    data = setup["api"].post("/requirements/update-zentao-task-dates", json=_payload(setup, [setup["reqs"][0].id])).json()
    assert data["results"][0]["status"] == "updated"
    assert "缓存" in data["results"][0]["warning"]


def test_rest_client_sends_dates_only(monkeypatch):
    seen = []
    monkeypatch.setattr(ZentaoClient, "put", lambda self, path, body: seen.append((path, body)))
    ZentaoClient("http://zentao.test", "test-token").update_task_dates(19924, est_started=START, deadline=END)
    assert seen == [("tasks/19924", {"estStarted": START, "deadline": END})]


@pytest.mark.parametrize("url", ["/requirements/assign-and-publish", "/requirements/create-zentao-tasks"])
@pytest.mark.parametrize("invalid", ["dates", "foreign", "owner", "empty", "conflict"])
def test_publication_preflight_before_local_assignment_remote_and_notification(db_session, monkeypatch, setup, url, invalid):
    req = setup["reqs"][2]
    sent, remote = [], []
    async def send(text):
        sent.append(text)
    monkeypatch.setattr(routes, "send_markdown", send)
    monkeypatch.setattr(routes.ZentaoTaskSyncService, "create_tasks_for_assignment", lambda *a, **kw: remote.append(a))
    body = {"major_version_id": setup["major"].id, "assignments": [{"requirement_id": req.id, "owner_id": None}],
            "task_start_date": START, "task_deadline": END}
    if invalid == "dates": body["task_start_date"] = None
    if invalid == "foreign": body["assignments"].append({"requirement_id": setup["foreign"].id})
    if invalid == "owner": body["assignments"][0]["owner_id"] = 99999
    if invalid == "empty": body["assignments"] = []
    if invalid == "conflict": body["assignments"].append({"requirement_id": req.id, "owner_id": setup["actor"].id})
    response = setup["api"].post(url, json=body)
    assert response.status_code == 400
    assert not sent and not remote
    db_session.refresh(req)
    assert req.owner_id == setup["owner"].id and req.test_completed


@pytest.mark.parametrize("url", ["/requirements/assign-and-publish", "/requirements/create-zentao-tasks"])
def test_publication_passes_only_selected_deduplicated_requirements(db_session, monkeypatch, setup, url):
    sent, remote = [], []
    async def send(text): sent.append(text)
    def create(self, major_id, assignments, **kwargs):
        remote.extend(assignments)
        return {"ok": True, "created_tasks": [], "errors": []}
    monkeypatch.setattr(routes, "send_markdown", send)
    monkeypatch.setattr(routes.ZentaoTaskSyncService, "create_tasks_for_assignment", create)
    req = setup["reqs"][0]
    assignment = {"requirement_id": req.id, "owner_id": setup["actor"].id}
    response = setup["api"].post(url, json={"major_version_id": setup["major"].id, "assignments": [assignment, assignment],
        "task_start_date": START, "task_deadline": END})
    assert response.status_code == 200
    assert remote == [assignment]
    db_session.refresh(setup["reqs"][2])
    assert setup["reqs"][2].owner_id == setup["owner"].id and setup["reqs"][2].test_completed
    db_session.refresh(req)
    assert req.owner_id == (setup["actor"].id if url.endswith("assign-and-publish") else None)
    assert bool(sent) == url.endswith("assign-and-publish")


def test_list_exposes_cached_dates_and_conjunctive_software_filter(setup):
    api = setup["api"]
    rows = api.get(f"/requirements/admin/list?major_version_id={setup['major'].id}&software_id=1").json()
    assert rows[0]["zentao_task_id"] == 19924
    assert rows[0]["task_dates_synced"] is True
    assert rows[2]["task_dates_synced"] is False
    assert rows[0]["task_start_date"] == "2026-09-14" and rows[0]["task_deadline"] == "2026-09-18"
    assert api.get(f"/requirements/admin/list?major_version_id={setup['major'].id}&software_id=2").json() == []


def test_assignment_tab_permission_is_required(db_session, setup):
    setup["actor"].role = UserRole.USER
    setup["actor"].tab_permissions = '["mine"]'
    db_session.commit()
    response = setup["api"].post("/requirements/update-zentao-task-dates", json=_payload(setup))
    assert response.status_code == 403
    assert not setup["client"].reads and not setup["client"].writes


def test_assignment_tab_user_can_update_other_assignees_dates(db_session, setup):
    setup["actor"].role = UserRole.USER
    setup["actor"].tab_permissions = '["assign"]'
    db_session.commit()
    response = setup["api"].post("/requirements/update-zentao-task-dates", json=_payload(setup))
    assert response.status_code == 200 and response.json()["counts"]["updated"] == 2


def test_unbuilt_only_needs_no_zentao_client(monkeypatch, setup):
    monkeypatch.setattr(schedules, "get_system_zentao_client", lambda db: (_ for _ in ()).throw(AssertionError("must not connect")))
    data = setup["api"].post("/requirements/update-zentao-task-dates", json=_payload(setup, [setup["reqs"][1].id])).json()
    assert data["counts"]["skipped"] == 1 and not setup["client"].reads


def test_missing_system_client_preserves_partial_skip(monkeypatch, setup):
    monkeypatch.setattr(schedules, "get_system_zentao_client", lambda db: None)
    data = setup["api"].post("/requirements/update-zentao-task-dates", json=_payload(setup)).json()
    assert data["counts"]["failed"] == 2 and data["counts"]["skipped"] == 1
    assert not setup["client"].writes


def test_pure_local_assignment_retains_optional_dates(monkeypatch, setup):
    sent = []
    async def send(text): sent.append(text)
    monkeypatch.setattr(routes, "send_markdown", send)
    monkeypatch.setattr(routes.ZentaoTaskSyncService, "create_tasks_for_assignment", lambda *a, **kw: (_ for _ in ()).throw(AssertionError("no remote action")))
    req = setup["reqs"][0]
    response = setup["api"].post("/requirements/assign-and-publish", json={"major_version_id": setup["major"].id,
        "assignments": [{"requirement_id": req.id, "owner_id": setup["owner"].id}], "create_zentao_tasks": False})
    assert response.status_code == 200 and response.json()["zentao"] is None
    assert len(sent) == 1 and not setup["client"].reads
