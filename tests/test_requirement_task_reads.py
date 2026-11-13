import pytest
from fastapi import HTTPException

from test_requirement_tasks import env
from app.services.zentao_v2_protocol import ZentaoAPIError


def test_pause_recovers_live_task_from_execution_list(env, monkeypatch):
    e = env
    e['client'].tasks[102]['status'] = 'doing'
    def unavailable(*a, **kw):
        raise ZentaoAPIError(500, 'task history unavailable')
    monkeypatch.setattr(e['client'], 'get_task', unavailable)
    result = e['service'].operate(e['req'].id, 102, e['alice'], action='pause')
    assert result['ok']
    assert e['client'].tasks[102]['status'] == 'pause'
    assert sum(fields.get('status') == 'pause' for _, _, fields in e['client'].calls) == 1


def test_failed_preflight_is_marked_not_started_without_writes(env, monkeypatch):
    e = env
    def unavailable(*a, **kw):
        raise ZentaoAPIError(502, 'unavailable')
    monkeypatch.setattr(e['client'], 'get_task', unavailable)
    monkeypatch.setattr(e['client'], 'list_execution_tasks', unavailable)
    with pytest.raises(HTTPException) as exc:
        e['service'].operate(e['req'].id, 102, e['alice'], action='pause')
    assert exc.value.status_code == 502
    assert exc.value.headers['X-Operation-Outcome'] == 'not-started'
    assert e['client'].calls == []
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.api.deps import get_current_user, get_db
    from app.api.routes.requirements import router
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db] = lambda: e['db']
    app.dependency_overrides[get_current_user] = lambda: e['alice']
    with TestClient(app) as client:
        response = client.post(f"/requirements/{e['req'].id}/linked-tasks/102/operate", json={'action': 'pause'})
    assert response.status_code == 502
    assert response.headers['X-Operation-Outcome'] == 'not-started'
    assert e['client'].calls == []


def test_preflight_rejects_task_from_wrong_execution(env, monkeypatch):
    e = env
    monkeypatch.setattr(e['client'], 'get_task', lambda *_: None)
    monkeypatch.setattr(e['client'], 'list_execution_tasks', lambda *_: [
        {**e['client'].tasks[102], 'execution': 999},
    ])
    with pytest.raises(HTTPException) as exc:
        e['service'].operate(e['req'].id, 102, e['alice'], action='pause')
    assert exc.value.headers['X-Operation-Outcome'] == 'not-started'
    assert e['client'].calls == []
