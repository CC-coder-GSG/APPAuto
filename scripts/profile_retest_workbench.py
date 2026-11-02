"""Compare retest reads on independent in-memory copies; never modify the source DB.

Example: python scripts/profile_retest_workbench.py --database app_auto.db --baseline-ref HEAD
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
import types
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from app.models import Requirement, User
from app.services.workbench_service import WorkbenchService


def legacy_modules(ref):
    modules = {}
    for name in ('workbench_link_service', 'retest_service', 'workbench_service'):
        fullname = f'app.services.{name}'
        source = subprocess.check_output(
            ['git', '-c', f'safe.directory={ROOT.as_posix()}', 'show', f'{ref}:app/services/{name}.py'],
            cwd=ROOT, encoding='utf-8-sig',
        )
        module = types.ModuleType(fullname)
        module.__file__ = str(ROOT / 'app/services' / f'{name}.py')
        with patch.dict(sys.modules, modules):
            exec(compile(source, module.__file__, 'exec'), module.__dict__)
        modules[fullname] = module
    return modules


def measure(snapshot, service_type, user_id, major_id, modules, *, indexes=False, backfill=False, workbench='retest'):
    db = sqlite3.connect(':memory:', check_same_thread=False)
    snapshot.backup(db)
    engine = create_engine('sqlite://', creator=lambda: db, poolclass=StaticPool)
    statements = []
    if indexes:
        from app.db.workbench_indexes import ensure_workbench_indexes
        with Session(engine) as session:
            ensure_workbench_indexes(session)
    backfill_result = None
    if backfill:
        from app.services.retest_aggregate_service import RetestAggregateService
        with Session(engine, autoflush=False) as session:
            backfill_result = RetestAggregateService(session).reconcile_all(commit_batches=True)
    @event.listens_for(engine, 'after_cursor_execute')
    def executed(conn, cursor, statement, params, context, many):
        statements.append(statement)
    try:
        # Benchmark local SQL only, including for historical mine implementations
        # whose list GET used to call the remote service synchronously.
        with Session(engine, autoflush=False) as session, patch.dict(sys.modules, modules), \
                patch.object(service_type, '_maybe_sync_task_status', return_value=False):
            user = session.get(User, user_id)
            statements.clear()
            start = time.perf_counter()
            service = service_type(session)
            method = service.get_my_workbench if workbench == 'mine' else service.get_retest_workbench
            rows = method(current_user=user, major_version_id=major_id)
            duration = time.perf_counter() - start
            stats = {'requirements': len(rows), 'statements': len(statements),
                     'writes': sum(q.lstrip().split()[0] in {'UPDATE', 'INSERT', 'DELETE'} for q in statements),
                     'ms': round(duration * 1000, 2)}
            if backfill_result is not None:
                stats['startup_backfill'] = backfill_result
            # Timestamps are expected to change when repairing stale aggregates.
            aggregate = session.query(Requirement.id, Requirement.retest_completed, Requirement.retest_passed,
                                      Requirement.retest_minor_version_id, Requirement.retested_by_id).order_by(Requirement.id).all()
            return rows, aggregate, stats
    finally:
        engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--baseline-ref')
    parser.add_argument('--workbench', choices=['retest', 'mine'], default='retest')
    parser.add_argument('--user-id', type=int)
    parser.add_argument('--major-version-id', type=int, action='append')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--indexes', action='store_true', help='Apply index backfill to the optimized in-memory copy only')
    parser.add_argument('--backfill', action='store_true', help='Reconcile optimized copy before timing the read-only GET')
    parser.add_argument('--allow-result-changes', action='store_true', help='Report intentional rule corrections without failing')
    args = parser.parse_args()
    source = sqlite3.connect(args.database.resolve().as_uri() + '?mode=ro', uri=True)
    snapshot = sqlite3.connect(':memory:')
    source.backup(snapshot)
    source.close()
    try:
        user_id = args.user_id or snapshot.execute('SELECT id FROM users ORDER BY id LIMIT 1').fetchone()[0]
        condition = ' WHERE test_completed=1' if args.workbench == 'retest' else ''
        majors = args.major_version_id or [r[0] for r in snapshot.execute(
            'SELECT DISTINCT major_version_id FROM requirements' + condition + ' ORDER BY major_version_id')]
        modules = legacy_modules(args.baseline_ref) if args.baseline_ref else {}
        reports = []
        for major in majors:
            report = {'major_version_id': major}
            if modules:
                old_rows, old_aggregate, report['before'] = measure(
                    snapshot, modules['app.services.workbench_service'].WorkbenchService, user_id, major, modules,
                    workbench=args.workbench)
            rows, aggregate, report['after'] = measure(snapshot, WorkbenchService, user_id, major, {}, indexes=args.indexes,
                                                       backfill=args.backfill, workbench=args.workbench)
            if modules:
                def comparable(value):
                    if isinstance(value, dict):
                        return {k: comparable(v) for k, v in value.items() if k != 'retest_activated_req_id'}
                    return [comparable(v) for v in value] if isinstance(value, list) else value
                report['identical_response'] = comparable(rows) == comparable(old_rows)
                report['identical_aggregate'] = aggregate == old_aggregate
                if not args.allow_result_changes and (not report['identical_response'] or not report['identical_aggregate']):
                    raise AssertionError(f'Output changed for major {major}')
            reports.append(report)
        result = json.dumps(reports, ensure_ascii=False, indent=2)
        print(result)
        if args.output:
            args.output.write_text(result + '\n', encoding='utf-8')
    finally:
        snapshot.close()


if __name__ == '__main__':
    main()
