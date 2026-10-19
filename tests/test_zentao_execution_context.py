"""IPD 5.6 omits linked products from the v2 execution detail."""
from types import SimpleNamespace

import pytest

from app.services.zentao_client_service import ZentaoClient
from app.services.zentao_build_push_service import _push_one_execution
from app.services.zentao_version_diff_service import _resolve_target_exec_meta


@pytest.fixture
def ipd_client(monkeypatch):
    client = ZentaoClient('https://zentao.example/zentao', 'test-token', account='builder')
    state = SimpleNamespace(
        execution={'id': '1875', 'name': 's4050', 'project': '134', 'product': 'single', 'openedBy': 'builder'},
        page={'execution': {'id': 1875, 'project': 134, 'product': 'single'},
              'products': {'15': {'id': 15, 'name': 'Survey Master'}}},
        page_calls=[], writes=[], builds=[{'id': 5280, 'name': '4.0.5.0.261019_alpha.1(40500001)',
                                          'execution': 1875, 'product': 15}])

    def get(path, params=None):
        if path == 'executions/1875':
            return state.execution
        if path == 'products/15/systems':
            return {'systems': [{'id': 3}]}
        raise AssertionError('Unexpected API read: ' + path)

    def get_page(path, params=None):
        assert path == 'execution-view-1875.json'
        state.page_calls.append(path)
        return state.page

    def post(path, body):
        assert path == 'builds'
        assert body['product'] == 15 and body['system'] == 3 and body['executionID'] == 1875
        state.writes.append(body)
        row = {'id': 5299, 'name': body['name'], 'execution': 1875, 'product': 15}
        state.builds.append(row)
        return row

    monkeypatch.setattr(client, 'get', get)
    monkeypatch.setattr(client, 'get_page', get_page)
    monkeypatch.setattr(client, 'post', post)
    monkeypatch.setattr(client, 'list_execution_builds', lambda *a, **kw: list(state.builds))
    monkeypatch.setattr(client, 'update_build', lambda *a, **kw: pytest.fail('Existing real build must not be changed'))
    return client, state


def test_ipd_single_mode_resolves_products_from_verified_execution_page(ipd_client):
    client, state = ipd_client
    assert client.get_execution_context(1875) == {'product_ids': [15], 'project_id': 134}
    assert state.page_calls == ['execution-view-1875.json']


@pytest.mark.parametrize('products', [[{'id': '15'}, {'id': 15}], [15, '15'],
    {'15': {'id': 15}}, {'15': 'Survey Master'}, {'id': '15'}, '15'])
def test_existing_product_shapes_do_not_need_page_fallback(ipd_client, products):
    client, state = ipd_client
    state.execution['products'] = products
    assert client.get_execution_context(1875) == {'product_ids': [15], 'project_id': 134}
    assert state.page_calls == []


def test_legacy_numeric_product_and_nested_project_are_preserved(ipd_client):
    client, state = ipd_client
    state.execution.update(product='15', project={'id': '134'})
    assert client.get_execution_context(1875) == {'product_ids': [15], 'project_id': 134}
    assert state.page_calls == []


@pytest.mark.parametrize('field,value', [('id', 999), ('project', 999)])
def test_page_for_different_execution_or_project_cannot_supply_product(ipd_client, field, value):
    client, state = ipd_client
    state.page['execution'][field] = value
    assert client.get_execution_context(1875) == {'product_ids': [], 'project_id': 134}


def test_page_unavailable_preserves_project_without_guessing_product(ipd_client, monkeypatch):
    client, state = ipd_client
    monkeypatch.setattr(client, 'get_page', lambda *a, **kw: (_ for _ in ()).throw(TimeoutError()))
    assert client.get_execution_context(1875) == {'product_ids': [], 'project_id': 134}
    assert state.writes == []


@pytest.mark.parametrize('products', [[True, False, 0, -15, 'single', {'id': 'not-an-id'}], {}])
def test_invalid_product_ids_are_not_used_for_writes(ipd_client, products):
    client, state = ipd_client
    state.page['products'] = products
    assert client.get_execution_context(1875) == {'product_ids': [], 'project_id': 134}


def test_retry_only_creates_missing_placeholder_and_is_idempotent(ipd_client):
    client, state = ipd_client
    major = SimpleNamespace(zentao_execution_id=1875, version_no='V4.0.5.0')
    name = state.builds[0]['name']
    first = _push_one_execution(client, major, name)
    assert first.error is None
    assert first.created_build_id == 5280 and first.renamed_build_id is None
    assert first.new_placeholder_build_id == 5299
    assert len(state.writes) == 1 and 'xxxx' in state.writes[0]['name']
    second = _push_one_execution(client, major, name)
    assert second.error is None and second.created_build_id == 5280
    assert second.new_placeholder_build_id is None and len(state.writes) == 1


def test_reassign_build_uses_same_product_recovery(ipd_client):
    client, state = ipd_client
    assert _resolve_target_exec_meta(client, 1875) == ('s4050', 134, 15, 'builder')
    assert state.writes == []
