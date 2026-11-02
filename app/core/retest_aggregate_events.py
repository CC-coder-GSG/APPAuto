"""Maintain derived retest fields at application transaction boundaries.

Only source-field mutations enqueue work. Reads, unrelated writes and updates to
the derived fields themselves do not trigger reconciliation.
"""
from __future__ import annotations

from sqlalchemy import event, inspect, select
from sqlalchemy.orm import Session


class AppSession(Session):
    pass


_SOURCE_FIELDS = {
    'bug_tracking': ('requirement_id', 'source_type', 'source_ref', 'zentao_story_id',
                     'zentao_linked_case_id', 'zentao_deleted', 'zentao_opened_at',
                     'zentao_opened_by_account', 'retest_activated', 'retest_activated_req_id',
                     'retest_activated_by_id', 'retest_activated_at', 'retest_dismissed'),
    'requirements': ('owner_id', 'zentao_story_id', 'test_completed', 'test_completed_at'),
    'requirement_retest_records': ('requirement_id', 'user_id', 'passed', 'minor_version_id', 'updated_at'),
    'zentao_testcase_mirror': ('zentao_story_id', 'zentao_case_id', 'deleted'),
    'test_cases': ('requirement_id', 'zentao_case_id', 'zentao_case_numeric_id'),
    'users': ('zentao_account',),
}
_PENDING = 'retest_aggregate_pending'
_OBJECTS = 'retest_aggregate_objects'
_RUNNING = 'retest_aggregate_running'


def _scope(session):
    return session.info.setdefault(_PENDING, {'req_ids': set(), 'story_ids': set(),
                                             'case_keys': set(), 'owner_ids': set(), 'all': False})


def _add(scope, table, values):
    if table == 'requirements':
        scope['req_ids'].add(values.get('id'))
    elif table == 'users':
        scope['owner_ids'].add(values.get('id'))
    else:
        scope['req_ids'].update((values.get('requirement_id'), values.get('retest_activated_req_id')))
        scope['story_ids'].add(values.get('zentao_story_id'))
        scope['case_keys'].update((values.get('zentao_case_id'), values.get('zentao_linked_case_id')))


@event.listens_for(AppSession, 'before_flush')
def collect_sources(session, flush_context, instances):
    if session.info.get(_RUNNING):
        return
    touched = []
    by_table = {}
    for obj in set(session.new) | set(session.dirty) | set(session.deleted):
        state = inspect(obj)
        table = state.mapper.local_table
        fields = _SOURCE_FIELDS.get(table.name)
        if obj in session.deleted and table.name in {'users', 'versions', 'software_products'}:
            _scope(session)['all'] = True
        if fields is None:
            continue
        relationships = ('owner',) if table.name == 'requirements' else ()
        if obj not in session.new and obj not in session.deleted and not any(
            state.attrs[name].history.has_changes() for name in (*fields, *relationships)
        ):
            continue
        touched.append(obj)
        if state.identity:
            by_table.setdefault(table, set()).add(state.identity[0])
    if not touched:
        return
    pending = _scope(session)
    # Query preimages before flush: attribute history may lack an expired old
    # story/case value. Both sides of a reassignment must be refreshed.
    for table, ids in by_table.items():
        columns = [table.c.id, *(table.c[name] for name in _SOURCE_FIELDS[table.name])]
        for start in range(0, len(ids), 400):
            rows = session.connection().execute(select(*columns).where(table.c.id.in_(sorted(ids)[start:start + 400])))
            for row in rows.mappings():
                _add(pending, table.name, row)
    session.info.setdefault(_OBJECTS, []).extend(touched)


@event.listens_for(AppSession, 'after_flush_postexec')
def collect_current_sources(session, flush_context):
    for obj in session.info.pop(_OBJECTS, []):
        state = inspect(obj)
        table = state.mapper.local_table.name
        if not state.deleted:
            _add(_scope(session), table, {name: getattr(obj, name) for name in ('id', *_SOURCE_FIELDS[table])})


@event.listens_for(AppSession, 'do_orm_execute')
def collect_bulk_sources(execution):
    if execution.session.info.get(_RUNNING) or not (execution.is_insert or execution.is_update or execution.is_delete):
        return
    table = getattr(execution.statement, 'table', None)
    if table is not None and (table.name in _SOURCE_FIELDS or
                              (execution.is_delete and table.name in {'versions', 'software_products'})):
        # ORM bulk DML bypasses normal object histories. Reconcile the batch at
        # commit; the current application primarily uses normal ORM mutations.
        _scope(execution.session)['all'] = True


@event.listens_for(AppSession, 'before_commit')
def reconcile_before_commit(session):
    if session.info.get(_RUNNING):
        return
    session.flush()
    pending = session.info.get(_PENDING)
    if pending is None:
        return
    session.info[_RUNNING] = True
    try:
        from app.services.retest_aggregate_service import RetestAggregateService
        RetestAggregateService(session).reconcile_scope(pending)
        session.flush()
        session.info.pop(_PENDING, None)
    finally:
        session.info.pop(_RUNNING, None)


@event.listens_for(AppSession, 'after_soft_rollback')
def discard_rolled_back_sources(session, previous_transaction):
    session.info.pop(_OBJECTS, None)
    session.info.pop(_PENDING, None)
    session.info.pop(_RUNNING, None)
    if previous_transaction.nested:
        # Outer writes can survive a savepoint rollback. A full pass at the
        # outer commit avoids discarding their pending invalidations.
        _scope(session)['all'] = True


@event.listens_for(AppSession, 'after_transaction_end')
def clear_finished_transaction(session, transaction):
    if transaction.parent is None:
        session.info.pop(_OBJECTS, None)
        session.info.pop(_PENDING, None)
