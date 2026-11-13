import json
from copy import deepcopy

import pytest
from fastapi import HTTPException

from test_requirement_tasks import env  # shared scoped-task/remote fixture
from app.models import FinalTestRecord, RequirementChangeRetest, ZentaoTaskMirror
from app.services.final_test_service import FinalTestService
from app.services.requirement_change_service import RequirementChangeService
from app.services.workbench_service import WorkbenchService


@pytest.fixture
def changed(env, monkeypatch):
    e = env
    e['major'].final_test_enabled = True
    e['req'].test_completed = True
    for tid, task in e['client'].tasks.items():
        task.update(status='closed', assignedTo='closed', storyVersion=2, latestStoryVersion=6,
                    finishedBy='alice', consumed=3)
        mirror = e['db'].get(ZentaoTaskMirror, tid)
        mirror.status = 'closed'
        mirror.assigned_to = 'closed'
        mirror.task_story_change_pending = True
        mirror.task_story_version = 2
        mirror.task_latest_story_version = 6
        mirror.finished_by = 'alice'
    e['db'].add_all([FinalTestRecord(requirement_id=e['req'].id, user_id=u.id,
                                    case_completed=True, test_completed=True) for u in [e['alice'], e['bob']]])
    e['db'].commit()
    e['client'].list_assignable_users = lambda _: {'alice': 'Alice', 'bob': 'Bob'}
    def create(execution, **kw):
        assert kw['est_started'] and kw['deadline'], 'Retest tasks must carry planned dates'
        tid = max(e['client'].tasks) + 1
        task = dict(id=tid, execution=execution, story=kw['story'], type='test', isParent=0,
                    status='wait', assignedTo=kw['assigned_to'], name=kw['name'], desc=kw['desc'],
                    storyVersion=6, latestStoryVersion=6, estimate=kw['estimate'],
                    estStarted=kw['est_started'], deadline=kw['deadline'])
        e['client'].tasks[tid] = task
        return deepcopy(task)
    e['client'].create_execution_task = create
    e['change'] = RequirementChangeService(e['db'])
    return e


def dispatch(e, source=101, owner=None):
    return e['change'].dispatch(e['req'].id, e['admin'], source_task_id=source,
                                owner_id=(owner or e['alice']).id, expected_story_version=6,
                                task_start_date='2026-11-13', task_deadline='2026-11-16', mode='create')


def test_reopens_closed_changed_task_without_acknowledging_and_archives_owner_only(changed):
    e = changed
    result = dispatch(e)
    task = e['client'].tasks[101]
    assert result['task_id'] == 101 and not result['created']
    assert task['status'] == 'doing' and task['assignedTo'] == 'alice'
    assert task['storyVersion'] == 2  # only the assignee may acknowledge v6 later
    assert task['consumed'] == 3
    assert e['req'].owner_id == e['alice'].id and not e['req'].test_completed
    alice = e['db'].query(FinalTestRecord).filter_by(user_id=e['alice'].id).one()
    bob = e['db'].query(FinalTestRecord).filter_by(user_id=e['bob'].id).one()
    assert not alice.test_completed and not alice.case_completed
    assert bob.test_completed and bob.case_completed
    receipt = e['db'].query(RequirementChangeRetest).one()
    assert json.loads(receipt.previous_state)['test_completed'] is True
    assert e['db'].get(ZentaoTaskMirror, 101).task_story_change_pending
    assert e['db'].get(ZentaoTaskMirror, 101).efforts_submitted == 0


def test_repeated_dispatch_never_restarts_or_clears_new_progress_again(changed):
    e = changed
    dispatch(e)
    record = e['db'].query(FinalTestRecord).filter_by(user_id=e['alice'].id).one()
    record.case_completed = True
    e['db'].commit()
    calls = len(e['client'].calls)
    assert dispatch(e)['already_dispatched']
    assert record.case_completed and len(e['client'].calls) == calls
    assert e['db'].query(RequirementChangeRetest).count() == 1


def test_old_execution_creates_current_task_and_preserves_old_task(changed):
    e = changed
    e['client'].tasks[101]['execution'] = 999
    result = dispatch(e)
    assert result['created'] and result['task_id'] != 101
    assert e['client'].tasks[101]['status'] == 'closed'
    target = e['client'].tasks[result['task_id']]
    assert target['execution'] == 123 and target['story'] == 333
    assert target['assignedTo'] == 'alice'
    assert target['estStarted'] == '2026-11-13' and target['deadline'] == '2026-11-16'
    rows = WorkbenchService(e['db']).get_my_workbench(current_user=e['alice'], major_version_id=e['major'].id)
    assert any(t['task_id'] == result['task_id'] for t in rows[0]['test_tasks'])
    assert rows[0]['change_retests'][0]['previous_test_completed']


def test_create_timeout_recovers_receipt_without_duplicate_creation(changed):
    e = changed
    e['client'].tasks[101]['execution'] = 999
    create = e['client'].create_execution_task
    def timeout(*args, **kwargs):
        create(*args, **kwargs)
        raise TimeoutError()
    e['client'].create_execution_task = timeout
    with pytest.raises(HTTPException) as exc:
        dispatch(e)
    assert exc.value.status_code == 502
    assert e['db'].query(FinalTestRecord).filter_by(user_id=e['alice'].id).one().test_completed
    count = len(e['client'].tasks)
    result = dispatch(e)
    assert result['ok'] and len(e['client'].tasks) == count
    assert e['db'].query(RequirementChangeRetest).count() == 1


def test_unverified_restart_does_not_reset_personal_state(changed, monkeypatch):
    e = changed
    monkeypatch.setattr(e['client'], 'restart_task', lambda *a, **kw: None)
    with pytest.raises(HTTPException):
        dispatch(e)
    assert e['db'].query(FinalTestRecord).filter_by(user_id=e['alice'].id).one().test_completed
    assert e['db'].query(RequirementChangeRetest).one().status == 'unknown'
    assert e['change'].preview(e['req'].id, e['admin'])['tasks']


@pytest.mark.parametrize('bad', ['permission', 'story', 'version', 'parent', 'assignee'])
def test_preflight_rejects_before_any_remote_write(changed, bad):
    e = changed
    if bad == 'story': e['client'].tasks[101]['story'] = 444
    if bad == 'version': e['client'].tasks[101]['latestStoryVersion'] = 7
    if bad == 'parent': e['client'].tasks[101]['isParent'] = 1
    if bad == 'assignee': e['client'].list_assignable_users = lambda _: {}
    with pytest.raises(HTTPException):
        e['change'].dispatch(e['req'].id, e['alice'] if bad == 'permission' else e['admin'],
                             source_task_id=101, owner_id=e['alice'].id, expected_story_version=6)
    assert e['client'].calls == []
    assert e['db'].query(RequirementChangeRetest).count() == 0


def test_personal_completion_does_not_bypass_change_acknowledgement(changed):
    e = changed
    dispatch(e)
    with pytest.raises(HTTPException) as exc:
        FinalTestService(e['db']).upsert_record(e['req'].id, e['alice'], test_completed=True)
    assert exc.value.status_code == 409


def test_round_needs_confirmed_task_and_current_personal_completion(changed):
    e = changed
    dispatch(e)
    row = e['db'].get(ZentaoTaskMirror, 101)
    row.status = 'done'
    row.task_story_version = 6
    row.task_story_change_pending = False
    record = e['db'].query(FinalTestRecord).filter_by(user_id=e['alice'].id).one()
    e['db'].commit()
    assert e['change'].summaries([e['req'].id])[e['req'].id][0]['status'] == 'active'
    record.case_completed = record.test_completed = True
    e['db'].commit()
    assert e['change'].summaries([e['req'].id])[e['req'].id][0]['status'] == 'completed'


def test_previous_assignee_uses_assignment_history_instead_of_closing_actor():
    page = {'actions': {'1': {'id': 1, 'actor': 'manager', 'action': 'closed',
        'history': [{'field': 'assignedTo', 'old': 'alice', 'new': 'closed'}]}}}
    assert RequirementChangeService.previous_assignee(page) == 'alice'
    assert RequirementChangeService.previous_assignee({'actions': [{'actor': 'manager'}]}) is None


def test_restart_timeout_is_recovered_without_restarting_twice(changed):
    e = changed
    restart = e['client'].restart_task
    def timeout(*args, **kwargs):
        restart(*args, **kwargs)
        raise TimeoutError()
    e['client'].restart_task = timeout
    with pytest.raises(HTTPException):
        dispatch(e)
    assert dispatch(e)['ok']
    assert sum(fields.get('status') == 'doing' for _, _, fields in e['client'].calls) == 1


def test_retry_after_another_requirement_change_keeps_same_receipt(changed):
    e = changed
    e['client'].tasks[101]['execution'] = 999
    create = e['client'].create_execution_task
    def timeout(*args, **kwargs):
        create(*args, **kwargs)
        raise TimeoutError()
    e['client'].create_execution_task = timeout
    with pytest.raises(HTTPException):
        dispatch(e)
    e['client'].tasks[101]['latestStoryVersion'] = 7
    result = e['change'].dispatch(e['req'].id, e['admin'], source_task_id=101,
                                 owner_id=e['alice'].id, expected_story_version=7, mode='create')
    assert result['ok'] and len(e['client'].tasks) == 3
    assert e['db'].query(RequirementChangeRetest).one().story_version == 7


def test_change_retest_api_permissions_and_request_validation(changed):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.api.routes.requirements import router
    from app.api.deps import get_current_user, get_db
    e = changed
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db] = lambda: e['db']
    app.dependency_overrides[get_current_user] = lambda: e['alice']
    with TestClient(app) as client:
        url = f"/requirements/{e['req'].id}/change-retest"
        assert client.get(url).json()['can_manage'] is False
        body = {'source_task_id': 101, 'owner_id': e['alice'].id, 'expected_story_version': 6}
        assert client.post(url, json=body).status_code == 403
        app.dependency_overrides[get_current_user] = lambda: e['admin']
        assert client.post(url, json={**body, 'source_task_id': -1}).status_code == 422
        assert client.post(url, json=body).json()['task_id'] == 101


@pytest.mark.parametrize('start,end', [(None, None), ('2026-11-13', None),
                                      ('2026-11-16', '2026-11-13'), ('bad', '2026-11-16')])
def test_new_task_dates_validated_before_reserving_or_creating(changed, start, end):
    e = changed
    e['client'].tasks[101]['execution'] = 999
    with pytest.raises(HTTPException) as exc:
        e['change'].dispatch(e['req'].id, e['admin'], source_task_id=101, owner_id=e['alice'].id,
                             expected_story_version=6, task_start_date=start, task_deadline=end, mode='create')
    assert exc.value.status_code == 400
    assert e['db'].query(RequirementChangeRetest).count() == 0
    assert len(e['client'].tasks) == 2


def test_explicit_create_rejection_is_saved_and_retryable(changed):
    from app.services.zentao_v2_protocol import ZentaoAPIError
    e = changed
    e['client'].tasks[101]['execution'] = 999
    create = e['client'].create_execution_task
    def reject(*args, **kwargs):
        raise ZentaoAPIError(422, '禅道校验未通过：estStarted, deadline')
    e['client'].create_execution_task = reject
    with pytest.raises(HTTPException) as exc:
        dispatch(e)
    assert 'estStarted, deadline' in exc.value.detail
    receipt = e['db'].query(RequirementChangeRetest).one()
    assert receipt.status == 'rejected'
    assert json.loads(receipt.previous_state)['dispatch_stage'] == 'create'
    task = next(t for t in e['change'].preview(e['req'].id, e['admin'])['tasks'] if t['task_id'] == 101)
    assert 'deadline' in task['dispatch_error'] and not task['needs_create_confirmation']
    e['client'].create_execution_task = create
    assert dispatch(e)['ok']
    assert len(e['client'].tasks) == 3
    assert e['db'].query(RequirementChangeRetest).count() == 1


def test_legacy_unknown_receipt_requires_confirmation_before_recreating(changed):
    e = changed
    e['client'].tasks[101]['execution'] = 999
    source = e['db'].get(ZentaoTaskMirror, 101)
    source.execution_id = 999
    receipt = RequirementChangeRetest(requirement_id=e['req'].id, source_task_id=101,
        story_version=6, owner_id=e['alice'].id, actor_id=e['admin'].id, status='unknown')
    e['db'].add(receipt)
    e['db'].commit()
    with pytest.raises(HTTPException) as exc:
        dispatch(e)
    assert exc.value.status_code == 409
    assert len(e['client'].tasks) == 2
    assert e['change'].preview(e['req'].id, e['admin'])['tasks'][0]['needs_create_confirmation']
    result = e['change'].dispatch(e['req'].id, e['admin'], source_task_id=101, owner_id=e['alice'].id,
                                 expected_story_version=6, task_start_date='2026-11-13',
                                 task_deadline='2026-11-16', retry_unconfirmed=True, mode='create')
    assert result['ok'] and len(e['client'].tasks) == 3
    assert e['db'].query(RequirementChangeRetest).one().id == receipt.id


def test_confirmed_retry_still_recovers_remote_task_before_creating(changed):
    e = changed
    e['client'].tasks[101]['execution'] = 999
    create = e['client'].create_execution_task
    def timeout(*args, **kwargs):
        create(*args, **kwargs)
        raise TimeoutError()
    e['client'].create_execution_task = timeout
    with pytest.raises(HTTPException):
        dispatch(e)
    result = e['change'].dispatch(e['req'].id, e['admin'], source_task_id=101, owner_id=e['alice'].id,
                                 expected_story_version=6, retry_unconfirmed=True, mode='create')
    assert result['ok'] and len(e['client'].tasks) == 3


def test_failed_recovery_does_not_turn_safe_create_into_unknown(changed, monkeypatch):
    e = changed
    e['client'].tasks[101]['execution'] = 999
    def unavailable(*args):
        raise TimeoutError()
    with monkeypatch.context() as patch:
        patch.setattr(e['client'], 'list_execution_tasks', unavailable)
        with pytest.raises(HTTPException):
            dispatch(e)
    assert e['db'].query(RequirementChangeRetest).one().status == 'prepared'
    assert dispatch(e)['ok']


def test_cross_version_defaults_to_original_task_and_keeps_acknowledgement_pending(changed):
    e = changed
    e['client'].tasks[102]['execution'] = 999
    e['db'].get(ZentaoTaskMirror, 102).execution_id = 999
    e['db'].commit()
    result = e['change'].dispatch(e['req'].id, e['admin'], source_task_id=102,
                                 owner_id=e['alice'].id, expected_story_version=6)
    assert result['task_id'] == 102 and not result['created']
    assert len(e['client'].tasks) == 2
    task = e['client'].tasks[102]
    assert task['status'] == 'doing' and task['assignedTo'] == 'alice'
    assert task['execution'] == 999 and task['storyVersion'] == 2
    assert 102 in {t.task_id for t in e['service'].rows_by_requirement([e['req']])[e['req'].id]}
    # After the assignee confirms the new requirement, the explicit old-execution
    # task remains operable through this requirement's existing task endpoint.
    task['storyVersion'] = 6
    assert e['service'].operate(e['req'].id, 102, e['alice'], action='finish', consumed=1)['ok']


def test_existing_generated_task_can_switch_to_original_without_erasing_history(changed):
    e = changed
    e['req'].zentao_task_id = None
    e['client'].tasks[101]['status'] = 'cancel'
    e['db'].get(ZentaoTaskMirror, 101).status = 'cancel'
    e['client'].tasks[102]['execution'] = 999
    e['db'].get(ZentaoTaskMirror, 102).execution_id = 999
    e['db'].commit()
    created = dispatch(e, source=102)['task_id']
    record = e['db'].query(FinalTestRecord).filter_by(user_id=e['alice'].id).one()
    record.case_completed = True
    e['db'].commit()
    preview = e['change'].preview(e['req'].id, e['admin'])
    row = next(t for t in preview['tasks'] if t['task_id'] == 102)
    assert row['mode'] == 'reactivate' and row['existing_task_id'] == created
    result = e['change'].dispatch(e['req'].id, e['admin'], source_task_id=102,
                                 owner_id=e['alice'].id, expected_story_version=6)
    assert result['task_id'] == 102
    assert e['client'].tasks[created]['status'] == 'wait'  # never cancel/delete the previous copy
    receipt = e['db'].query(RequirementChangeRetest).one()
    state = json.loads(receipt.previous_state)
    assert state['replaced_task_id'] == created and state['test_completed'] is True
    assert record.case_completed
    rows = e['service'].rows_by_requirement([e['req']])[e['req'].id]
    assert 102 in {r.task_id for r in rows} and created not in {r.task_id for r in rows}
    calls = len(e['client'].calls)
    assert e['change'].dispatch(e['req'].id, e['admin'], source_task_id=102,
        owner_id=e['alice'].id, expected_story_version=6)['already_dispatched']
    assert len(e['client'].calls) == calls
    e['client'].tasks[102]['storyVersion'] = 6
    e['service'].operate(e['req'].id, 102, e['alice'], action='finish', consumed=1)
    assert e['req'].test_completed
    assert e['change'].summaries([e['req'].id])[e['req'].id][0]['status'] == 'completed'


def test_failed_switch_keeps_previous_task_binding(changed, monkeypatch):
    e = changed
    e['client'].tasks[102]['execution'] = 999
    created = dispatch(e, source=102)['task_id']
    monkeypatch.setattr(e['client'], 'restart_task', lambda *a, **kw: None)
    with pytest.raises(HTTPException):
        e['change'].dispatch(e['req'].id, e['admin'], source_task_id=102,
                             owner_id=e['alice'].id, expected_story_version=6)
    receipt = e['db'].query(RequirementChangeRetest).one()
    assert receipt.status == 'active' and receipt.task_id == created
    assert 'replaced_task_id' not in json.loads(receipt.previous_state)


def test_switching_to_original_can_choose_a_different_assignee(changed):
    e = changed
    e['client'].tasks[102]['execution'] = 999
    dispatch(e, source=102)
    result = e['change'].dispatch(e['req'].id, e['admin'], source_task_id=102,
                                 owner_id=e['bob'].id, expected_story_version=6)
    assert result['task_id'] == 102 and e['client'].tasks[102]['assignedTo'] == 'bob'
    receipt = e['db'].query(RequirementChangeRetest).one()
    assert receipt.owner_id == e['bob'].id
    state = json.loads(receipt.previous_state)
    assert state['prior_dispatch']['owner_id'] == e['alice'].id
    assert state['prior_dispatch']['previous_state']['test_completed'] is True
    assert state['test_completed'] is True
