from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from app.api.deps import get_current_user, get_db
from app.api.routes import automation
from app.core.exceptions import AppError
from app.models import AutomationRepository, AutomationRun, AutomationCaseResult, User, UserRole
from app.schemas.automation import CatalogUpload, RunCreate, RunResultUpload, BuildStarted
from app.services import automation_service as svc, automation_zentao as zt

SHA = 'a' * 40


def catalog_payload(ids=(41,), revision=SHA):
    return CatalogUpload(revision=revision, branch='main', cases=[{
        'case_id': cid, 'automation_id': f'sm.{i}', 'path': f'tests/smoke/{i}.robot', 'name': f'Test {i}',
        'tags': [f'zentao:{cid}'] if cid else []} for i, cid in enumerate(ids)])


class Jenkins:
    def __init__(self):
        self.calls = []
        self.fail = False

    def get_job(self, name):
        return {'property': [{'parameterDefinitions': [{'name': x} for x in ('AUTOMATION_RUN_ID', 'ZENTAO_CASE_IDS',
                'ZENTAO_TESTTASK_ID', 'TEST_REVISION', 'TEST_BRANCH', 'DEVICE_ID', 'APK_PATH', 'PACKAGE', 'OMNIQA_ENABLED', 'OMNIQA_DRY_RUN', 'OMNIQA_REPOSITORY_ID')]}]}

    def trigger_build(self, job, params):
        self.calls.append(params)
        if self.fail:
            raise TimeoutError()
        return 'http://jenkins.test/queue/item/7/'

    def get_build(self, job, number):
        params = self.calls[-1] if self.calls else {}
        return {'number': number, 'actions': [{'parameters': [{'name': k, 'value': v} for k, v in params.items()]}]}

    def get_queue_item(self, url):
        return {'executable': {'number': 18}}


class Zentao:
    def __init__(self):
        self.submissions = []
        self.unknown = False

    def prepare(self, repo, task, ids):
        return [{'case_id': cid, 'case_version': 3, 'run_id': 100 + cid, 'step_ids': [500 + cid],
                 'title': f'Case {cid}', 'definition_hash': 'unchanged'} for cid in sorted(ids)]

    def close(self):
        pass

    def submit(self, repo, run, plan, result, reconcile_only=False):
        self.submissions.append((result.case_id, reconcile_only))
        if self.unknown:
            raise zt.ResultOutcomeUnknown()
        return 998


@pytest.fixture
def env(db_session, monkeypatch):
    db = db_session
    owner = User(username='automation_owner', password_hash='x', role=UserRole.USER)
    db.add(owner)
    db.commit()
    repo = AutomationRepository(owner_id=owner.id, name='SurveyMaster', git_url='git@git.test:qa/sm.git', branch='main',
             jenkins_job='SurveyMaster-RF-Test', jenkins_base_url='http://jenkins.test', zentao_base_url='http://zentao.test',
             product_id=15, execution_id=1289, default_testtask_id=9, integration_key_hash=svc.digest('repo-secret'))
    db.add(repo)
    db.commit()
    svc.save_catalog(db, repo, catalog_payload())
    jenkins, zentao = Jenkins(), Zentao()
    monkeypatch.setattr(svc, '_jenkins', lambda *a: jenkins)
    monkeypatch.setattr(zt, 'client_for_repository', lambda *a: zentao)
    return SimpleNamespace(db=db, owner=owner, repo=repo, jenkins=jenkins, zentao=zentao)


def request_payload(**overrides):
    return RunCreate.model_validate({'case_ids': [41], 'testtask_id': 9, 'device_serial': 'android-1',
                'apk_reference': r'D:\apks\sm.apk', 'idempotency_key': 'request_123456', 'parameters': {}} | overrides)


def start(env):
    return svc.create_run(env.db, env.repo, env.owner.id, request_payload())


def result_payload(**overrides):
    return RunResultUpload.model_validate({'job_name': 'SurveyMaster-RF-Test', 'build_number': 18, 'revision': SHA,
       'device_serial': 'android-1', 'outcome': 'completed', 'results': [{'case_id': 41, 'status': 'FAIL',
        'duration_seconds': 1.25, 'message': 'Assertion failed'}]} | overrides)


def test_catalog_rejects_duplicate_ids_atomically(env):
    with pytest.raises(AppError, match='重复'):
        svc.save_catalog(env.db, env.repo, catalog_payload((41, 41), 'b' * 40))
    assert env.repo.catalog_revision == SHA


def test_catalog_same_revision_is_immutable(env):
    changed = catalog_payload()
    changed.cases[0].name = 'Renamed'
    with pytest.raises(AppError, match='同一 Git'):
        svc.save_catalog(env.db, env.repo, changed)


@pytest.mark.parametrize('path', ['tests/../escape.robot', 'tests/_hidden.robot', 'tests/a\\b.robot', 'C:/bad.robot'])
def test_catalog_rejects_unsafe_or_hidden_paths(path):
    from pydantic import ValidationError
    body = catalog_payload().model_dump()
    body['cases'][0]['path'] = path
    with pytest.raises(ValidationError):
        CatalogUpload.model_validate(body)


def test_run_freezes_context_and_is_idempotent(env):
    run = start(env)
    assert run.status == 'queued'
    assert json.loads(run.plan_json)[0]['case_version'] == 3
    assert env.jenkins.calls[0]['TEST_REVISION'] == SHA
    assert env.jenkins.calls[0]['ZENTAO_CASE_IDS'] == '41'
    assert env.jenkins.calls[0]['OMNIQA_REPOSITORY_ID'] == str(env.repo.id)
    svc.save_catalog(env.db, env.repo, catalog_payload((42,), 'b' * 40))
    assert svc.create_run(env.db, env.repo, env.owner.id, request_payload()).id == run.id
    assert len(env.jenkins.calls) == 1
    assert run.revision == SHA


def test_changed_idempotency_payload_is_rejected(env):
    start(env)
    with pytest.raises(AppError, match='参数不同'):
        svc.create_run(env.db, env.repo, env.owner.id, request_payload(device_serial='other'))
    assert len(env.jenkins.calls) == 1


@pytest.mark.parametrize('changes', [{'case_ids': [99]}, {'testtask_id': 10}, {'parameters': {'TEST_REVISION': 'evil'}},
                                   {'parameters': {'API_TOKEN': 'secret'}}, {'case_ids': [41, 41]}])
def test_run_rejects_scope_and_parameter_overrides(env, changes):
    with pytest.raises(AppError):
        svc.create_run(env.db, env.repo, env.owner.id, request_payload(**changes))
    assert not env.jenkins.calls


def test_dispatch_timeout_never_replays_the_build(env):
    env.jenkins.fail = True
    run = start(env)
    assert run.status == 'dispatch_unknown'
    start(env)
    assert len(env.jenkins.calls) == 1


def test_results_require_exact_build_and_frozen_revision(env):
    run = start(env)
    with pytest.raises(AppError):
        svc.receive_results(env.db, env.repo, run, result_payload(revision='b' * 40))
    assert env.db.query(AutomationCaseResult).count() == 0


def test_results_require_exact_case_set_and_are_immutable(env):
    run = start(env)
    with pytest.raises(AppError):
        svc.receive_results(env.db, env.repo, run, result_payload(results=[]))
    svc.receive_results(env.db, env.repo, run, result_payload())
    svc.receive_results(env.db, env.repo, run, result_payload())
    assert env.db.query(AutomationCaseResult).count() == 1
    assert svc.serialize_run(env.db, run)['summary']['FAIL'] == 1
    with pytest.raises(AppError):
        svc.receive_results(env.db, env.repo, run, result_payload(message='different'))


def test_missing_xml_records_environment_failure_without_fake_results(env):
    run = start(env)
    svc.receive_results(env.db, env.repo, run, result_payload(outcome='environment_error', results=[]))
    assert run.status == 'environment_error'
    assert svc.serialize_run(env.db, run)['summary']['missing_case_ids'] == [41]
    assert env.db.query(AutomationCaseResult).count() == 0


def test_skip_is_preserved_and_not_uploaded_as_pass_or_failure(env):
    run = start(env)
    payload = result_payload()
    payload.results[0].status = 'SKIP'
    svc.receive_results(env.db, env.repo, run, payload)
    env.repo.writeback_enabled = True
    svc.synchronize_results(env.db, env.repo, run)
    assert env.db.query(AutomationCaseResult).one().sync_status == 'skipped'
    assert not env.zentao.submissions


def test_dry_run_results_cannot_be_written_to_zentao(env):
    run = svc.create_run(env.db, env.repo, env.owner.id, request_payload(dry_run=True))
    payload = result_payload(dry_run=True)
    payload.results[0].status = 'PASS'
    svc.receive_results(env.db, env.repo, run, payload)
    env.repo.writeback_enabled = True
    svc.synchronize_results(env.db, env.repo, run)
    assert env.db.query(AutomationCaseResult).one().sync_status == 'skipped'
    assert not env.zentao.submissions


def test_callback_cannot_change_frozen_dry_run_mode(env):
    run = start(env)
    with pytest.raises(AppError, match='演练模式'):
        svc.receive_results(env.db, env.repo, run, result_payload(dry_run=True))
    assert run.result_hash is None


def test_unknown_result_only_allows_readonly_reconciliation(env):
    run = start(env)
    svc.receive_results(env.db, env.repo, run, result_payload())
    env.repo.writeback_enabled = True
    env.zentao.unknown = True
    svc.synchronize_results(env.db, env.repo, run)
    svc.synchronize_results(env.db, env.repo, run)
    assert env.zentao.submissions == [(41, False), (41, True)]
    env.zentao.unknown = False
    svc.synchronize_results(env.db, env.repo, run)
    row = env.db.query(AutomationCaseResult).one()
    assert row.sync_status == 'synced' and row.zentao_result_id == 998
    svc.synchronize_results(env.db, env.repo, run)
    assert len(env.zentao.submissions) == 3


def test_jwt_and_repository_key_are_scoped(env):
    other = User(username='other', password_hash='x', role=UserRole.USER)
    env.db.add(other)
    env.db.commit()
    with pytest.raises(AppError):
        svc.repository_for_user(env.db, env.repo.id, other)
    for key in ['', 'bad-key']:
        with pytest.raises(AppError):
            svc.repository_for_key(env.db, env.repo.id, key)
    assert svc.repository_for_key(env.db, env.repo.id, 'repo-secret').id == env.repo.id


def test_callback_api_rejects_wrong_key_and_accepts_duplicate_receipt(env):
    run = start(env)
    app = FastAPI()
    app.include_router(automation.router)
    app.dependency_overrides[get_db] = lambda: env.db
    app.dependency_overrides[get_current_user] = lambda: env.owner

    @app.exception_handler(AppError)
    async def handler(request, exc):
        return JSONResponse({'message': exc.message}, status_code=exc.status_code)

    client = TestClient(app)
    path = f'/automation/integrations/{env.repo.id}/runs/{run.id}/results'
    assert client.post(path, json=result_payload().model_dump()).status_code == 401
    headers = {'X-Automation-Key': 'repo-secret'}
    assert client.post(path, headers=headers, json=result_payload().model_dump()).status_code == 200
    assert client.post(path, headers=headers, json=result_payload().model_dump()).status_code == 200
    assert env.db.query(AutomationCaseResult).count() == 1
    detail = client.get(f'/automation/runs/{run.id}').json()
    assert detail['results'][0]['status'] == 'FAIL'
    assert 'integration_key_hash' not in client.get('/automation/repositories').text


def test_zentao_launch_keeps_actor_and_validates_jenkins_run_id(env):
    app = FastAPI()
    app.include_router(automation.router)
    app.dependency_overrides[get_db] = lambda: env.db

    @app.exception_handler(AppError)
    async def handler(request, exc):
        return JSONResponse({'message': exc.message}, status_code=exc.status_code)

    client = TestClient(app)
    payload = request_payload(dry_run=True).model_dump() | {'zentao_account': 'tester'}
    path = f'/automation/integrations/{env.repo.id}/launch'
    assert client.post(path, json=payload).status_code == 401
    response = client.post(path, json=payload, headers={'X-Automation-Key': 'repo-secret'})
    assert response.status_code == 201
    run = env.db.get(AutomationRun, response.json()['id'])
    assert run.source == 'zentao' and run.external_actor == 'tester'
    assert len(env.jenkins.calls) == 1
    env.jenkins.calls[-1]['AUTOMATION_RUN_ID'] = 'different-run'
    with pytest.raises(AppError, match='运行编号'):
        svc.bind_build(env.db, env.repo, run, BuildStarted(job_name='SurveyMaster-RF-Test', build_number=18,
                       revision=SHA, device_serial='android-1'))


def test_shared_key_cannot_bind_build_from_another_project(env):
    run = start(env)
    env.repo.integration_key_hash = ''
    env.jenkins.calls[-1]['OMNIQA_REPOSITORY_ID'] = str(env.repo.id + 1)
    with pytest.raises(AppError, match='平台项目 ID'):
        svc.bind_build(env.db, env.repo, run, BuildStarted(job_name='SurveyMaster-RF-Test', build_number=18,
                       revision=SHA, device_serial='android-1'))
    assert run.build_number is None
