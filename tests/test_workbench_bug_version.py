from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from sqlalchemy import text

from app.api.routes.zentao_hydrate import _fetch_bugs_concurrent, _writeback_bug_fields
from app.db.seed import ensure_bug_schema_compat
from app.models import BugSourceType, BugTracking, Version, VersionType
from app.services.overall_test_service import OverallTestService
from app.services.workbench_link_service import WorkbenchLinkService
from app.services.zentao_normalizer import normalize_bug
from app.services.zentao_client_service import ZentaoAPIError


def _bug(db):
    major = Version(version_no='V-BUG-VERSION', version_type=VersionType.MAJOR)
    db.add(major)
    db.flush()
    bug = BugTracking(major_version_id=major.id, source_type=BugSourceType.MANUAL,
                      bug_id='b#71040', zentao_bug_id='71040', zentao_live_status='active',
                      found_minor_version_id=None, zentao_affected_version='4.0.1')
    db.add(bug)
    db.commit()
    return bug


@pytest.mark.parametrize('raw, expected', [
    ('4.0.2', '4.0.2'), ({'id': 100, 'name': '4.0.2'}, '4.0.2'),
    ({'100': '4.0.2'}, '4.0.2'), (['4.0.2', {'name': '4.0.3'}], '4.0.2, 4.0.3'),
    (0, ''), ('0', ''), (None, ''), ('', ''),
    (5262, '5262'), ('5262', '5262'), ({'id': 5262, 'name': ''}, '5262'),
    ({'5262': ''}, '5262'), ([5262, {'name': '4.0.3'}], '5262, 4.0.3'),
])
def test_resolved_build_normalizes_remote_formats(raw, expected):
    assert normalize_bug({'id': 71040, 'resolvedBuild': raw})['resolved_build'] == expected


def test_hydration_caches_solution_version_and_status_for_next_render(db_session):
    bug = _bug(db_session)
    _writeback_bug_fields(db_session, {'71040': normalize_bug({
        'id': 71040, 'status': 'resolved', 'resolvedBuild': {'id': 100, 'name': '4.0.2'},
    })}, 'http://zentao.test')
    db_session.expire_all()
    brief = WorkbenchLinkService(db_session).serialize_bug_brief(bug, {})
    assert brief['zentao_live_status'] == 'resolved'
    assert brief['resolved_version_no'] == '4.0.2'
    assert brief['found_minor_version_no'] == '4.0.1'
    _writeback_bug_fields(db_session, {'71040': normalize_bug({
        'id': 71040, 'status': 'active', 'resolvedBuild': '',
    })}, 'http://zentao.test')
    brief = WorkbenchLinkService(db_session).serialize_bug_brief(bug, {})
    assert brief['zentao_live_status'] == 'active'
    assert brief['resolved_version_no'] == ''


def test_remote_solution_version_precedes_local_and_explicit_empty_does_not_use_stale_local(db_session):
    bug = _bug(db_session)
    bug.fixed_minor_version_id = 99
    service = WorkbenchLinkService(db_session)
    assert service.serialize_bug_brief(bug, {99: 'local-version'})['resolved_version_no'] == 'local-version'
    bug.zentao_resolved_build = 'remote-version'
    assert service.serialize_bug_brief(bug, {99: 'local-version'})['resolved_version_no'] == 'remote-version'
    bug.zentao_resolved_build = ''
    assert service.serialize_bug_brief(bug, {99: 'local-version'})['resolved_version_no'] == ''


def test_sync_preserves_missing_solution_field_and_clears_explicit_empty(db_session):
    bug = _bug(db_session)
    service = OverallTestService(db_session)

    def apply(raw):
        normalized = service._normalize_zentao_bug_summary(raw, 'http://zentao.test')
        return service._write_bug_fields_from_normalized(
            bug, normalized, execution_id=None, execution_name=None,
            sync_message='test', touched_if_existing=True,
        )

    apply({'id': 71040, 'status': 'resolved', 'resolvedBuild': {'name': '4.0.2'}})
    assert bug.zentao_resolved_build == '4.0.2'
    assert apply({'id': 71040, 'status': 'resolved', 'resolvedBuild': '4.0.3'})
    assert bug.zentao_resolved_build == '4.0.3'
    apply({'id': 71040, 'status': 'closed'})
    assert bug.zentao_resolved_build == '4.0.3'
    apply({'id': 71040, 'status': 'active', 'resolvedBuild': ''})
    assert bug.zentao_resolved_build == ''


def test_existing_database_adds_solution_cache_without_losing_bugs(db_session):
    bug_id = _bug(db_session).id
    db_session.execute(text('ALTER TABLE bug_tracking DROP COLUMN zentao_resolved_build'))
    db_session.commit()
    ensure_bug_schema_compat(db_session)
    ensure_bug_schema_compat(db_session)
    row = db_session.execute(text('SELECT id, zentao_resolved_build FROM bug_tracking WHERE id=:id'),
                             {'id': bug_id}).one()
    assert row.id == bug_id
    assert row.zentao_resolved_build is None


def test_cached_build_id_uses_local_version_name(db_session):
    bug = _bug(db_session)
    db_session.add(Version(version_no='4.0.6', version_type=VersionType.MINOR,
                           parent_id=bug.major_version_id, zentao_build_id=5262))
    bug.zentao_resolved_build = '5262'
    db_session.commit()
    assert WorkbenchLinkService(db_session).serialize_bug_brief(bug, {})['resolved_version_no'] == '4.0.6'
    bug.zentao_resolved_build = '99999'
    assert WorkbenchLinkService(db_session).serialize_bug_brief(bug, {})['resolved_version_no'] == '版本名称待获取'


def test_hydration_resolves_numeric_build_and_deduplicates_requests(db_session):
    bug = _bug(db_session)
    calls = []

    def build(build_id):
        calls.append(build_id)
        return {'id': build_id, 'name': '4.0.6'}

    client = SimpleNamespace(
        get_bug_with_fallback=lambda bug_id: {'id': bug_id, 'status': 'resolved', 'resolvedBuild': '5262'},
        get_build=build,
    )
    data, got_401, errors = asyncio.run(_fetch_bugs_concurrent(client, 'http://zentao.test', [71040, 71041]))
    assert not got_401 and not errors
    assert calls == [5262]
    assert data['71040']['resolved_build'] == data['71041']['resolved_build'] == '4.0.6'
    _writeback_bug_fields(db_session, data, 'http://zentao.test')
    assert bug.zentao_resolved_build == '4.0.6'


def test_hydration_prefers_local_name_without_remote_build_request():
    def unexpected(build_id):
        pytest.fail('Local version mapping must avoid a remote build request')

    client = SimpleNamespace(
        get_bug_with_fallback=lambda bug_id: {'id': bug_id, 'resolvedBuild': {'id': 5262}},
        get_build=unexpected,
    )
    data, _, errors = asyncio.run(_fetch_bugs_concurrent(client, 'http://zentao.test', [71040], {'5262': '4.0.6'}))
    assert not errors and data['71040']['resolved_build'] == '4.0.6'


@pytest.mark.parametrize('status', [401, 403, 404, 500])
def test_build_lookup_failure_preserves_bug_status_and_can_retry(status):
    def failed(build_id):
        raise ZentaoAPIError(status, 'build lookup failed')

    client = SimpleNamespace(
        get_bug_with_fallback=lambda bug_id: {'id': bug_id, 'status': 'closed', 'resolvedBuild': 5262},
        get_build=failed,
    )
    data, got_401, errors = asyncio.run(_fetch_bugs_concurrent(client, 'http://zentao.test', [71040]))
    assert got_401 == (status == 401)
    assert not errors and data['71040']['status'] == 'closed'
    assert data['71040']['resolved_build'] == '5262'
    client.get_build = lambda build_id: {'id': build_id, 'name': '4.0.6'}
    data, _, errors = asyncio.run(_fetch_bugs_concurrent(client, 'http://zentao.test', [71040]))
    assert not errors and data['71040']['resolved_build'] == '4.0.6'


def test_hydration_rejects_wrong_build_and_resolves_multiple_build_references():
    client = SimpleNamespace(
        get_bug_with_fallback=lambda bug_id: {'id': bug_id, 'resolvedBuild': [5262, {'name': '4.0.7'}, 5263]},
        get_build=lambda build_id: {'id': build_id if build_id == 5262 else 99, 'name': '4.0.6'},
    )
    data, _, errors = asyncio.run(_fetch_bugs_concurrent(client, 'http://zentao.test', [71040]))
    assert not errors and data['71040']['resolved_build'] == '4.0.6, 4.0.7, 5263'
