from datetime import datetime

import pytest
from sqlalchemy import event

from app.models import (BugSourceType, BugTracking, FinalTestRecord, Requirement,
                        TestCase as Case, User, Version, VersionType, ZentaoTaskMirror,
                        ZentaoTestCaseMirror)
from app.services.workbench_service import WorkbenchService


def seed_mine(db, count, *, final=False, reconcile=False):
    viewer = User(username='mine_viewer', password_hash='x', zentao_account='viewer')
    major = Version(version_no='mine-major', version_type=VersionType.MAJOR,
                    final_test_enabled=final, zentao_execution_id=100)
    db.add_all([viewer, major])
    db.flush()
    for i in range(count):
        owner = User(username=f'mine_owner_{i}', password_hash='x', zentao_account=f'owner{i}')
        db.add(owner)
        db.flush()
        req = Requirement(zentao_req_id=f'r#{1000+i}', title=f'Mine {i}', major_version_id=major.id,
                          owner_id=owner.id, zentao_story_id=1000+i,
                          zentao_task_id=10000+i*2, zentao_task_status_cache='done',
                          test_completed=not reconcile, test_completed_at=datetime(2026, 1, 1))
        db.add(req)
        db.flush()
        db.add_all([
            Case(requirement_id=req.id, zentao_case_id=f'u#{2000+i}', zentao_case_numeric_id=2000+i),
            ZentaoTestCaseMirror(zentao_case_id=f'u#{2000+i}', zentao_case_numeric_id=2000+i,
                                zentao_story_id=1000+i),
            BugTracking(major_version_id=major.id, bug_id=f'b#{3000+i}',
                        source_type=BugSourceType.MANUAL, zentao_story_id=1000+i,
                        zentao_linked_case_id=str(2000+i), zentao_opened_at=datetime(2026, 1, 2)),
            FinalTestRecord(requirement_id=req.id, user_id=viewer.id, case_completed=True,
                            test_completed=i % 2 == 0),
        ])
        for n in range(2):
            db.add(ZentaoTaskMirror(task_id=10000+i*2+n, execution_id=100, story=1000+i,
                                    type='test', name=f'Task {i}/{n}', status='done',
                                    assigned_to='viewer', finished_date=datetime(2026, 1, 1)))
    db.commit()
    return viewer, major


@pytest.mark.parametrize('count,final,reconcile,limit', [(10, False, False, 18),
                                                        (401, True, False, 30),
                                                        (50, False, True, 40)])
def test_mine_bounded_queries_and_no_remote_wait(db_session, monkeypatch, count, final, reconcile, limit):
    viewer, major = seed_mine(db_session, count, final=final, reconcile=reconcile)
    viewer_id, major_id = viewer.id, major.id
    db_session.expire_all()
    viewer = db_session.get(User, viewer_id)
    def forbidden(*args):
        raise AssertionError('List GET must not contact Zentao')
    monkeypatch.setattr(WorkbenchService, '_maybe_sync_task_status', forbidden)
    statements = []
    def record(conn, cursor, statement, params, context, many):
        statements.append(statement)
    event.listen(db_session.bind, 'after_cursor_execute', record)
    try:
        rows = WorkbenchService(db_session).get_my_workbench(current_user=viewer, major_version_id=major_id)
    finally:
        event.remove(db_session.bind, 'after_cursor_execute', record)
    assert len(rows) == count
    selects = [q for q in statements if q.lstrip().startswith('SELECT')]
    assert len(selects) <= limit, (len(selects), statements)
    if not reconcile:
        assert len(selects) == len(statements)
    for row in rows:
        i = int(row['title'].split()[-1])
        assert row['has_multiple_test_tasks']
        assert len(row['test_tasks']) == 2
        assert row['test_completed'] == (i % 2 == 0 if final else True)
        assert row['test_cases'][0]['bugs'][0]['bug_id'] == f'b#{3000+i}'


def test_prefetch_same_response_and_no_stale_cross_request_cache(db_session, monkeypatch):
    from app.services import workbench_service as module
    from app.services.workbench_link_service import WorkbenchLinkService
    viewer, major = seed_mine(db_session, 4)
    svc = WorkbenchService(db_session)
    optimized = svc.get_my_workbench(current_user=viewer, major_version_id=major.id)
    with monkeypatch.context() as patch:
        patch.setattr(module, 'WorkbenchLinkService', lambda db, **kw: WorkbenchLinkService(db))
        assert optimized == svc.get_my_workbench(current_user=viewer, major_version_id=major.id)
    bug = db_session.query(BugTracking).filter_by(bug_id='b#3000').one()
    bug.zentao_deleted = True
    db_session.commit()
    rows = svc.get_my_workbench(current_user=viewer, major_version_id=major.id)
    assert next(r for r in rows if r['title'] == 'Mine 0')['test_cases'][0]['bugs'] == []


def test_all_pending_reads_local_tasks_without_sync(db_session, monkeypatch):
    viewer, major = seed_mine(db_session, 2)
    task = db_session.get(ZentaoTaskMirror, 10000)
    task.status = 'doing'
    db_session.commit()
    monkeypatch.setattr(WorkbenchService, '_maybe_sync_task_status', lambda *_: pytest.fail('remote GET sync'))
    rows = WorkbenchService(db_session).get_my_workbench(current_user=viewer, mode='all_pending')
    assert [r['title'] for r in rows] == ['Mine 0']
    assert rows[0]['test_completed'] is False


def test_background_sync_throttled_single_flight_and_recovers(db_session, monkeypatch):
    from app.services import workbench_service as module
    from app.services.zentao_task_sync_service import ZentaoTaskSyncService
    from app.services.zentao_task_mirror_service import ZentaoTaskMirrorService
    svc = WorkbenchService(db_session)
    monkeypatch.setattr(module, '_task_status_sync_at', {})
    monkeypatch.setattr(module, '_task_status_sync_running', set())
    calls = []
    def sync(self, major):
        calls.append(major)
        # A second caller arriving while the first is running must not sync again.
        assert svc._maybe_sync_task_status(major) is False
    monkeypatch.setattr(ZentaoTaskSyncService, 'sync_tasks_status_for_major', sync)
    monkeypatch.setattr(ZentaoTaskMirrorService, 'sync_one_major', lambda *_: None)
    assert svc._maybe_sync_task_status(1) is True
    assert svc._maybe_sync_task_status(1) is False
    assert calls == [1]
    def fail(*args):
        raise RuntimeError('Zentao unavailable')
    monkeypatch.setattr(ZentaoTaskMirrorService, 'sync_one_major', fail)
    assert svc._maybe_sync_task_status(2) is False
    assert module._task_status_sync_running == set()
    module._task_status_sync_at.clear()
    monkeypatch.setattr(ZentaoTaskMirrorService, 'sync_one_major', lambda *_: None)
    assert svc._maybe_sync_task_status(2) is True


def test_refresh_endpoint_scopes_version_and_assigned_all_pending(db_session, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.api.deps import get_current_user, get_db
    from app.api.routes.workbench import router
    from app.models.software import SoftwareProduct
    viewer, major = seed_mine(db_session, 1)
    software = SoftwareProduct(name='mine-software')
    other = SoftwareProduct(name='other-software')
    db_session.add_all([software, other])
    db_session.flush()
    major.software_id = software.id
    db_session.add(Version(version_no='mine-minor', version_type=VersionType.MINOR,
                           parent_id=major.id, software_id=software.id, zentao_execution_id=100))
    db_session.commit()
    calls = []
    monkeypatch.setattr(WorkbenchService, '_maybe_sync_task_status', lambda self, mid: calls.append(mid) or True)
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: viewer
    app.dependency_overrides[get_db] = lambda: db_session
    with TestClient(app) as client:
        response = client.post('/workbench/refresh-task-status', json={
            'major_version_id': major.id, 'software_id': other.id})
        assert response.status_code == 200 and not response.json()['refreshed']
        assert calls == []
        response = client.post('/workbench/refresh-task-status', json={
            'mode': 'all_pending', 'software_id': software.id})
        assert response.status_code == 200 and response.json()['refreshed']
        assert calls == [major.id]
        assert client.post('/workbench/refresh-task-status', json={'mode': 'invalid'}).status_code == 422
