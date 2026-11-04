from datetime import datetime, timedelta

import pytest
from sqlalchemy import event, text, update

from app.models import BugSourceType, BugTracking, Requirement, RequirementRetestRecord, User, Version, VersionType, ZentaoTestCaseMirror
from app.services.bug_service import BugService
from app.services.retest_aggregate_service import AGGREGATE_FIELDS, RetestAggregateService
from app.services.retest_service import RetestService
from app.services.workbench_service import WorkbenchService
from app.services.zentao_testcase_service import ZentaoTestCaseService


@pytest.fixture
def env(db_session):
    db = db_session
    owner = User(username='aggregate_owner', password_hash='x', zentao_account='owner')
    tester = User(username='aggregate_tester', password_hash='x', zentao_account='tester')
    majors = [Version(version_no=f'aggregate-{i}', version_type=VersionType.MAJOR) for i in range(2)]
    db.add_all([owner, tester, *majors])
    db.flush()
    cutoff = datetime(2026, 8, 1)
    reqs = [Requirement(zentao_req_id=f'r#{i}', title=f'Aggregate {i}', major_version_id=majors[i].id,
                        owner_id=owner.id, zentao_story_id=123, test_completed=True, test_completed_at=cutoff)
            for i in range(2)]
    db.add_all(reqs)
    db.commit()
    return dict(db=db, owner=owner, tester=tester, majors=majors, reqs=reqs, cutoff=cutoff)


def add_bug(e, **values):
    data = dict(major_version_id=e['majors'][0].id, source_type=BugSourceType.MANUAL,
                bug_id='b#aggregate', zentao_story_id=123, zentao_opened_by_account='tester',
                zentao_opened_at=e['cutoff'] + timedelta(days=1))
    data.update(values)
    bug = BugTracking(**data)
    e['db'].add(bug)
    return bug


def conclusions(e):
    return [RetestService(e['db']).compute_conclusion(r) for r in e['reqs']]


def assert_consistent(e, expected):
    assert conclusions(e) == expected
    for req, conclusion in zip(e['reqs'], expected):
        assert req.retest_completed is (conclusion != 'pending')
        assert req.retest_passed is {'pending': None, 'passed': True, 'failed': False}[conclusion]
        rows = WorkbenchService(e['db']).get_retest_workbench(current_user=e['tester'], major_version_id=req.major_version_id)
        if req.test_completed:
            assert rows[0]['retest_conclusion'] == conclusion
        else:
            assert rows == []


def test_bug_create_logical_and_physical_delete_update_all_versions(env):
    e = env
    bug = add_bug(e)
    e['db'].commit()
    assert_consistent(e, ['failed', 'failed'])
    bug.zentao_deleted = True
    e['db'].commit()
    assert_consistent(e, ['pending', 'pending'])
    bug.zentao_deleted = False
    e['db'].commit()
    assert_consistent(e, ['failed', 'failed'])
    BugService(e['db']).delete_bug(bug.id)
    assert_consistent(e, ['pending', 'pending'])


def test_expired_bug_story_reassignment_refreshes_old_and_new_targets(env):
    e = env
    e['reqs'][1].zentao_story_id = 456
    bug = add_bug(e)
    e['db'].commit()
    assert_consistent(e, ['failed', 'pending'])
    e['db'].expire(bug, ['zentao_story_id'])
    bug.zentao_story_id = 456
    e['db'].commit()
    assert_consistent(e, ['pending', 'failed'])


def test_case_sync_reassignment_and_deletion_refresh_previous_story(env):
    e = env
    e['reqs'][1].zentao_story_id = 456
    sync = ZentaoTestCaseService(e['db'])
    case, _, _ = sync._upsert_case(dict(zentao_case_id='u#500', zentao_case_numeric_id=500,
                                      zentao_story_id=123, deleted=False), sync_source='test')
    add_bug(e, zentao_story_id=None, zentao_linked_case_id='500')
    e['db'].commit()
    assert_consistent(e, ['failed', 'pending'])
    sync._upsert_case(dict(zentao_case_id='u#500', zentao_case_numeric_id=500,
                          zentao_story_id=456, deleted=False), sync_source='test')
    e['db'].commit()
    assert_consistent(e, ['pending', 'failed'])
    case.deleted = True
    e['db'].commit()
    assert_consistent(e, ['pending', 'pending'])


def test_owner_account_and_completion_cutoff_changes_recompute(env):
    e = env
    add_bug(e)
    e['db'].commit()
    e['owner'].zentao_account = 'tester'
    e['db'].commit()
    assert_consistent(e, ['pending', 'pending'])
    e['owner'].zentao_account = 'owner'
    e['reqs'][1].test_completed_at = e['cutoff'] + timedelta(days=2)
    e['db'].commit()
    assert_consistent(e, ['failed', 'pending'])
    e['reqs'][0].test_completed_at = None
    e['db'].commit()
    assert_consistent(e, ['pending', 'pending'])


def test_activations_are_scoped_and_dismiss_updates_all_evidence_targets(env):
    e = env
    # No opened date: activation alone belongs only to the selected requirement.
    bug = add_bug(e, zentao_opened_at=None)
    e['db'].commit()
    service = RetestService(e['db'])
    service.mark_bug_activated(bug.id, e['reqs'][0].id, e['tester'])
    assert_consistent(e, ['failed', 'pending'])
    service.mark_bug_activated(bug.id, e['reqs'][1].id, e['tester'])
    assert_consistent(e, ['pending', 'failed'])
    bug.zentao_opened_at = e['cutoff'] + timedelta(days=1)
    e['db'].commit()
    assert_consistent(e, ['failed', 'failed'])
    service.toggle_bug_dismissed(bug.id, e['reqs'][1].id, True, e['tester'])
    assert_consistent(e, ['passed', 'passed'])
    bug.zentao_deleted = True
    e['db'].commit()
    assert_consistent(e, ['pending', 'pending'])


def test_hidden_activation_is_visible_at_its_actual_requirement(env):
    e = env
    e['reqs'][1].zentao_story_id = 456
    bug = add_bug(e, zentao_opened_at=None, requirement_id=e['reqs'][0].id)
    e['db'].commit()
    RetestService(e['db']).mark_bug_activated(bug.id, e['reqs'][1].id, e['tester'])
    assert_consistent(e, ['pending', 'failed'])
    row = WorkbenchService(e['db']).get_retest_workbench(current_user=e['tester'], major_version_id=e['reqs'][1].major_version_id)[0]
    assert row['retest_evidence_bugs'][0]['id'] == bug.id


def test_submit_withdraw_and_reopen_preserve_records_without_stale_aggregates(env):
    e = env
    req = e['reqs'][0]
    service = RetestService(e['db'])
    service.submit_retest(req.id, retest_completed=True, retest_passed=True,
                          retest_minor_version_id=None, current_user=e['tester'])
    assert_consistent(e, ['passed', 'pending'])
    req.test_completed = False
    req.test_completed_at = None
    e['db'].commit()
    assert_consistent(e, ['pending', 'pending'])
    assert e['db'].query(RequirementRetestRecord).filter_by(requirement_id=req.id).count() == 1
    req.test_completed = True
    req.test_completed_at = e['cutoff']
    e['db'].commit()
    assert_consistent(e, ['passed', 'pending'])
    service.submit_retest(req.id, retest_completed=False, retest_passed=None,
                          retest_minor_version_id=None, current_user=e['tester'])
    assert_consistent(e, ['pending', 'pending'])


def test_read_only_get_never_repairs_stale_data_or_commits(env):
    e = env
    add_bug(e)
    e['db'].commit()
    # Simulate a historical/out-of-band write, bypassing application hooks.
    e['db'].connection().execute(text('UPDATE requirements SET retest_completed=FALSE,retest_passed=NULL'))
    e['db'].commit()
    e['db'].expire_all()
    # Establish the fixture's PostgreSQL savepoint before counting read SQL.
    e['db'].connection()
    statements = []
    def record(conn, cursor, statement, params, context, many):
        statements.append(statement)
    event.listen(e['db'].bind, 'after_cursor_execute', record)
    try:
        before = [tuple(getattr(req, f) for f in AGGREGATE_FIELDS) for req in e['reqs']]
        for _ in range(2):
            WorkbenchService(e['db']).get_retest_workbench(current_user=e['tester'], major_version_id=e['majors'][0].id)
        assert before == [tuple(getattr(req, f) for f in AGGREGATE_FIELDS) for req in e['reqs']]
        assert not e['db'].dirty
        assert all(q.lstrip().split()[0] == 'SELECT' for q in statements)
    finally:
        event.remove(e['db'].bind, 'after_cursor_execute', record)
    service = RetestAggregateService(e['db'])
    assert service.reconcile_all(commit_batches=True)['changed'] == 2
    stable_times = [r.retested_at for r in e['reqs']]
    assert service.reconcile_all(commit_batches=True)['changed'] == 0
    assert stable_times == [r.retested_at for r in e['reqs']]
    assert_consistent(e, ['failed', 'failed'])


def test_bulk_update_and_savepoint_rollback_do_not_miss_outer_writes(env):
    e = env
    add_bug(e)
    e['db'].commit()
    e['db'].execute(update(BugTracking).values(zentao_deleted=True))
    e['db'].commit()
    assert_consistent(e, ['pending', 'pending'])
    e['db'].execute(update(BugTracking).values(zentao_deleted=False))
    e['db'].commit()
    assert_consistent(e, ['failed', 'failed'])
    e['reqs'][0].test_completed_at = e['cutoff'] + timedelta(days=2)
    try:
        with e['db'].begin_nested():
            e['reqs'][1].test_completed_at = None
            e['db'].flush()
            raise ValueError('rollback inner work')
    except ValueError:
        pass
    e['db'].commit()
    assert_consistent(e, ['pending', 'failed'])


def test_reconciliation_failure_rolls_back_business_write_and_retry_works(env, monkeypatch):
    e = env
    original = RetestAggregateService.reconcile_scope
    def fail(*args, **kwargs):
        raise RuntimeError('injected aggregate failure')
    monkeypatch.setattr(RetestAggregateService, 'reconcile_scope', fail)
    add_bug(e)
    with pytest.raises(RuntimeError, match='injected aggregate failure'):
        e['db'].commit()
    e['db'].rollback()
    assert e['db'].query(BugTracking).count() == 0
    assert_consistent(e, ['pending', 'pending'])
    monkeypatch.setattr(RetestAggregateService, 'reconcile_scope', original)
    add_bug(e)
    e['db'].commit()
    assert_consistent(e, ['failed', 'failed'])
