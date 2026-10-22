from datetime import timedelta

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from app.api.deps import get_current_user, get_db
from app.api.routes import requirements
from app.core.exceptions import AppError
from app.models import (
    BugSourceType, BugTracking, Requirement, RequirementRetestRecord,
    TestCase as CaseModel, User, UserRole, Version, VersionType,
)
from app.models.enums import TaskBoardStatus
from app.models.final_test_record import FinalTestRecord
from app.models.software import SoftwareProduct
from app.models.task_board import TaskBoardTask
from app.models.zentao_task_mirror import ZentaoTaskMirror
from app.models.zentao_testcase_mirror import ZentaoTestCaseMirror
from app.services.assignment_progress_service import AssignmentProgressService
from app.services.retest_service import RetestService
from app.utils.time_utils import local_now


@pytest.fixture
def progress_env(db_session):
    db = db_session
    now = local_now()
    owner = User(username="progress_owner", password_hash="x", role=UserRole.ADMIN, zentao_account="alice")
    reviewer = User(username="progress_reviewer", password_hash="x", role=UserRole.USER, zentao_account="bob")
    software = SoftwareProduct(name="progress-a")
    other_software = SoftwareProduct(name="progress-b")
    db.add_all([owner, reviewer, software, other_software])
    db.flush()
    major = Version(version_no="V-progress", version_type=VersionType.MAJOR, software_id=software.id, zentao_execution_id=100)
    next_major = Version(version_no="V-next", version_type=VersionType.MAJOR, software_id=software.id, zentao_execution_id=200)
    foreign_major = Version(version_no="V-foreign", version_type=VersionType.MAJOR, software_id=other_software.id, zentao_execution_id=300)
    db.add_all([major, next_major, foreign_major])
    db.flush()
    req = Requirement(zentao_req_id="r#900", zentao_story_id=900, title="进度需求", owner_id=owner.id,
                      major_version_id=major.id, case_completed=True, test_completed=True,
                      test_completed_at=now - timedelta(days=2), zentao_task_id=501, zentao_parent_task_id=500)
    unassigned = Requirement(zentao_req_id="r#901", zentao_story_id=901, title="未分配需求", major_version_id=major.id)
    foreign = Requirement(zentao_req_id="r#902", title="其他软件", major_version_id=foreign_major.id)
    db.add_all([req, unassigned, foreign])
    db.flush()
    case = CaseModel(requirement_id=req.id, zentao_case_id="u#101", zentao_case_numeric_id=101)
    mirror = ZentaoTestCaseMirror(zentao_case_id="u#101", zentao_case_numeric_id=101, zentao_story_id=900)
    db.add_all([case, mirror])
    db.flush()
    bug_args = dict(major_version_id=major.id, created_by_id=owner.id, source_type=BugSourceType.MANUAL)

    def bug(number, **kwargs):
        row = BugTracking(**{**bug_args, "bug_id": f"b#{number}", "zentao_bug_id": str(number), **kwargs})
        db.add(row)
        return row

    linked = bug(1001, requirement_id=req.id, source_type=BugSourceType.CASE, source_ref=str(case.id),
                 zentao_story_id=900, zentao_linked_case_id="101", zentao_live_status="resolved", closed=False)
    cross = bug(1002, major_version_id=next_major.id, zentao_story_id=900,
                zentao_opened_at=now - timedelta(days=1), zentao_opened_by_account="bob")
    self_report = bug(1003, zentao_story_id=900, zentao_opened_at=now - timedelta(days=1), zentao_opened_by_account="alice")
    activation = bug(1004, requirement_id=unassigned.id, zentao_story_id=901,
                     retest_activated=True, retest_activated_req_id=req.id,
                     retest_activated_by_id=reviewer.id, retest_activated_at=now)
    dismissed = bug(1005, zentao_story_id=900, zentao_opened_at=now - timedelta(days=1),
                    zentao_opened_by_account="bob", retest_dismissed=True)
    historical = bug(1006, requirement_id=req.id, source_type=BugSourceType.RETEST)
    closed = bug(1007, requirement_id=req.id, closed=True, zentao_live_status="closed")
    bug(1008, zentao_story_id=900, zentao_deleted=True)
    bug(1009, major_version_id=foreign_major.id, zentao_story_id=900)
    db.add(RequirementRetestRecord(requirement_id=req.id, user_id=reviewer.id, passed=True))
    for task_id, story, execution, status, parent in [
        (500, 900, 100, "doing", True), (501, None, 100, "done", False),
        (502, 900, 100, "doing", False), (503, 900, 200, "wait", False), (504, 900, 100, "cancel", False),
    ]:
        db.add(ZentaoTaskMirror(task_id=task_id, execution_id=execution, name=f"task-{task_id}", story=story,
                               status=status, is_parent=parent, parent=500 if task_id == 501 else 0,
                               deadline=now.date() - timedelta(days=1), assigned_to_realname="当前指派人",
                               finished_by_realname="真实完成者" if task_id == 501 else None))
    locals_ = []
    for title, status, software_id, archived in [
        ("local-blocked", TaskBoardStatus.BLOCKED, software.id, False),
        ("local-done", TaskBoardStatus.DONE, software.id, False),
        ("wrong-software", TaskBoardStatus.TODO, other_software.id, False),
        ("archived", TaskBoardStatus.DONE, software.id, True),
    ]:
        task = TaskBoardTask(title=title, status=status, board_date=now.date(), due_at=now - timedelta(hours=1),
                             target_type="requirement", target_id=req.id, software_id=software_id,
                             major_version_id=major.id, archived=archived, assignee_id=reviewer.id)
        db.add(task)
        locals_.append(task)
    db.commit()
    return dict(db=db, req=req, unassigned=unassigned, foreign=foreign, owner=owner, reviewer=reviewer,
                software=software, other_software=other_software, major=major,
                bugs=[linked, cross, self_report, activation, dismissed, historical, closed], locals=locals_)


def test_associations_are_deduplicated_scoped_and_read_only(progress_env):
    e = progress_env
    service = AssignmentProgressService(e["db"])
    data = service.detail(e["req"].id, e["major"].id, e["software"].id)
    bugs = data["bugs"]["items"]
    assert {b["bug_id"] for b in bugs} == {f"b#{n}" for n in range(1001, 1008)}
    assert len(bugs) == 7
    linked = next(b for b in bugs if b["bug_id"] == "b#1001")
    assert "用例关联" in linked["association_sources"]
    assert linked["zentao_live_status"] == "resolved" and linked["closed"] is False
    cross = next(b for b in bugs if b["bug_id"] == "b#1002")
    assert cross["major_version_no"] == "V-next"
    assert bugs[-1]["closed"] is True
    assert data["requirement"]["bug_count"] == 7
    assert data["requirement"]["open_bug_count"] == 6
    # Merely inspecting this report must not rewrite lifecycle/aggregate state.
    assert e["req"].retest_completed is False
    assert not e["db"].dirty


def test_retest_matches_existing_conclusion_and_preserves_history(progress_env):
    e = progress_env
    data = AssignmentProgressService(e["db"]).detail(e["req"].id)
    rows = {b["bug_id"]: b for b in data["retest_bugs"]["items"]}
    assert set(rows) == {"b#1002", "b#1004", "b#1005", "b#1006"}
    assert rows["b#1002"]["retest_problem_kind"] == "collected"
    assert rows["b#1004"]["retest_problem_kind"] == "activated"
    assert rows["b#1005"]["retest_dismissed"] and not rows["b#1005"]["retest_problem"]
    assert not rows["b#1006"].get("retest_problem", False)
    assert data["requirement"]["retest_active_problem_count"] == 2
    assert data["requirement"]["retest_conclusion"] == RetestService(e["db"]).compute_conclusion(e["req"]) == "failed"
    assert data["retest_records"][0]["passed"] is True
    other = AssignmentProgressService(e["db"]).detail(e["unassigned"].id)
    assert other["requirement"]["retest_active_problem_count"] == 0, "activation belongs only to its recorded requirement"


def test_tasks_include_own_child_but_exclude_parents_other_execution_and_archives(progress_env):
    e = progress_env
    data = AssignmentProgressService(e["db"]).detail(e["req"].id)
    tasks = data["tasks"]["items"]
    assert {t["id"] for t in tasks if t["source"] == "zentao"} == {501, 502, 504}
    assert {t["name"] for t in tasks if t["source"] == "local"} == {"local-blocked", "local-done"}
    child = next(t for t in tasks if t["source"] == "zentao" and t["id"] == 501)
    assert child["is_test_task"] and child["parent_id"] == 500
    assert child["finished_by_name"] == "真实完成者"
    assert data["requirement"]["task_count"] == 5
    assert data["requirement"]["task_done_count"] == 2
    assert data["requirement"]["task_overdue_count"] == 2


def test_known_task_without_mirror_is_visible_as_pending_sync(progress_env):
    e = progress_env
    e["req"].zentao_task_id = 999
    e["req"].zentao_task_status_cache = "pause"
    e["db"].commit()
    tasks = AssignmentProgressService(e["db"]).detail(e["req"].id)["tasks"]["items"]
    task = next(t for t in tasks if t["id"] == 999 and t["source"] == "zentao")
    assert task["status"] == "pause" and task["synced_at"] is None


@pytest.mark.parametrize("completed", [False, True])
def test_findings_without_test_completion_timestamp_are_not_retest_problems(progress_env, completed):
    e = progress_env
    req = e["unassigned"]
    req.test_completed = completed
    e["db"].add(BugTracking(major_version_id=e["major"].id, zentao_story_id=req.zentao_story_id,
                            bug_id="b#1010", zentao_opened_at=local_now(), zentao_opened_by_account="bob"))
    e["db"].commit()
    data = AssignmentProgressService(e["db"]).detail(req.id)
    assert data["requirement"]["bug_count"] == 2
    assert data["requirement"]["retest_conclusion"] == "pending"
    assert data["requirement"]["retest_active_problem_count"] == 0
    assert data["retest_bugs"]["total"] == 0


def test_summary_includes_unassigned_and_compatible_major_summaries(progress_env):
    e = progress_env
    service = AssignmentProgressService(e["db"])
    data = service.summary(e["major"].id, e["software"].id)
    assert data["summary"]["requirements"] == 2
    assert data["summary"]["unassigned_total"] == 1
    assert data["summary"]["retest_failed_total"] == 1
    assert data["summary"]["owners"] == 1
    assert data["owners"][-1]["owner_id"] == -1
    assert data["owners"][0]["major_summaries"][0]["total_requirements"] == 1
    item = data["owners"][0]["requirements"][0]
    assert item["bug_count"] == 7 and "bugs" not in item and "tasks" not in item
    assert service.summary(e["major"].id, e["other_software"].id)["summary"]["requirements"] == 0
    with pytest.raises(HTTPException) as exc:
        service.detail(e["req"].id, software_id=e["other_software"].id)
    assert exc.value.status_code == 404


def test_pagination_is_independent_and_clamps_after_deletions(progress_env):
    e = progress_env
    data = AssignmentProgressService(e["db"]).detail(e["req"].id, bug_page=2, task_page=99, page_size=2)
    assert data["bugs"]["page"] == 2 and len(data["bugs"]["items"]) == 2
    assert data["retest_bugs"]["page"] == 1
    assert data["tasks"]["page"] == 3 and len(data["tasks"]["items"]) == 1


def test_final_test_counts_each_person_independently_without_associations(progress_env, monkeypatch):
    e = progress_env
    db = e["db"]
    e["major"].final_test_enabled = True
    newcomer = User(username="not_started", password_hash="x")
    db.add(newcomer)
    db.add_all([
        FinalTestRecord(requirement_id=e["req"].id, user_id=e["owner"].id, case_completed=True),
        FinalTestRecord(requirement_id=e["req"].id, user_id=e["reviewer"].id, test_completed=True),
        # A record outside the selected major must not count.
        FinalTestRecord(requirement_id=e["foreign"].id, user_id=e["owner"].id,
                        case_completed=True, test_completed=True),
    ])
    db.commit()

    def unexpected_collect(*args):
        pytest.fail("Final test must not collect bugs, retest conclusions or tasks")

    monkeypatch.setattr(AssignmentProgressService, "_collect", unexpected_collect)
    data = AssignmentProgressService(db).summary(e["major"].id, e["software"].id)
    assert data["mode"] == "final_test"
    assert data["summary"] == {
        "owners": 3, "requirements": 2, "person_requirements": 6,
        "case_done": 1, "case_pending": 5, "test_done": 1, "test_pending": 5,
    }
    people = {o["owner_id"]: o for o in data["owners"]}
    assert set(people) == {e["owner"].id, e["reviewer"].id, newcomer.id}, "sync bot is excluded"
    for person in people.values():
        assert {r["id"] for r in person["requirements"]} == {e["req"].id, e["unassigned"].id}
        assert all(r["owner_id"] == person["owner_id"] for r in person["requirements"])
        assert all("bug_count" not in r and "retest_conclusion" not in r and "task_count" not in r
                   for r in person["requirements"])
    owner_state = people[e["owner"].id]["requirements"][0]
    reviewer_state = people[e["reviewer"].id]["requirements"][0]
    assert owner_state["case_completed"] and not owner_state["test_completed"]
    assert not reviewer_state["case_completed"] and reviewer_state["test_completed"]
    assert people[newcomer.id]["major_summaries"][0]["case_pending"] == 2
    assert not db.dirty


def test_final_test_switching_preserves_original_and_personal_states(progress_env):
    e = progress_env
    db = e["db"]
    record = FinalTestRecord(requirement_id=e["req"].id, user_id=e["owner"].id,
                             case_completed=True, test_completed=True)
    db.add(record)
    e["major"].final_test_enabled = True
    db.commit()
    service = AssignmentProgressService(db)
    assert service.summary(e["major"].id)["summary"]["test_done"] == 1
    record.test_completed = False
    db.commit()
    assert service.summary(e["major"].id)["summary"]["test_done"] == 0
    assert e["req"].test_completed is True
    # A stale major selection cannot reveal another software's personal records.
    scoped = service.summary(e["major"].id, e["other_software"].id)
    assert scoped["mode"] == "assignment" and scoped["owners"] == []
    e["major"].final_test_enabled = False
    db.commit()
    normal = service.summary(e["major"].id)
    assert normal["mode"] == "assignment" and normal["summary"]["test_done"] == 1
    e["major"].final_test_enabled = True
    db.commit()
    assert service.summary(e["major"].id)["summary"]["test_done"] == 0


def test_final_test_before_any_checkmarks_and_without_requirements(progress_env):
    e = progress_env
    e["major"].final_test_enabled = True
    empty_major = Version(version_no="empty-final", version_type=VersionType.MAJOR,
                          software_id=e["software"].id, final_test_enabled=True)
    e["db"].add(empty_major)
    e["db"].commit()
    service = AssignmentProgressService(e["db"])
    fresh = service.summary(e["major"].id)
    assert fresh["summary"]["owners"] == 2
    assert fresh["summary"]["case_pending"] == fresh["summary"]["test_pending"] == 4
    empty = service.summary(empty_major.id)
    assert empty["mode"] == "final_test"
    assert empty["summary"]["person_requirements"] == empty["summary"]["case_pending"] == 0


def test_final_test_retains_recorded_participants_and_excludes_inactive_nonparticipants(progress_env):
    e = progress_env
    e["major"].final_test_enabled = True
    e["reviewer"].is_team_member = False
    e["db"].add(User(username="inactive", password_hash="x", is_active=False))
    e["db"].add(FinalTestRecord(requirement_id=e["req"].id, user_id=e["reviewer"].id,
                               case_completed=True))
    e["db"].commit()
    data = AssignmentProgressService(e["db"]).summary(e["major"].id)
    assert {o["owner_id"] for o in data["owners"]} == {e["owner"].id, e["reviewer"].id}
    assert data["summary"]["case_done"] == 1


def test_dismissed_problems_do_not_override_a_pass_record(progress_env):
    e = progress_env
    for bug in e["bugs"]:
        bug.retest_dismissed = True
    e["db"].commit()
    data = AssignmentProgressService(e["db"]).detail(e["req"].id)
    assert data["requirement"]["retest_conclusion"] == "passed"
    assert data["requirement"]["retest_active_problem_count"] == 0
    assert data["retest_bugs"]["total"] == 4


def test_refresh_failure_retains_cached_data_and_reports_warnings(progress_env, monkeypatch):
    from app.services.workbench_refresh_service import WorkbenchRefreshService
    from app.services.zentao_task_mirror_service import ZentaoTaskMirrorService
    e = progress_env
    monkeypatch.setattr(WorkbenchRefreshService, "preflight_refresh", lambda *a, **k: {"bugs": {"ok": False, "error": "offline"}, "testcases": {"ok": True}})
    monkeypatch.setattr(ZentaoTaskMirrorService, "sync_one_major", lambda *a, **k: {"ok": False, "error": "task offline"})
    data = AssignmentProgressService(e["db"]).refresh(e["req"].id, e["owner"], e["major"].id, e["software"].id)
    assert data["bugs"]["total"] == 7 and data["tasks"]["total"] == 5
    assert len(data["warnings"]) == 2
    assert "offline" in data["warnings"][0]


def test_successful_refresh_returns_updated_status_and_counts(progress_env, monkeypatch):
    from app.services.workbench_refresh_service import WorkbenchRefreshService
    from app.services.zentao_task_mirror_service import ZentaoTaskMirrorService
    e = progress_env

    def refresh_bugs(*_args, **kwargs):
        assert kwargs["testcases_recent"] is True
        e["bugs"][0].closed = True
        e["db"].commit()
        return {"bugs": {"ok": True}, "testcases": {"ok": True}}

    def refresh_tasks(*_args, **_kwargs):
        e["db"].query(ZentaoTaskMirror).filter_by(task_id=502).update({"status": "done"})
        e["db"].commit()
        return {"ok": True}

    monkeypatch.setattr(WorkbenchRefreshService, "preflight_refresh", refresh_bugs)
    monkeypatch.setattr(ZentaoTaskMirrorService, "sync_one_major", refresh_tasks)
    data = AssignmentProgressService(e["db"]).refresh(e["req"].id, e["owner"])
    assert data["warnings"] == []
    assert data["requirement"]["open_bug_count"] == 5
    assert data["requirement"]["task_done_count"] == 3


def test_empty_detail_has_explicit_empty_pages(progress_env):
    e = progress_env
    data = AssignmentProgressService(e["db"]).detail(e["foreign"].id)
    for key in ("bugs", "retest_bugs", "tasks"):
        assert data[key] == {"items": [], "total": 0, "page": 1, "page_size": 20}
    assert data["retest_records"] == []


def test_api_permissions_scope_and_page_validation(progress_env, monkeypatch):
    e = progress_env
    app = FastAPI()
    app.include_router(requirements.router)
    app.dependency_overrides[get_db] = lambda: e["db"]
    app.dependency_overrides[get_current_user] = lambda: e["reviewer"]

    @app.exception_handler(AppError)
    async def app_error(_request, exc):
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.message})

    client = TestClient(app)
    url = f'/requirements/admin/progress/{e["req"].id}'
    assert client.get('/requirements/admin/progress').status_code == 403
    assert client.get(url).status_code == 403
    assert client.post(url + '/refresh').status_code == 403
    e["reviewer"].tab_permissions = '["assign"]'
    e["db"].commit()
    assert client.get(url).status_code == 200, "explicit assignment access works for non-admins"
    e["major"].final_test_enabled = True
    e["db"].commit()
    final = client.get(f'/requirements/admin/progress?major_version_id={e["major"].id}&software_id={e["software"].id}')
    assert final.status_code == 200 and final.json()["mode"] == "final_test"
    assert final.json()["summary"]["test_done"] == 0, "original owner completion must not leak into final test"
    app.dependency_overrides[get_current_user] = lambda: e["owner"]
    assert client.get(url + '?page_size=0').status_code == 422
    assert client.get(url + '?bug_page=0').status_code == 422
    assert client.get(url + '?page_size=101').status_code == 422
    assert client.get(url + f'?software_id={e["other_software"].id}').status_code == 404
    assert client.get(url + '?page_size=2').json()["bugs"]["total"] == 7
    monkeypatch.setattr(AssignmentProgressService, "refresh", lambda *a, **k: {"warnings": []})
    assert client.post(url + '/refresh').status_code == 200
