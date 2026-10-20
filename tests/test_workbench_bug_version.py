from __future__ import annotations

import pytest
from sqlalchemy import text

from app.api.routes.zentao_hydrate import _writeback_bug_fields
from app.db.seed import ensure_bug_schema_compat
from app.models import BugSourceType, BugTracking, Version, VersionType
from app.services.overall_test_service import OverallTestService
from app.services.workbench_link_service import WorkbenchLinkService
from app.services.zentao_normalizer import normalize_bug


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
