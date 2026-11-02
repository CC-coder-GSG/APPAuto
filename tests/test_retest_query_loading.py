from datetime import datetime, timedelta

import pytest
from sqlalchemy import event, inspect, text

from app.db.workbench_indexes import ensure_workbench_indexes
from app.models import (BugSourceType, BugTracking, Requirement, RequirementRetestRecord,
                        TestCase as Case, User, Version, VersionType, ZentaoTestCaseMirror)
from app.services.retest_service import RetestService
from app.services.workbench_link_service import WorkbenchLinkService
from app.services.workbench_service import WorkbenchService


def seed(db, count):
    owner = User(username='query_owner', password_hash='x', zentao_account='owner')
    tester = User(username='query_tester', password_hash='x', zentao_account='tester')
    other = User(username='query_other', password_hash='x', zentao_account='other')
    major = Version(version_no='query-major', version_type=VersionType.MAJOR)
    cross = Version(version_no='query-cross', version_type=VersionType.MAJOR)
    db.add_all([owner, tester, other, major, cross])
    db.flush()
    when = datetime(2026, 1, 2)
    reqs = []
    for i in range(count):
        req = Requirement(zentao_req_id=f'r#{10000+i}', title=f'Query {i}', major_version_id=major.id,
                          owner_id=owner.id, zentao_story_id=10000+i, test_completed=True,
                          test_completed_at=when - timedelta(days=1))
        db.add(req)
        db.flush()
        reqs.append(req)
        db.add_all([
            Case(requirement_id=req.id, zentao_case_id=f'u#{20000+i}', zentao_case_numeric_id=20000+i),
            ZentaoTestCaseMirror(zentao_case_id=f'u#{20000+i}', zentao_case_numeric_id=20000+i,
                                zentao_story_id=10000+i, deleted=False),
            BugTracking(major_version_id=cross.id, bug_id=f'b#{30000+i}', source_type=BugSourceType.MANUAL,
                        zentao_linked_case_id=str(20000+i), zentao_opened_at=when,
                        zentao_opened_by_account='other', retest_dismissed=i % 3 == 0),
            BugTracking(major_version_id=major.id, requirement_id=req.id, bug_id=f'b#{40000+i}',
                        source_type=BugSourceType.MANUAL, retest_activated=True,
                        retest_activated_req_id=req.id, retest_activated_by_id=other.id,
                        retest_activated_at=when, retest_dismissed=i % 3 == 0),
            RequirementRetestRecord(requirement_id=req.id, user_id=tester.id, passed=True, updated_at=when),
            RequirementRetestRecord(requirement_id=req.id, user_id=other.id, passed=False, updated_at=when - timedelta(days=1)),
        ])
    db.commit()
    return tester, major, reqs


@pytest.mark.parametrize('count', [10, 50, 200, 401])
def test_queries_are_batched_and_read_only_after_write_time_reconciliation(db_session, count):
    tester, major, _ = seed(db_session, count)
    tester_id, major_id = tester.id, major.id
    db_session.expire_all()
    tester = db_session.get(User, tester_id)
    statements = []
    def record(conn, cursor, statement, params, context, many):
        statements.append(statement)
    event.listen(db_session.bind, 'after_cursor_execute', record)
    try:
        rows = WorkbenchService(db_session).get_retest_workbench(current_user=tester, major_version_id=major_id)
    finally:
        event.remove(db_session.bind, 'after_cursor_execute', record)
    assert len(rows) == count
    assert all(q.lstrip().startswith('SELECT') for q in statements)
    selects = [q for q in statements if q.lstrip().startswith('SELECT')]
    assert len(selects) <= (25 if count > 400 else 15), len(selects)
    assert len([q for q in selects if 'FROM requirement_retest_records' in q]) == (2 if count > 400 else 1)
    for i, row in enumerate(rows):
        assert row['my_retest_passed'] is True
        assert row['retest_conclusion'] == ('passed' if i % 3 == 0 else 'failed')
        assert row['retest_passed'] is (i % 3 == 0)
        assert row['test_cases'][0]['bugs'][0]['major_version_no'] == 'query-cross'
        assert row['test_cases'][0]['bugs'][0]['retest_problem_kind'] == 'collected'


def test_prefetch_preserves_views_and_write_path_problem_scope(db_session):
    _, _, reqs = seed(db_session, 3)
    # A Bug tagged to one story but also to another story's testcase exercises
    # the existing batch-vs-single-requirement precedence rule.
    db_session.add(BugTracking(major_version_id=reqs[0].major_version_id, bug_id='b#mixed',
                              source_type=BugSourceType.RETEST, zentao_story_id=reqs[0].zentao_story_id,
                              zentao_linked_case_id='20001', zentao_opened_at=datetime(2026, 1, 3)))
    local_case = db_session.query(Case).filter_by(requirement_id=reqs[0].id).one()
    local_case.zentao_product_id = '310'
    local_case.zentao_product_name = '310'
    db_session.add(BugTracking(major_version_id=reqs[0].major_version_id, bug_id='b#product-name',
                              source_type=BugSourceType.MANUAL, zentao_product_id=310,
                              zentao_product_name='Readable product'))
    deleted_case = db_session.query(ZentaoTestCaseMirror).filter_by(zentao_case_numeric_id=20002).one()
    deleted_case.deleted = True
    db_session.commit()
    baseline = WorkbenchLinkService(db_session)
    batched = WorkbenchLinkService(db_session, requirements=reqs)
    for scope in [reqs, [reqs[1]]]:
        assert baseline.build_requirement_case_view(scope, {}) == batched.build_requirement_case_view(scope, {})
        for include_retest in [False, True]:
            assert baseline.build_requirement_free_bug_view(scope, {}, include_retest=include_retest) == batched.build_requirement_free_bug_view(scope, {}, include_retest=include_retest)
        assert baseline.build_retest_evidence(scope, {}) == batched.build_retest_evidence(scope, {})
    retest = RetestService(db_session)
    for req in reqs:
        assert retest.compute_retest_problems(req) == retest.compute_retest_problems(
            req, activated_rows=batched.activated_bug_rows(req.id),
            evidence=batched.build_retest_evidence([req], {}).get(req.id, []))


def test_request_cache_does_not_hide_subsequent_bug_changes(db_session):
    tester, major, _ = seed(db_session, 1)
    service = WorkbenchService(db_session)
    first = service.get_retest_workbench(current_user=tester, major_version_id=major.id)
    assert first[0]['test_cases'][0]['bugs']
    bug = db_session.query(BugTracking).filter_by(bug_id='b#30000').one()
    bug.zentao_deleted = True
    db_session.commit()
    second = service.get_retest_workbench(current_user=tester, major_version_id=major.id)
    assert second[0]['test_cases'][0]['bugs'] == []


def test_all_pending_is_per_user_and_latest_record_ties_use_id(db_session):
    tester, major, reqs = seed(db_session, 3)
    service = WorkbenchService(db_session)
    assert service.get_retest_workbench(current_user=tester, mode='all_pending') == []
    viewer = User(username='query_viewer', password_hash='x')
    db_session.add(viewer)
    # Both records share a timestamp: the later database id is authoritative.
    records = db_session.query(RequirementRetestRecord).filter_by(requirement_id=reqs[0].id).order_by(RequirementRetestRecord.id).all()
    records[1].updated_at = records[0].updated_at
    db_session.commit()
    rows = service.get_retest_workbench(current_user=viewer, mode='all_pending')
    assert len(rows) == 3
    assert not rows[0]['my_retest_completed']
    assert rows[0]['retest_conclusion'] == 'failed'
    assert rows[0]['retest_passed'] is False


def test_index_backfill_is_idempotent_and_uses_equivalent_legacy_indexes(db_session):
    # Simulate an old database whose model-declared indexes were never created.
    for table in ('requirements', 'bug_tracking', 'zentao_testcase_mirror', 'zentao_task_mirror'):
        for index in inspect(db_session.connection()).get_indexes(table):
            if not index['unique']:
                db_session.execute(text(f'DROP INDEX "{index["name"]}"'))
    db_session.execute(text('CREATE INDEX legacy_story_index ON bug_tracking (zentao_story_id)'))
    db_session.commit()
    ensure_workbench_indexes(db_session)
    before = {t: inspect(db_session.connection()).get_indexes(t) for t in ('requirements', 'bug_tracking', 'zentao_task_mirror')}
    ensure_workbench_indexes(db_session)
    after = {t: inspect(db_session.connection()).get_indexes(t) for t in before}
    assert before == after
    story_indexes = [i for i in after['bug_tracking'] if i['column_names'] == ['zentao_story_id']]
    assert [i['name'] for i in story_indexes] == ['legacy_story_index']
    for query in (
        'SELECT id FROM requirements WHERE major_version_id=1 AND test_completed=1',
        'SELECT id FROM bug_tracking WHERE requirement_id IN (1, 2)',
        "SELECT id FROM bug_tracking WHERE zentao_linked_case_id IN ('123', 'u#123')",
        'SELECT id FROM bug_tracking WHERE retest_activated_req_id=1',
        'SELECT task_id FROM zentao_task_mirror WHERE story=123',
    ):
        plan = db_session.execute(text('EXPLAIN QUERY PLAN ' + query)).all()
        assert any('SEARCH' in row[3] and 'INDEX' in row[3] for row in plan), plan
