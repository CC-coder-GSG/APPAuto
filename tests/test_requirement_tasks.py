from copy import deepcopy
from datetime import date

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.api.deps import get_current_user, get_db
from app.api.routes import requirements
from app.models import FinalTestRecord, Requirement, TestExecution as Execution, User, UserRole, Version, VersionType
from app.models.software import SoftwareProduct
from app.models.zentao_task_mirror import ZentaoTaskMirror
from app.services.requirement_task_service import RequirementTaskService
from app.services.workbench_service import WorkbenchService
from app.services.zentao_task_mirror_service import ZentaoTaskMirrorService


class TaskClient:
    def __init__(self, tasks):
        self.tasks = tasks
        self.calls = []

    def get_task(self, task_id):
        return deepcopy(self.tasks[task_id])

    def list_execution_tasks(self, execution_id):
        return [deepcopy(t) for t in self.tasks.values() if t['execution'] == execution_id]

    def update_task(self, task_id, fields):
        self.calls.append(('update', task_id, fields))
        self.tasks[task_id].update(fields)
        return self.get_task(task_id)

    def start_task(self, task_id, **kwargs):
        return self.update_task(task_id, {'status': 'doing'})

    def pause_task(self, task_id, **kwargs):
        return self.update_task(task_id, {'status': 'pause'})

    def restart_task(self, task_id, **kwargs):
        return self.update_task(task_id, {'status': 'doing'})

    def finish_task(self, task_id, **kwargs):
        return self.update_task(task_id, {'status': 'done', 'finishedBy': 'alice'})

    def reassign_task(self, task_id, account):
        return self.update_task(task_id, {'assignedTo': account})

    def update_task_dates(self, task_id, *, est_started, deadline):
        return self.update_task(task_id, {'estStarted': est_started, 'deadline': deadline})


@pytest.fixture
def env(db_session, monkeypatch):
    db = db_session
    alice = User(username='multi_alice', password_hash='x', zentao_account='alice')
    bob = User(username='multi_bob', password_hash='x', zentao_account='bob')
    admin = User(username='multi_admin', password_hash='x', role=UserRole.ADMIN, zentao_account='admin')
    software = SoftwareProduct(name='multi-task-product')
    db.add_all([alice, bob, admin, software])
    db.flush()
    major = Version(version_no='multi-major', version_type=VersionType.MAJOR,
                    software_id=software.id, zentao_execution_id=123)
    db.add(major)
    db.flush()
    minor = Version(version_no='multi-minor', version_type=VersionType.MINOR,
                    software_id=software.id, parent_id=major.id)
    req = Requirement(zentao_req_id='r#333', title='multiple tasks', major_version_id=major.id,
                      owner_id=alice.id, zentao_story_id=333, zentao_task_id=101,
                      zentao_task_assigned_to='alice', zentao_task_status_cache='wait', case_completed=True)
    db.add_all([minor, req])
    tasks = {}
    for tid in [101, 102]:
        tasks[tid] = dict(id=tid, execution=123, story=333, type='test', isParent=0, parent=0,
                          name=f'Task {tid}', status='wait', assignedTo='alice', estimate=4, left=4,
                          estStarted='2026-10-22', deadline='2026-10-23')
        db.add(ZentaoTaskMirror(task_id=tid, execution_id=123, story=333, type='test',
                                name=f'Task {tid}', status='wait', assigned_to='alice',
                                assignee_user_id=alice.id, estimate=4, left=4))
    db.commit()
    client = TaskClient(tasks)
    monkeypatch.setattr('app.services.zentao_system_client.get_system_zentao_client', lambda *_: client)
    monkeypatch.setattr('app.services.zentao_task_mirror_service.get_system_zentao_client', lambda *_: client)
    monkeypatch.setattr('app.services.zentao_task_mirror_service.get_user_zentao_client', lambda *_: client)
    monkeypatch.setattr(WorkbenchService, '_maybe_sync_task_status', lambda *_: None)
    monkeypatch.setattr(ZentaoTaskMirrorService, '_auto_consumed_hours', lambda *_: 0.5)
    monkeypatch.setattr(ZentaoTaskMirrorService, '_split_segment_by_day', lambda *_: [])
    return dict(db=db, alice=alice, bob=bob, admin=admin, req=req, major=major, minor=minor,
                client=client, service=RequirementTaskService(db), software=software)


def test_completion_waits_for_both_tasks_and_reactivation_reopens_requirement(env):
    e = env
    svc = e['service']
    svc.operate(e['req'].id, 102, e['alice'], action='finish', consumed=1,
                minor_version_id=e['minor'].id, notes='second task')
    assert e['req'].test_completed is False
    assert e['req'].zentao_task_id == 101
    assert e['client'].tasks[101]['status'] == 'wait'
    assert e['db'].query(Execution).one().notes == '任务 #102：second task'
    svc.operate(e['req'].id, 101, e['alice'], action='finish', consumed=1)
    assert e['req'].test_completed is True
    svc.operate(e['req'].id, 102, e['alice'], action='reactivate')
    assert e['req'].test_completed is False
    assert e['client'].tasks[101]['status'] == 'done'


def test_selected_task_owns_its_status_estimate_and_tracking(env):
    e = env
    svc = e['service']
    svc.operate(e['req'].id, 102, e['alice'], action='set_time', hours=7)
    svc.operate(e['req'].id, 102, e['alice'], action='start')
    selected = e['db'].get(ZentaoTaskMirror, 102)
    assert selected.status == 'doing' and selected.estimate == 7
    assert selected.local_started_at is not None
    assert e['req'].task_started_at is None
    assert e['db'].get(ZentaoTaskMirror, 101).estimate == 4
    svc.operate(e['req'].id, 102, e['alice'], action='pause')
    assert selected.status == 'pause' and selected.local_started_at is None
    assert e['client'].tasks[101]['status'] == 'wait'


def test_retrying_completion_does_not_duplicate_effort_or_execution_records(env):
    e = env
    for _ in range(2):
        e['service'].operate(e['req'].id, 102, e['alice'], action='finish', consumed=1,
                            minor_version_id=e['minor'].id, notes='retry')
    assert e['db'].query(Execution).count() == 1
    assert sum(fields.get('status') == 'done' for _, _, fields in e['client'].calls) == 1


def test_estimate_update_that_does_not_take_effect_is_not_reported_as_success(env, monkeypatch):
    e = env
    monkeypatch.setattr(e['client'], 'update_task', lambda tid, fields: e['client'].get_task(tid))
    with pytest.raises(HTTPException) as exc:
        e['service'].operate(e['req'].id, 102, e['alice'], action='set_time', hours=9)
    assert exc.value.status_code == 502
    assert e['db'].get(ZentaoTaskMirror, 102).estimate == 4


def test_unknown_status_blocks_completion_and_cancelled_primary_does_not(env):
    e = env
    primary = e['db'].get(ZentaoTaskMirror, 101)
    secondary = e['db'].get(ZentaoTaskMirror, 102)
    primary.status = 'done'
    secondary.status = None
    e['db'].commit()
    e['service'].reconcile(e['req'])
    assert not e['req'].test_completed
    primary.status = 'cancel'
    e['req'].zentao_task_status_cache = 'cancel'
    secondary.status = 'done'
    e['db'].commit()
    e['service'].reconcile(e['req'])
    assert e['req'].test_completed


def test_completed_task_stays_in_selection_after_assignment_flow(env):
    e = env
    row = e['db'].get(ZentaoTaskMirror, 102)
    row.status = 'done'
    row.assigned_to = 'bob'
    row.assignee_user_id = e['bob'].id
    row.finished_by = 'alice'
    e['db'].commit()
    task = next(t for t in e['service'].describe([e['req']], e['alice'])[e['req'].id] if t['task_id'] == 102)
    assert task['assigned_to_me'] is True and task['can_operate'] is False


def test_scope_rejects_other_users_parents_development_and_other_executions(env):
    e = env
    for tid, fields in [(103, dict(execution_id=999)), (104, dict(story=999)),
                        (105, dict(is_parent=True)), (106, dict(type='devel')),
                        (107, dict(status='cancel'))]:
        e['db'].add(ZentaoTaskMirror(**dict(task_id=tid, execution_id=123, story=333, type='test',
                                          name='excluded', status='wait', **{}) | fields))
    e['db'].commit()
    for tid in [103, 104, 105, 106, 107]:
        with pytest.raises(HTTPException) as exc:
            e['service'].operate(e['req'].id, tid, e['alice'], action='start')
        assert exc.value.status_code == 404
    with pytest.raises(HTTPException) as exc:
        e['service'].operate(e['req'].id, 102, e['bob'], action='start')
    assert exc.value.status_code == 403
    assert not e['client'].calls


def test_remote_reassociation_or_reassignment_is_rejected_before_mutation(env):
    e = env
    e['client'].tasks[102]['story'] = 999
    with pytest.raises(HTTPException) as exc:
        e['service'].operate(e['req'].id, 102, e['alice'], action='start')
    assert exc.value.status_code == 409
    e['client'].tasks[102]['story'] = 333
    e['client'].tasks[102]['assignedTo'] = 'bob'
    with pytest.raises(HTTPException) as exc:
        e['service'].operate(e['req'].id, 102, e['alice'], action='start')
    assert exc.value.status_code == 403
    assert not e['client'].calls


def test_remote_unassignment_is_rejected_before_mutation(env):
    e = env
    e['client'].tasks[102]['assignedTo'] = ''
    with pytest.raises(HTTPException) as exc:
        e['service'].operate(e['req'].id, 102, e['alice'], action='start')
    assert exc.value.status_code == 403 and not e['client'].calls


def test_assign_edit_changes_selected_manual_task_only(env):
    e = env
    result = e['service'].edit(e['req'].id, 102, e['admin'], owner_id=e['bob'].id,
                               start='2026-11-01', deadline='2026-11-02')
    assert result['ok'] is True
    assert e['client'].tasks[102]['assignedTo'] == 'bob'
    assert e['client'].tasks[102]['estStarted'] == '2026-11-01'
    assert e['client'].tasks[101]['assignedTo'] == 'alice'
    assert e['client'].tasks[101]['estStarted'] == '2026-10-22'
    assert e['req'].owner_id == e['alice'].id and e['req'].zentao_task_id == 101
    assert e['db'].get(ZentaoTaskMirror, 102).deadline == date(2026, 11, 2)


def test_assign_edit_reports_partial_result_when_dates_cannot_be_read_after_reassignment(env, monkeypatch):
    e = env
    original = e['client'].get_task
    reads_after_assign = 0

    def get_task(tid):
        nonlocal reads_after_assign
        if e['client'].tasks[tid]['assignedTo'] == 'bob':
            reads_after_assign += 1
            if reads_after_assign > 2:
                raise RuntimeError('offline after assignment confirmation')
        return original(tid)

    monkeypatch.setattr(e['client'], 'get_task', get_task)
    result = e['service'].edit(e['req'].id, 102, e['admin'], owner_id=e['bob'].id,
                               start='2026-11-01', deadline='2026-11-02')
    assert result['ok'] is False
    assert result['results']['assignment']['ok'] is True
    assert result['results']['dates']['status'] == 'failed'
    assert e['client'].tasks[102]['assignedTo'] == 'bob'
    assert e['client'].tasks[102]['estStarted'] == '2026-10-22'


def test_final_test_personal_completion_waits_for_all_own_tasks(env):
    e = env
    e['major'].final_test_enabled = True
    e['db'].commit()
    e['service'].operate(e['req'].id, 102, e['alice'], action='finish', consumed=1)
    record = e['db'].query(FinalTestRecord).one()
    assert record.test_completed is False
    e['service'].operate(e['req'].id, 101, e['alice'], action='finish', consumed=1)
    assert record.test_completed is True
    e['service'].operate(e['req'].id, 102, e['alice'], action='reactivate')
    assert record.test_completed is False


def test_workbench_exposes_own_manual_tasks_on_another_owners_requirement(env):
    e = env
    e['req'].owner_id = e['bob'].id
    e['db'].commit()
    rows = WorkbenchService(e['db']).get_my_workbench(current_user=e['alice'], major_version_id=e['major'].id)
    assert len(rows) == 1 and rows[0]['id'] == e['req'].id
    assert rows[0]['has_multiple_test_tasks'] is True
    assert [t['task_id'] for t in rows[0]['test_tasks'] if t['can_operate']] == [101, 102]
    assert not rows[0]['all_linked_test_tasks_completed']


def test_all_pending_includes_new_tasks_even_if_original_requirement_was_completed(env):
    e = env
    e['req'].test_completed = True
    e['db'].get(ZentaoTaskMirror, 101).status = 'done'
    e['db'].commit()
    rows = WorkbenchService(e['db']).get_my_workbench(current_user=e['alice'], mode='all_pending', software_id=e['software'].id)
    assert len(rows) == 1 and not rows[0]['test_completed']


def test_snapshot_sync_reopens_previously_completed_requirement_with_new_task(env):
    e = env
    e['req'].test_completed = True
    e['client'].tasks[101]['status'] = 'done'
    e['db'].commit()
    mirror = ZentaoTaskMirrorService(e['db'])
    by_account, by_name = mirror._build_user_maps()
    mirror._sync_execution(e['client'], e['major'], by_account, by_name)
    e['db'].commit()
    assert e['req'].test_completed is False
    e['client'].tasks[102]['status'] = 'closed'
    mirror._sync_execution(e['client'], e['major'], by_account, by_name)
    e['db'].commit()
    assert e['req'].test_completed is True


def test_api_edit_requires_assignment_access_and_operation_requires_own_task(env):
    e = env
    app = FastAPI()
    app.include_router(requirements.router)
    app.dependency_overrides[get_db] = lambda: e['db']
    app.dependency_overrides[get_current_user] = lambda: e['alice']
    from app.core.exceptions import AppError
    from fastapi.responses import JSONResponse

    @app.exception_handler(AppError)
    async def error(_request, exc):
        return JSONResponse(status_code=exc.status_code, content={'detail': exc.message})

    client = TestClient(app)
    url = f'/requirements/{e["req"].id}/linked-tasks/102'
    assert client.get(f'/requirements/{e["req"].id}/linked-tasks').status_code == 403
    assert client.put(url, json={}).status_code == 403
    assert client.post(url + '/operate', json={'action': 'set_time', 'hours': 0}).status_code == 422
    app.dependency_overrides[get_current_user] = lambda: e['bob']
    assert client.post(url + '/operate', json={'action': 'start'}).status_code == 403
    app.dependency_overrides[get_current_user] = lambda: e['admin']
    assert len(client.get(f'/requirements/{e["req"].id}/linked-tasks').json()['tasks']) == 2
    response = client.put(url, json={'task_start_date': '2026-12-01', 'task_deadline': '2026-12-02'})
    assert response.status_code == 200 and response.json()['ok'] is True
