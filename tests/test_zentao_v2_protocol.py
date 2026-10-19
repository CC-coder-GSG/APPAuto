"""Offline wire-contract checks based on the official v2 OpenAPI catalogue."""
from datetime import timedelta
from types import SimpleNamespace

import httpx
import pytest

from app.services.zentao_client_service import ZentaoAPIError, ZentaoClient
from app.services.zentao_v2_protocol import decode_v2, normalize_base_url, relative_path


def response(payload, status=200):
    return httpx.Response(status, json=payload)


@pytest.mark.parametrize('path', [
    'https://other.invalid/bugs', '//other.invalid/bugs', '../api.php/v1/bugs',
    'api.php/v1/bugs', '/api.php/v2/bugs', '%252e%252e/bugs', 'bugs?token=x',
    'bugs#fragment', r'bugs\..\api.php\v1',
])
def test_transport_rejects_paths_that_escape_v2(path, monkeypatch):
    monkeypatch.setattr(httpx, 'get', lambda *a, **k: pytest.fail('must reject before transport'))
    with pytest.raises(ZentaoAPIError):
        ZentaoClient('https://z.invalid/zentao', 'secret').get(path)


def test_base_url_accepts_v2_suffix_but_never_v1():
    assert normalize_base_url('https://z.invalid/zentao/api.php/v2/') == 'https://z.invalid/zentao'
    for url in ['https://z.invalid/api.php/v1', 'https://z.invalid/%2561pi.php/v1',
                'https://user:pass@z.invalid', 'https://z.invalid?token=secret']:
        with pytest.raises(ZentaoAPIError):
            normalize_base_url(url)


def test_wire_pagination_all_statuses_and_resource_envelope(monkeypatch):
    calls = []
    def get(url, **kwargs):
        calls.append((url, kwargs))
        if url.endswith('/bugs/8'):
            return response({'status': 'success', 'bug': {'id': 8, 'status': 'closed'}})
        return response({'status': 'success', 'bugs': []})
    monkeypatch.setattr(httpx, 'get', get)
    cli = ZentaoClient('https://z.invalid/zentao', 'secret')
    cli.get('products/2/bugs', {'limit': 100, 'page': 2, 'status': 'all'})
    assert calls[0][0] == 'https://z.invalid/zentao/api.php/v2/products/2/bugs'
    assert calls[0][1]['params'] == {'recPerPage': 100, 'pageID': 2, 'browseType': 'all'}
    assert calls[0][1]['follow_redirects'] is False
    assert cli.get_bug(8) == {'id': 8, 'status': 'closed'}


@pytest.mark.parametrize('payload', [None, [], {}, {'message': 'success'}, {'status': 'success', 'errors': {'title': 'required'}}])
def test_ambiguous_write_is_not_success(payload):
    with pytest.raises(ZentaoAPIError) as error:
        decode_v2(response(payload), write=True)
    assert error.value.outcome_unknown


@pytest.mark.parametrize('status', [401, 403, 404, 422, 500, 302])
def test_http_status_is_not_hidden_by_success_body(status):
    with pytest.raises(ZentaoAPIError) as error:
        decode_v2(response({'status': 'success'}, status), write=True)
    assert error.value.status_code == status
    assert error.value.outcome_unknown == (status >= 500)


@pytest.mark.parametrize('status', ['fail', 'failed', 'error'])
def test_explicit_rejection_is_distinct_from_unknown_write(status):
    with pytest.raises(ZentaoAPIError) as error:
        decode_v2(response({'status': status, 'code': 403}), write=True)
    assert error.value.status_code == 403
    assert not error.value.outcome_unknown


def test_ipd56_required_dates_are_an_explicit_validation_rejection():
    with pytest.raises(ZentaoAPIError) as error:
        decode_v2(response({'result': 'fail', 'message': {
            'estStarted': ['required'], 'deadline': ['required'],
            'password': ['must never be displayed'],
        }}), write=True)
    assert error.value.status_code == 422
    assert not error.value.outcome_unknown
    assert 'estStarted' in error.value.message
    assert 'deadline' in error.value.message
    assert 'password' not in error.value.message
    assert 'must never be displayed' not in error.value.message


def test_lost_create_response_never_retries_or_uses_same_title(monkeypatch):
    calls = []
    def post(url, **kwargs):
        calls.append(url)
        raise httpx.ReadTimeout('lost response')
    monkeypatch.setattr(httpx, 'post', post)
    cli = ZentaoClient('https://z.invalid', 'secret')
    monkeypatch.setattr(cli, 'get', lambda *a, **k: pytest.fail('no title-based recovery'))
    with pytest.raises(ZentaoAPIError) as error:
        cli.create_bug(2, {'title': 'existing duplicate title'})
    assert error.value.outcome_unknown
    assert calls == ['https://z.invalid/api.php/v2/bugs']


@pytest.mark.parametrize('method,args,kwargs,expected,body', [
    ('create_bug', (2, {'title': 'B', 'openedBuild': ['3']}), {}, ('post', 'bugs'), {'productID': 2, 'title': 'B', 'openedBuild': ['3']}),
    ('create_execution_task', (9,), {'name': 'T'}, ('post', 'tasks'), {'executionID': 9, 'name': 'T', 'type': 'test', 'pri': 3}),
    ('close_bug', (8,), {}, ('put', 'bugs/8/close'), {'comment': ''}),
    ('active_bug', (8,), {}, ('put', 'bugs/8/activate'), {'comment': ''}),
    ('start_task', (8,), {'real_started': '2026-10-15 10:00:00'}, ('put', 'tasks/8/start'), {'realStarted': '2026-10-15 10:00:00'}),
    ('close_task', (8,), {}, ('put', 'tasks/8/close'), {'comment': ''}),
    ('reassign_task', (8, 'alice'), {}, ('put', 'tasks/8'), {'assignedTo': 'alice'}),
    ('delete_bug', (8,), {}, ('delete', 'bugs/8'), None),
    ('delete_build', (8,), {}, ('delete', 'builds/8'), None),
])
def test_mutations_follow_v2_methods_and_bodies(method, args, kwargs, expected, body, monkeypatch):
    captured = []
    def send(url, **options):
        captured.append((url, options))
        return response({'status': 'success', 'id': 18})
    monkeypatch.setattr(httpx, expected[0], send)
    getattr(ZentaoClient('https://z.invalid', 'secret'), method)(*args, **kwargs)
    assert len(captured) == 1
    assert captured[0][0] == 'https://z.invalid/api.php/v2/' + expected[1]
    assert captured[0][1].get('json') == body


def test_task_detail_gap_uses_retained_page_not_v1(monkeypatch):
    cli = ZentaoClient('https://z.invalid', 'secret')
    monkeypatch.setattr(httpx, 'get', lambda *a, **k: response({'status': 'success'}))
    pages = []
    def page(path):
        pages.append(path)
        return {'task': {'id': 8, 'name': 'retained'}}
    monkeypatch.setattr(cli, 'get_page', page)
    assert cli.get_task(8)['name'] == 'retained'
    assert pages == ['task-view-8.json']


def test_paginated_lists_include_closed_and_do_not_drop_later_pages(monkeypatch):
    pages = []
    def get(url, **kwargs):
        params = kwargs['params']
        pages.append(params)
        ids = [1, 2] if params['pageID'] == 1 else [3]
        return response({'status': 'success', 'executions': [{'id': i, 'status': 'closed'} for i in ids]})
    monkeypatch.setattr(httpx, 'get', get)
    assert [x['id'] for x in ZentaoClient('https://z.invalid', 'secret').list_project_executions(8, limit=2)] == [1, 2, 3]
    assert all(p['status'] == 'all' for p in pages)
    assert all(p['browseType'] == 'all' for p in pages)
    assert pages[1]['pageID'] == 2


def test_ignored_pagination_fails_instead_of_silently_claiming_complete(monkeypatch):
    monkeypatch.setattr(httpx, 'get', lambda *a, **k: response({'status': 'success', 'tasks': [{'id': 1}], 'total': 2}))
    with pytest.raises(ZentaoAPIError, match='同一页'):
        ZentaoClient('https://z.invalid', 'secret').list_execution_tasks(8, limit=1)


def test_ipd56_task_lists_include_closed_tasks_across_pages(monkeypatch):
    calls = []

    def get(url, **kwargs):
        params = kwargs['params']
        calls.append(params)
        # Observed on IPD 5.6: status=all alone still excludes closed tasks
        # and reports a smaller total, making the missing data look complete.
        tasks = [{'id': 1, 'status': 'done'}]
        if params.get('browseType') == 'all':
            tasks += [{'id': 2, 'status': 'closed'}, {'id': 3, 'status': 'closed'}]
        offset = (params['pageID'] - 1) * params['recPerPage']
        return response({'status': 'success', 'tasks': tasks[offset:offset + params['recPerPage']],
                         'pager': {'recTotal': len(tasks)}})

    monkeypatch.setattr(httpx, 'get', get)
    tasks = ZentaoClient('https://z.invalid', 'secret').list_execution_tasks(8, limit=2)
    assert [task['id'] for task in tasks] == [1, 2, 3]
    assert [params['pageID'] for params in calls] == [1, 2]
    assert all(params['status'] == params['browseType'] == 'all' for params in calls)


def test_build_system_ambiguity_blocks_create(monkeypatch):
    monkeypatch.setattr(httpx, 'get', lambda *a, **k: response({'status': 'success', 'systems': [{'id': 1}, {'id': 2}]}))
    monkeypatch.setattr(httpx, 'post', lambda *a, **k: pytest.fail('ambiguous product must not be written'))
    with pytest.raises(ZentaoAPIError, match='system'):
        ZentaoClient('https://z.invalid', 'secret', account='alice').create_execution_build(8, 'B', product_id=2)


def test_build_system_mapping_is_checked_against_accessible_product_systems(monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, 'zentao_build_system_mapping_json', '{"2": 20}')
    monkeypatch.setattr(httpx, 'get', lambda *a, **k: response({'status': 'success', 'systems': [{'id': 10}, {'id': 20}]}))
    cli = ZentaoClient('https://z.invalid', 'secret', account='alice')
    assert cli._build_system(2) == 20
    monkeypatch.setattr(settings, 'zentao_build_system_mapping_json', '{"2": 99}')
    with pytest.raises(ZentaoAPIError, match='不属于'):
        cli._build_system(2)


def test_build_edit_preserves_required_fields(monkeypatch):
    cli = ZentaoClient('https://z.invalid', 'secret')
    snapshot = {'id': 8, 'execution': 9, 'product': 2, 'system': 3, 'builder': {'account': 'alice'},
                'date': '2026-10-15', 'name': 'old', 'desc': 'keep'}
    monkeypatch.setattr(cli, 'get_build', lambda *_: snapshot)
    captured = []
    monkeypatch.setattr(cli, 'put', lambda path, body: captured.append((path, body)) or {'status': 'success'})
    cli.update_build(8, name='new')
    assert captured == [('builds/8', {k: v for k, v in {**snapshot, 'name': 'new', 'builder': 'alice'}.items() if k != 'id'})]


def test_upload_uses_one_documented_multipart_field_and_returned_url(monkeypatch):
    calls = []
    def post(url, **kwargs):
        calls.append((url, kwargs))
        return response({'status': 'success', 'id': 8, 'url': '/zentao/file-read-8.png'})
    monkeypatch.setattr(httpx, 'post', post)
    result = ZentaoClient('https://z.invalid/zentao', 'secret').upload_file(b'png', 'image', 'image/png')
    assert result['url'] == '/zentao/file-read-8.png'
    assert calls[0][0] == 'https://z.invalid/zentao/api.php/v2/files'
    assert calls[0][1]['files'] == {'file': ('image.png', b'png', 'image/png')}
    assert calls[0][1]['data'] == {'objectType': 'bug', 'objectID': '0'}


def test_v2_login_discards_old_cached_token_without_schema_change(monkeypatch):
    from app.services import zentao_auth_service as auth
    from app.utils.time_utils import utc_now_naive
    binding = SimpleNamespace(user_id=1, base_url='https://z.invalid/zentao', zentao_account='alice',
                              zentao_password_ciphertext='encrypted', token_value='v1-token',
                              token_expires_at=utc_now_naive() + timedelta(hours=4), last_refresh_status='ok')
    assert not auth._is_token_valid(binding)
    monkeypatch.setattr(auth, 'decrypt_password', lambda _: 'password')
    calls = []
    def post(url, **kwargs):
        calls.append((url, kwargs))
        return response({'status': 'success', 'token': 'new-v2-token'})
    monkeypatch.setattr(httpx, 'post', post)
    token, expiry = auth._fetch_new_token(binding)
    assert token == 'new-v2-token'
    assert calls[0][0] == 'https://z.invalid/zentao/api.php/v2/users/login'
    binding.token_value, binding.token_expires_at, binding.last_refresh_status = token, expiry, 'ok_v2'
    assert auth._is_token_valid(binding)


def test_web_credentials_are_redacted_and_redirects_stay_inside_site():
    from app.services.zentao_web_session import ZentaoWebLogin, ZentaoWebSessionError, _same_site_request
    login = ZentaoWebLogin('https://z.invalid/zentao', 'alice', 'never-display')
    assert 'never-display' not in repr(login)
    check = _same_site_request(login.base_url)
    check(httpx.Request('GET', 'https://z.invalid/zentao/bug-view-8.json'))
    for url in ['https://other.invalid/zentao/bug-view-8.json', 'https://z.invalid/admin/',
                'https://z.invalid/zentao/api.php/v1/bugs']:
        with pytest.raises(ZentaoWebSessionError):
            check(httpx.Request('GET', url))


def test_page_write_lost_response_is_not_replayed(monkeypatch):
    from app.services import zentao_web_session as web
    calls = []
    class Client:
        def post(self, url, **kwargs):
            calls.append(url)
            raise httpx.ReadTimeout('lost')
        def get(self, *a, **k):
            return response({})
        def close(self):
            pass
    monkeypatch.setattr(web, '_open_web_session', lambda *_: Client())
    with pytest.raises(ZentaoAPIError) as error:
        web.request_page_via_web(web.ZentaoWebLogin('https://z.invalid', 'alice', 'password'),
                                 'bug-assignTo-8.json', data={'assignedTo': 'bob'})
    assert error.value.outcome_unknown
    assert calls == ['https://z.invalid/bug-assignTo-8.json']


def test_file_download_keeps_resource_path_without_using_rest_v1(monkeypatch):
    calls = []
    def get(url, **kwargs):
        calls.append(url)
        return httpx.Response(200, content=b'file-bytes', headers={'content-type': 'image/png'})
    monkeypatch.setattr(httpx, 'get', get)
    result = ZentaoClient('https://z.invalid/zentao', 'secret').get_file(8)
    assert result.content == b'file-bytes'
    assert calls == ['https://z.invalid/zentao/file-read-8.html']


def test_undocumented_parent_update_retains_form_and_preserves_fields(monkeypatch):
    cli = ZentaoClient('https://z.invalid', 'secret')
    original = {'id': 8, 'execution': {'id': 9}, 'name': 'keep', 'status': 'doing', 'assignedTo': {'account': 'alice'},
                'parent': 0, 'left': 4, 'consumed': 2, 'estimate': 6, 'story': {'id': 9}}
    monkeypatch.setattr(cli, 'get_task', lambda *a: original)
    monkeypatch.setattr(cli, 'put', lambda *a: pytest.fail('parent is not a documented v2 edit field'))
    captured = []
    def page(path, fields):
        captured.append((path, fields))
        return {'result': 'success'}
    monkeypatch.setattr(cli, 'post_page', page)
    cli.update_task(8, {'parent': 10})
    assert captured == [('task-edit-8.json', {'execution': 9, 'name': 'keep', 'status': 'doing', 'assignedTo': 'alice',
                                            'parent': 10, 'left': 4, 'consumed': 2, 'estimate': 6, 'story': 9})]


def test_finish_uses_v2_put_and_preserves_real_start_time(monkeypatch):
    cli = ZentaoClient('https://z.invalid', 'secret')
    monkeypatch.setattr(cli, 'get_task', lambda *a: {'id': 8, 'realStarted': '2026-10-14 09:00:00'})
    calls = []
    monkeypatch.setattr(httpx, 'put', lambda url, **kw: calls.append((url, kw['json'])) or response({'status': 'success'}))
    cli.finish_task(8, current_consumed=1.5, finished_date='2026-10-15 10:00:00')
    assert calls == [('https://z.invalid/api.php/v2/tasks/8/finish', {
        'realStarted': '2026-10-14 09:00:00', 'finishedDate': '2026-10-15 10:00:00', 'currentConsumed': 1.5})]


def test_bug_assignment_keeps_page_and_requires_readback(monkeypatch):
    cli = ZentaoClient('https://z.invalid', 'secret')
    calls = []
    monkeypatch.setattr(cli, 'post_page', lambda *a: calls.append(a) or {'result': 'success'})
    monkeypatch.setattr(cli, 'get_bug', lambda *a: {'id': 8, 'assignedTo': {'account': 'bob'}})
    cli.assign_bug(8, 'bob', 'comment')
    assert calls == [('bug-assignTo-8.json', {'assignedTo': 'bob', 'comment': 'comment'})]
    monkeypatch.setattr(cli, 'get_bug', lambda *a: {'id': 8, 'assignedTo': {'account': 'alice'}})
    with pytest.raises(ZentaoAPIError) as error:
        cli.assign_bug(8, 'bob')
    assert error.value.outcome_unknown


def test_v2_bug_detail_is_retained_when_optional_page_history_is_unavailable(monkeypatch):
    cli = ZentaoClient('https://z.invalid', 'secret')
    monkeypatch.setattr(cli, 'get_bug', lambda *a: {'id': 8, 'title': 'v2'})
    monkeypatch.setattr(cli, 'get_page', lambda *a: (_ for _ in ()).throw(ZentaoAPIError(403, 'no page access')))
    assert cli.get_bug_with_fallback(8) == {'id': 8, 'title': 'v2'}


def test_binding_factory_preserves_same_identity_for_rest_and_web(monkeypatch):
    monkeypatch.setattr('app.services.zentao_auth_service.decrypt_password', lambda _: 'web-password')
    binding = SimpleNamespace(base_url='https://z.invalid/zentao', zentao_account='alice',
                              zentao_password_ciphertext='cipher')
    cli = ZentaoClient.from_binding(binding, 'v2-token')
    assert cli.get('user')['profile']['account'] == 'alice'
    assert cli.web_login.account == 'alice'
    assert cli.web_login.password == 'web-password'
    assert cli._api_base == 'https://z.invalid/zentao/api.php/v2'


@pytest.mark.parametrize('kind', ['testcase', 'bug'])
def test_creation_preserves_id_when_supplemental_form_fails(monkeypatch, kind):
    cli = ZentaoClient('https://z.invalid', 'secret')
    calls = []
    monkeypatch.setattr(cli, 'post', lambda path, body: calls.append((path, body)) or {'status': 'success', 'id': 8})
    monkeypatch.setattr(cli, 'get_testcase', lambda *a: {'id': 8, 'keywords': ''})
    monkeypatch.setattr(cli, 'get_bug', lambda *a: {'id': 8, 'assignedTo': 'alice', 'module': 0})
    monkeypatch.setattr(cli, 'post_page', lambda *a: (_ for _ in ()).throw(ZentaoAPIError(401, 'web expired')))
    if kind == 'testcase':
        result = cli.create_testcase(2, {'title': 'case', 'keywords': 'keep', 'stage': 'feature'})
        assert calls == [('testcases', {'title': 'case', 'productID': 2})]
    else:
        result = cli.create_bug(2, {'title': 'B', 'openedBuild': ['trunk'], 'module': 9, 'assignedTo': 'bob'})
        assert calls == [('bugs', {'title': 'B', 'openedBuild': ['trunk'], 'productID': 2})]
    assert result['id'] == 8
    assert 'compat_warning' in result


def test_testcase_keywords_and_stage_use_retained_form_after_v2_creation(monkeypatch):
    cli = ZentaoClient('https://z.invalid', 'secret')
    monkeypatch.setattr(cli, 'post', lambda *a: {'status': 'success', 'id': 8})
    state = {'id': 8, 'title': 'case', 'keywords': '', 'stage': ''}
    monkeypatch.setattr(cli, 'get_testcase', lambda *a: dict(state))
    forms = []
    def form(path, fields):
        forms.append((path, fields))
        state.update(fields)
        return {'result': 'success'}
    monkeypatch.setattr(cli, 'post_page', form)
    result = cli.create_testcase(2, {'title': 'case', 'keywords': 'keep', 'stage': 'feature',
                                    'steps': [{'desc': 'do', 'expect': 'ok'}]})
    assert result['id'] == 8 and 'compat_warning' not in result
    path, fields = forms[0]
    assert path == 'testcase-edit-8.json'
    assert fields['stage'] == ['feature'] and fields['keywords'] == 'keep'
    assert fields['steps'] == ['do'] and fields['expects'] == ['ok']


@pytest.mark.parametrize('denied', [False, True])
def test_page_read_keeps_token_path_and_uses_cookie_only_on_gap(monkeypatch, denied):
    from app.services.zentao_web_session import ZentaoWebLogin
    cli = ZentaoClient('https://z.invalid', 'secret', web_login=ZentaoWebLogin('https://z.invalid', 'alice', 'p'))
    monkeypatch.setattr(httpx, 'get', lambda *a, **k: response({'result': 'fail'} if denied else
                                                            {'status': 'success', 'data': {'users': {'alice': 'A'}}}))
    cookie_calls = []
    def page(*args, **kwargs):
        cookie_calls.append(True)
        return {'users': {'bob': 'B'}}
    monkeypatch.setattr('app.services.zentao_web_session.request_page_via_web', page)
    assert cli.get_page('task-create-8.json') == {'users': {'bob': 'B'} if denied else {'alice': 'A'}}
    assert cookie_calls == ([True] if denied else [])
