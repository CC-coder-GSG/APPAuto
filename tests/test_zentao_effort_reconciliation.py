"""IPD 5.6 effort writes: reconcile delayed results without replaying a POST."""
from copy import deepcopy

import httpx
import pytest

from app.services import zentao_web_session as web


LOGIN = web.ZentaoWebLogin('http://z', 'alice', 'test-password')
ROW = {'id': 9, 'account': 'alice', 'date': '2026-10-19', 'consumed': 0.1,
       'left': 0.9, 'work': 'verification'}


class Client:
    def __init__(self, *, error=None, payload=None):
        self.error = error
        self.payload = payload or {'result': 'success'}
        self.posts = []
        self.gets = []

    def post(self, url, **kwargs):
        self.posts.append((url, kwargs))
        if self.error:
            raise self.error
        return httpx.Response(200, json=self.payload)

    def get(self, url, **kwargs):
        self.gets.append((url, kwargs))
        return httpx.Response(404, json={'result': 'fail'})

    def close(self):
        pass


def setup(monkeypatch, client, snapshots):
    pending = iter(deepcopy(snapshots))
    last = snapshots[-1]

    def read(*args):
        value = next(pending, last)
        if isinstance(value, Exception):
            raise value
        return deepcopy(value)

    monkeypatch.setattr(web, '_open_web_session', lambda *_: client)
    monkeypatch.setattr(web, '_logout_quietly', lambda *a: None)
    monkeypatch.setattr(web, '_EFFORT_POLL_DELAYS', (0, 0, 0))
    monkeypatch.setattr(web, '_fetch_efforts', read)


def submit():
    return web.record_task_efforts_via_web(LOGIN, 42, [
        {k: v for k, v in ROW.items() if k not in ('id', 'account')},
    ])


def test_create_timeout_reconciles_delayed_commit_without_second_post(monkeypatch):
    client = Client(error=httpx.ReadTimeout('response lost'))
    setup(monkeypatch, client, [[], [], httpx.ReadTimeout('read failed'), [ROW]])
    assert submit() == [ROW]
    assert len(client.posts) == 1
    assert client.posts[0][1]['timeout'].read == 45


@pytest.mark.parametrize('observed', [
    [], [dict(ROW, account='bob')], [dict(ROW, account='')],
    [dict(ROW, date='2026-10-18')], [dict(ROW, consumed=0.2)],
    [dict(ROW, left=0.8)], [dict(ROW, work='different')],
    [ROW, dict(ROW, id=10)],
])
def test_create_does_not_accept_wrong_or_ambiguous_records(monkeypatch, observed):
    client = Client(error=httpx.ReadTimeout('response lost'))
    setup(monkeypatch, client, [[], observed])
    with pytest.raises(web.ZentaoWebSessionError) as error:
        submit()
    assert error.value.outcome_unknown
    assert len(client.posts) == 1


def test_existing_matching_record_does_not_prove_new_write(monkeypatch):
    client = Client()
    setup(monkeypatch, client, [[ROW], [ROW]])
    with pytest.raises(web.ZentaoWebSessionError) as error:
        submit()
    assert error.value.outcome_unknown
    assert len(client.posts) == 1


def test_partial_multiday_commit_is_unknown_and_never_resubmitted(monkeypatch):
    client = Client(error=httpx.ReadTimeout('partial commit'))
    setup(monkeypatch, client, [[], [ROW]])
    rows = [{k: v for k, v in ROW.items() if k not in ('id', 'account')},
            {'date': '2026-10-20', 'consumed': 0.1, 'left': 0.8, 'work': 'verification'}]
    with pytest.raises(web.ZentaoWebSessionError) as error:
        web.record_task_efforts_via_web(LOGIN, 42, rows)
    assert error.value.outcome_unknown
    assert len(client.posts) == 1


def test_explicit_rejection_is_not_unknown(monkeypatch):
    client = Client(payload={'result': 'fail', 'message': {'consumed': ['invalid']}})
    setup(monkeypatch, client, [[]])
    with pytest.raises(web.ZentaoWebSessionError) as error:
        submit()
    assert not error.value.outcome_unknown
    assert len(client.posts) == 1


def test_edit_timeout_reconciles_all_fields_on_the_same_id(monkeypatch):
    client = Client(error=httpx.ReadTimeout('response lost'))
    updated = dict(ROW, consumed=0.2, left=0.8, work='edited')
    setup(monkeypatch, client, [[ROW], [ROW], [updated]])
    assert web.edit_task_effort_via_web(LOGIN, 42, 9, date=ROW['date'],
                                      consumed=0.2, left=0.8, work='edited') == [updated]
    assert len(client.posts) == 1


def test_edit_does_not_accept_only_date_and_hours_when_work_is_wrong(monkeypatch):
    client = Client()
    setup(monkeypatch, client, [[ROW], [dict(ROW, consumed=0.2, left=0.8)]])
    with pytest.raises(web.ZentaoWebSessionError) as error:
        web.edit_task_effort_via_web(LOGIN, 42, 9, date=ROW['date'],
                                    consumed=0.2, left=0.8, work='edited')
    assert error.value.outcome_unknown
    assert len(client.posts) == 1


def test_edit_refuses_effort_outside_task_before_post(monkeypatch):
    client = Client()
    setup(monkeypatch, client, [[]])
    with pytest.raises(web.ZentaoWebSessionError):
        web.edit_task_effort_via_web(LOGIN, 42, 9, date=ROW['date'], consumed=0.1, left=0.9)
    assert client.posts == []


def test_delete_last_effort_uses_ipd56_confirmation_and_readback(monkeypatch):
    client = Client()
    setup(monkeypatch, client, [[ROW], []])
    assert web.delete_task_effort_via_web(LOGIN, 42, 9) == []
    assert [url for url, _ in client.gets] == ['http://z/effort-delete-9-yes-.html']


def test_delete_timeout_can_still_be_confirmed_by_absence(monkeypatch):
    client = Client()

    def lost(url, **kwargs):
        client.gets.append((url, kwargs))
        raise httpx.ReadTimeout('delete response lost')

    client.get = lost
    setup(monkeypatch, client, [[ROW], [ROW], []])
    assert web.delete_task_effort_via_web(LOGIN, 42, 9) == []
    assert len(client.gets) == 1


def test_delete_falls_back_to_legacy_route_only_for_the_same_id(monkeypatch):
    client = Client()
    setup(monkeypatch, client, [[ROW], [ROW], [ROW], [ROW], []])
    assert web.delete_task_effort_via_web(LOGIN, 42, 9) == []
    assert [url for url, _ in client.gets] == [
        'http://z/effort-delete-9-yes-.html', 'http://z/task-deleteWorkhour-9-yes.html',
    ]


def test_delete_read_failures_never_count_as_absence_or_trigger_next_write(monkeypatch):
    client = Client()
    setup(monkeypatch, client, [[ROW], httpx.ReadTimeout('read failed')])
    with pytest.raises(web.ZentaoWebSessionError) as error:
        web.delete_task_effort_via_web(LOGIN, 42, 9)
    assert error.value.outcome_unknown
    assert len(client.gets) == 1


@pytest.mark.parametrize('raw', [None, '', [{'id': 0}], [{'id': 9, 'consumed': 'invalid'}],
                               [dict(ROW), dict(ROW)]])
def test_malformed_effort_lists_cannot_prove_a_record_was_deleted(raw):
    class MalformedClient:
        def get(self, *args):
            return httpx.Response(200, json={'status': 'success', 'data': {'efforts': raw}})

    with pytest.raises(web.ZentaoWebSessionError):
        web._fetch_efforts(MalformedClient(), 'http://z', 42)
