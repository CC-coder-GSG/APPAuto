from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest

from app.core.exceptions import AppError
from app.services import automation_zentao as zt


@pytest.fixture
def remote(monkeypatch):
    state = SimpleNamespace(case={'id': 41, 'product': 15, 'title': 'Original title', 'precondition': 'Original precondition',
            'type': 'feature', 'stage': 'feature', 'pri': 2, 'status': 'normal', 'version': 3, 'deleted': '0',
            'steps': [{'id': 541, 'desc': 'A step', 'expect': 'Expected', 'type': 'step'}]},
            history=[], writes=[], timeout=False, commit=True, changed=False)

    def handle(request):
        path = request.url.path
        assert path.startswith('/api.php/v1/')
        assert 'ciresults' not in path
        if path.endswith('/tokens'):
            return httpx.Response(201, json={'token': 'separate-result-token'})
        assert request.headers.get('Token') == 'separate-result-token'
        if path.endswith('/testtasks/9'):
            return httpx.Response(200, json={'id': 9, 'product': 15, 'execution': 1289, 'build': '22', 'status': 'doing',
                      'testcases': [{'id': 141, 'case': 41, 'version': 3}]})
        if path.endswith('/testcases/41'):
            return httpx.Response(200, json=state.case)
        assert path.endswith('/testcases/41/results')
        if request.method == 'GET':
            return httpx.Response(200, json={'results': state.history})
        assert request.method == 'POST'
        body = json.loads(request.content)
        assert request.url.params['testtask'] == '9'
        assert request.url.params['version'] == '3'
        assert set(body) == {'steps'}
        state.writes.append(body)
        if state.commit:
            status = 'fail' if any(x['result'] == 'fail' for x in body['steps']) else 'pass'
            state.history.append({'id': 998, 'case': 41, 'run': 141, 'version': 3, 'caseResult': status, 'stepResults': body['steps']})
        if state.changed:
            state.case['title'] = 'Changed concurrently'
        if state.timeout:
            raise httpx.ReadTimeout('unknown', request=request)
        return httpx.Response(200, json={})

    real_client = httpx.Client
    monkeypatch.setattr(zt.httpx, 'Client', lambda **kwargs: real_client(transport=httpx.MockTransport(handle), **kwargs))
    client = zt.AutomationZentaoClient('https://zentao.test', 'tester', 'test-password')
    repository = SimpleNamespace(product_id=15, execution_id=1289, target_build_id=22)
    run = SimpleNamespace(id='test-run', testtask_id=9, revision='a' * 40, build_url='https://jenkins.test/job/sm/18/')
    result = SimpleNamespace(case_id=41, status='FAIL', message='Assertion failed', duration_seconds='1.5', evidence_json='{"steps":[]}')
    yield state, client, repository, run, result
    client.close()


def test_execution_only_post_preserves_definitions_and_reconciles_duplicate(remote):
    state, client, repository, run, result = remote
    plan = client.prepare(repository, 9, [41])[0]
    before = zt.definition_fingerprint(state.case)
    assert client.submit(repository, run, plan, result) == 998
    assert client.submit(repository, run, plan, result) == 998
    assert len(state.writes) == 1
    assert zt.definition_fingerprint(state.case) == before


@pytest.mark.parametrize('deleted', [False, 0, '0', True, 1, '1'])
def test_ipd_deleted_flag_boolean_and_legacy_numeric_shapes(remote, deleted):
    state, client, repository, run, result = remote
    state.case['deleted'] = deleted
    if deleted in (False, 0, '0'):
        assert client.prepare(repository, 9, [41])[0]['case_version'] == 3
    else:
        with pytest.raises(AppError, match='产品、版本'):
            client.prepare(repository, 9, [41])


def test_committed_timeout_is_reconciled_without_second_post(remote):
    state, client, repository, run, result = remote
    plan = client.prepare(repository, 9, [41])[0]
    state.timeout = True
    assert client.submit(repository, run, plan, result) == 998
    assert len(state.writes) == 1


def test_unconfirmed_timeout_blocks_automatic_replay(remote):
    state, client, repository, run, result = remote
    plan = client.prepare(repository, 9, [41])[0]
    state.timeout, state.commit = True, False
    with pytest.raises(zt.ResultOutcomeUnknown):
        client.submit(repository, run, plan, result)
    with pytest.raises(zt.ResultOutcomeUnknown):
        client.submit(repository, run, plan, result, reconcile_only=True)
    assert len(state.writes) == 1


def test_changed_definition_blocks_post(remote):
    state, client, repository, run, result = remote
    plan = client.prepare(repository, 9, [41])[0]
    state.case['title'] = 'Edited by another user'
    with pytest.raises(AppError, match='已变化'):
        client.submit(repository, run, plan, result)
    assert not state.writes


def test_wrong_execution_build_and_missing_case_are_rejected(remote):
    state, client, repository, run, result = remote
    repository.execution_id = 999
    with pytest.raises(AppError, match='研发执行'):
        client.prepare(repository, 9, [41])
    repository.execution_id = 1289
    repository.target_build_id = 23
    with pytest.raises(AppError, match='构建'):
        client.prepare(repository, 9, [41])
    repository.target_build_id = 22
    with pytest.raises(AppError, match='尚未关联'):
        client.prepare(repository, 9, [42])


def test_multiple_steps_require_explicit_verified_mapping(remote):
    state, client, repository, run, result = remote
    state.case['steps'].append({'id': 542, 'desc': 'Second', 'expect': 'Expected', 'type': 'step'})
    plan = client.prepare(repository, 9, [41])[0]
    with pytest.raises(AppError, match='多步骤'):
        client.submit(repository, run, plan, result)
    assert not state.writes
    result.evidence_json = json.dumps({'steps': [{'step_id': 541, 'status': 'PASS', 'message': ''},
                                               {'step_id': 542, 'status': 'FAIL', 'message': 'Second failed'}]})
    assert client.submit(repository, run, plan, result) == 998
    assert [x['result'] for x in state.writes[0]['steps']] == ['pass', 'fail']
