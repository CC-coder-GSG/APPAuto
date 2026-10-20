from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.api.deps import get_current_user, get_db
from app.api.routes import automation
from app.core.exceptions import AppError
from app.models import AutomationRepository, AutomationIntegrationCredential, User, UserRole, UserZentaoBinding
from app.schemas.automation import IntegrationKeyConfiguration, RepositoryCreate, RunCreate
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


def test_multiple_projects_reuse_one_credential_and_keep_scope(configuration):
    db, user = configuration
    first = svc.create_repository(db, user, repository_payload())
    second = svc.create_repository(db, user, repository_payload(name='Second project'))
    assert first['integration_key'] and not first['integration_key_reused']
    assert second['integration_key'] is None and second['integration_key_reused']
    assert db.query(AutomationIntegrationCredential).count() == 1
    for result in (first, second):
        row = svc.repository_for_key(db, result['repository']['id'], first['integration_key'])
        assert row.integration_key_hash == ''
    stranger = User(username='other-key-owner', password_hash='x', role=UserRole.USER)
    db.add(stranger)
    db.commit()
    for changes in ({'owner_id': stranger.id}, {'jenkins_base_url': 'http://other-jenkins.test'}):
        row = AutomationRepository(**(dict(owner_id=user.id, name='Excluded', git_url='git@github.com:org/tests.git',
            branch='main', jenkins_job='Smoke', jenkins_base_url='http://jenkins.test',
            zentao_base_url='http://zentao.test/zentao', product_id=15, integration_key_hash='') | changes))
        db.add(row)
        db.commit()
        with pytest.raises(AppError) as rejected:
            svc.repository_for_key(db, row.id, first['integration_key'])
        assert rejected.value.status_code == 401


def test_existing_project_key_can_be_adopted_then_rotated(configuration):
    db, user = configuration
    legacy = AutomationRepository(owner_id=user.id, name='Existing', git_url='git@github.com:org/tests.git',
        jenkins_job='Smoke', jenkins_base_url='http://jenkins.test', zentao_base_url='http://zentao.test/zentao',
        product_id=15, integration_key_hash=svc.digest('already-saved-in-jenkins'))
    db.add(legacy)
    db.commit()
    assert svc.integration_key_status(db, user)['legacy_repository_ids'] == [legacy.id]
    adopted = svc.configure_integration_key(db, user, IntegrationKeyConfiguration(reuse_repository_id=legacy.id))
    assert adopted['configured'] and adopted['reused_existing_key'] and adopted['integration_key'] is None
    assert legacy.integration_key_hash == ''
    new = svc.create_repository(db, user, repository_payload())
    assert new['integration_key'] is None
    for rid in (legacy.id, new['repository']['id']):
        assert svc.repository_for_key(db, rid, 'already-saved-in-jenkins').id == rid
    with pytest.raises(AppError) as conflict:
        svc.configure_integration_key(db, user, IntegrationKeyConfiguration())
    assert conflict.value.status_code == 409
    rotated = svc.configure_integration_key(db, user, IntegrationKeyConfiguration(rotate=True))['integration_key']
    for rid in (legacy.id, new['repository']['id']):
        with pytest.raises(AppError):
            svc.repository_for_key(db, rid, 'already-saved-in-jenkins')
        assert svc.repository_for_key(db, rid, rotated).id == rid


def test_adoption_does_not_take_another_users_or_servers_key(configuration):
    db, user = configuration
    other = User(username='other-key-owner', password_hash='x', role=UserRole.USER)
    db.add(other)
    db.commit()
    user.role = UserRole.ADMIN
    for changes in ({'owner_id': other.id}, {'jenkins_base_url': 'http://other-jenkins.test'}):
        row = AutomationRepository(**(dict(owner_id=user.id, name='Other scope', git_url='git@github.com:org/tests.git',
            jenkins_job='Smoke', jenkins_base_url='http://jenkins.test', zentao_base_url='http://zentao.test/zentao',
            product_id=15, integration_key_hash=svc.digest('old-key')) | changes))
        db.add(row)
        db.commit()
        with pytest.raises(AppError, match='本人当前 Jenkins'):
            svc.configure_integration_key(db, user, IntegrationKeyConfiguration(reuse_repository_id=row.id))
    assert db.query(AutomationIntegrationCredential).count() == 0


def test_shared_credentials_do_not_outlive_user_access(configuration):
    db, user = configuration
    result = svc.create_repository(db, user, repository_payload())
    user.is_active = False
    with pytest.raises(AppError):
        svc.repository_for_key(db, result['repository']['id'], result['integration_key'])
    user.is_active = True
    user.tab_permissions = '["mine"]'
    with pytest.raises(AppError):
        svc.repository_for_key(db, result['repository']['id'], result['integration_key'])


def test_shared_key_survives_reopening_database_and_status_has_no_secret(configuration):
    from sqlalchemy.orm import Session
    db, user = configuration
    result = svc.create_repository(db, user, repository_payload())
    with Session(db.get_bind()) as reopened:
        assert svc.repository_for_key(reopened, result['repository']['id'], result['integration_key']).owner_id == user.id
        status = svc.integration_key_status(reopened, reopened.get(User, user.id))
        assert status == {'configured': True, 'jenkins_base_url': 'http://jenkins.test', 'legacy_repository_ids': []}
        assert result['integration_key'] not in str(status)
        assert reopened.query(AutomationIntegrationCredential).one().key_hash == svc.digest(result['integration_key'])


def test_shared_key_api_and_catalog_authorization(configuration):
    db, user = configuration
    app = FastAPI()
    app.include_router(automation.router)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: user

    @app.exception_handler(AppError)
    async def error(request, exc):
        return JSONResponse({'message': exc.message}, status_code=exc.status_code)

    client = TestClient(app)
    path = '/automation/configuration/integration-key'
    assert client.get(path).json()['configured'] is False
    response = client.post(path, json={})
    assert response.status_code == 200
    key = response.json()['integration_key']
    status = client.get(path)
    assert status.json()['configured'] is True and key not in status.text and 'key_hash' not in status.text
    assert client.post(path, json={}).status_code == 409
    assert client.post(path, json={'rotate': True, 'reuse_repository_id': 1}).status_code == 422
    for name in ('first', 'second'):
        project = client.post('/automation/repositories', json=repository_payload(name=name).model_dump())
        assert project.status_code == 201 and project.json()['integration_key'] is None
        rid = project.json()['repository']['id']
        catalog = dict(branch='main', revision='a' * 40, cases=[])
        route = f'/automation/integrations/{rid}/catalog'
        assert client.post(route, json=catalog, headers={'X-Automation-Key': 'wrong-key'}).status_code == 401
        assert client.post(route, json=catalog, headers={'X-Automation-Key': key}).status_code == 200
    user.tab_permissions = '["mine"]'
    assert client.post(path, json={'rotate': True}).status_code == 403
