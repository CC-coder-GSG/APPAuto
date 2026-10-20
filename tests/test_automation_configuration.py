from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.api.deps import get_current_user, get_db
from app.api.routes import automation
from app.core.exceptions import AppError
from app.models import AutomationRepository, User, UserRole, UserZentaoBinding
from app.schemas.automation import RepositoryCreate, RunCreate
from app.services import automation_service as svc
from app.services.zentao_client_service import ZentaoClient, ZentaoAPIError


@pytest.fixture
def configuration(db_session, monkeypatch):
    user = User(username='config-owner', password_hash='x', role=UserRole.USER)
    db_session.add(user)
    db_session.commit()
    db_session.add(UserZentaoBinding(user_id=user.id, base_url='http://zentao.test/zentao/',
                                    zentao_account='qa-owner', zentao_password_ciphertext='encrypted-private-value'))
    db_session.commit()
    monkeypatch.setattr(svc.jenkins_service, 'get_binding_creds', lambda *args: ('http://jenkins.test', 'qa', 'private-token'))
    return db_session, user


def repository_payload(**changes):
    return RepositoryCreate.model_validate(dict(name='自定义名称', git_url='git@github.com:org/tests.git',
                                                jenkins_job='folder/SurveyMaster', product_id=15) | changes)


@pytest.mark.parametrize('git_url', ['git@github.com:org/tests.git', 'ssh://git@github.com:2222/org/tests.git',
                                  'https://github.com/org/tests.git'])
def test_create_uses_bound_site_and_preserves_git_transport(configuration, git_url):
    db, user = configuration
    result = svc.create_repository(db, user, repository_payload(git_url=git_url))
    row = db.get(AutomationRepository, result['repository']['id'])
    assert row.zentao_base_url == 'http://zentao.test/zentao'
    assert row.name == '自定义名称' and row.git_url == git_url
    assert row.default_testtask_id is None and not row.writeback_enabled
    assert row.integration_key_hash != result['integration_key']


def test_legacy_payload_must_match_bound_site(configuration):
    db, user = configuration
    svc.create_repository(db, user, repository_payload(zentao_base_url='http://zentao.test/zentao'))
    with pytest.raises(AppError, match='本人已绑定'):
        svc.create_repository(db, user, repository_payload(zentao_base_url='http://other.test'))
    assert db.query(AutomationRepository).count() == 1


def test_missing_binding_and_binding_summary_do_not_expose_secrets(configuration):
    db, user = configuration
    summary = svc.configuration_bindings(db, user)
    assert summary == {'zentao_base_url': 'http://zentao.test/zentao', 'jenkins_base_url': 'http://jenkins.test'}
    db.query(UserZentaoBinding).delete()
    db.commit()
    with pytest.raises(AppError, match='绑定禅道'):
        svc.create_repository(db, user, repository_payload())


def test_options_authenticate_as_current_user(configuration, monkeypatch):
    db, user = configuration
    calls = []
    monkeypatch.setattr(svc, 'get_valid_token', lambda uid, session: calls.append(uid) or 'v2-token')
    client = svc._configuration_client(db, user)
    assert client.account == 'qa-owner' and client.base_url == 'http://zentao.test/zentao'
    assert calls == [user.id]


def test_testtasks_filter_execution_build_and_completed_status(configuration, monkeypatch):
    db, user = configuration
    rows = [
        dict(id=1, name='匹配测试单', execution={'id': 1289}, build=[{'id': 4843}], status='doing'),
        dict(id=2, name='其他执行', execution=999, build='4843', status='wait'),
        dict(id=3, name='其他构建', execution=1289, build='999', status='doing'),
        dict(id=4, name='已完成', execution=1289, build='4843', status='done'),
        dict(id=5, name='另一个可用测试单', execution=1289, build={'4843': 'v1'}, status='wait'),
    ]
    monkeypatch.setattr(svc, '_configuration_client', lambda *args: SimpleNamespace(list_product_testtasks=lambda pid: rows))
    result = svc.zentao_options(db, user, 'testtasks', product_id=15, execution_id=1289, build_id=4843)
    assert [row['id'] for row in result['items']] == [1, 5]
    assert [row['id'] for row in svc.zentao_options(db, user, 'testtasks', product_id=15)['items']] == [1, 2, 3, 5]


def test_builds_exclude_other_products_and_remote_errors_are_explicit(configuration, monkeypatch):
    db, user = configuration
    client = SimpleNamespace(list_execution_builds=lambda eid: [dict(id=1, name='v1', product={'id': 15}),
                                                               dict(id=2, name='other', product=9)])
    monkeypatch.setattr(svc, '_configuration_client', lambda *args: client)
    assert [row['id'] for row in svc.zentao_options(db, user, 'builds', product_id=15, execution_id=1289)['items']] == [1]

    def rejected(*args):
        raise ZentaoAPIError(403, '禅道拒绝访问，请检查当前账号权限')
    client.list_execution_builds = rejected
    with pytest.raises(AppError, match='获取禅道选项失败'):
        svc.zentao_options(db, user, 'builds', product_id=15, execution_id=1289)


def test_admin_run_choices_use_project_owner_and_keep_default_scope(configuration, monkeypatch):
    db, owner = configuration
    repository = SimpleNamespace(owner_id=owner.id, zentao_base_url='http://zentao.test/zentao',
                                 product_id=15, execution_id=1289, target_build_id=4843, default_testtask_id=1909)
    calls = []
    def choices(session, user, kind, **scope):
        calls.append((user.id, kind, scope))
        return {'items': [{'id': 1909, 'name': 'default'}, {'id': 1910, 'name': 'other'}]}
    monkeypatch.setattr(svc, 'zentao_options', choices)
    assert svc.repository_testtasks(db, repository)['items'] == [{'id': 1909, 'name': 'default'}]
    assert calls == [(owner.id, 'testtasks', {'product_id': 15, 'execution_id': 1289, 'build_id': 4843})]
    repository.zentao_base_url = 'http://old-site.test'
    with pytest.raises(AppError, match='不一致'):
        svc.repository_testtasks(db, repository)


def test_option_api_validation_permissions_and_no_configuration_secret(configuration, monkeypatch):
    db, user = configuration
    app = FastAPI()
    app.include_router(automation.router)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: user

    @app.exception_handler(AppError)
    async def error(request, exc):
        return JSONResponse({'message': exc.message}, status_code=exc.status_code)

    client = TestClient(app)
    response = client.get('/automation/configuration/bindings')
    assert response.status_code == 200 and 'private' not in response.text
    assert client.get('/automation/configuration/zentao/not-a-resource').status_code == 422
    assert client.get('/automation/configuration/zentao/testtasks?product_id=-1').status_code == 422
    assert client.get('/automation/configuration/zentao/testtasks').status_code == 422
    stranger = User(username='stranger', password_hash='x', role=UserRole.USER)
    db.add(stranger)
    db.commit()
    repo = svc.create_repository(db, user, repository_payload())['repository']
    app.dependency_overrides[get_current_user] = lambda: stranger
    assert client.get(f'/automation/repositories/{repo["id"]}/testtasks').status_code == 404
    stranger.tab_permissions = '["mine"]'
    assert client.get('/automation/configuration/bindings').status_code == 403


def test_remote_option_lists_read_all_pages(monkeypatch):
    client = ZentaoClient('http://zentao.test', 'token')
    requests = []
    def get(path, params):
        requests.append((path, params))
        key = 'testtasks' if path.endswith('testtasks') else 'products'
        return {key: [{'id': params['page'], 'name': 'row'}], 'pager': {'recTotal': 2}}
    monkeypatch.setattr(client, 'get', get)
    assert [row['id'] for row in client.list_product_testtasks(15, limit=1)] == [1, 2]
    assert requests[1] == ('products/15/testtasks', {'browseType': 'all', 'limit': 1, 'page': 2})
    assert [row['id'] for row in client.list_products(limit=1)] == [1, 2]


def test_execution_still_requires_testtask():
    with pytest.raises(ValidationError):
        RunCreate.model_validate(dict(case_ids=[1], device_serial='phone', apk_reference='app.apk',
                                      idempotency_key='request-123'))
