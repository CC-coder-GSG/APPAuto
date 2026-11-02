"""Backfill indexes for old installations after schema compatibility migrations."""
from __future__ import annotations

from sqlalchemy import inspect
from sqlalchemy.orm import Session

from app.models import BugTracking, Requirement, RequirementRetestRecord, ZentaoTaskMirror, ZentaoTestCaseMirror


def ensure_workbench_indexes(db: Session) -> None:
    targets = {
        Requirement: {('major_version_id', 'test_completed')},
        BugTracking: {('requirement_id', 'source_type', 'source_ref'), ('zentao_story_id',),
                      ('zentao_linked_case_id',), ('retest_activated_req_id',)},
        RequirementRetestRecord: {('requirement_id',), ('user_id',)},
        ZentaoTestCaseMirror: {('zentao_story_id',), ('zentao_case_numeric_id',)},
        ZentaoTaskMirror: {('story',)},
    }
    connection = db.connection()
    inspector = inspect(connection)
    for model, columns in targets.items():
        table = model.__table__
        existing = [tuple(item['column_names']) for item in inspector.get_indexes(table.name)
                    if item.get('dialect_options', {}).get('sqlite_where') is None]
        existing += [tuple(item['column_names']) for item in inspector.get_unique_constraints(table.name)]
        for index in sorted(table.indexes, key=lambda item: item.name):
            names = tuple(col.name for col in index.columns)
            if names not in columns or any(cols[:len(names)] == names for cols in existing):
                continue
            index.create(connection, checkfirst=True)
            existing.append(names)
    db.commit()
