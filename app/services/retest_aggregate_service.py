from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from sqlalchemy.orm import Session, joinedload

from app.models import Requirement, RequirementRetestRecord, ZentaoTestCaseMirror
from app.services.workbench_link_service import WorkbenchLinkService, _case_key, _case_key_sql_variants, _load_matching

AGGREGATE_FIELDS = ('retest_completed', 'retest_passed', 'retest_minor_version_id', 'retested_by_id', 'retested_at')


class RetestAggregateService:
    """Batch maintenance only; callers own the transaction and its rollback."""

    def __init__(self, db: Session):
        self.db = db

    def reconcile_scope(self, scope: dict) -> dict:
        if scope.get('all'):
            return self.reconcile_all()
        req_ids = {value for value in scope['req_ids'] if value}
        story_ids = {int(value) for value in scope['story_ids'] if value}
        keys = {_case_key(value) for value in scope['case_keys'] if value}
        if keys:
            variants = set(_case_key_sql_variants(keys)) | {str(value) for value in scope['case_keys'] if value}
            mirrors = _load_matching(self.db.query(ZentaoTestCaseMirror), [(ZentaoTestCaseMirror.zentao_case_id, variants)])
            story_ids.update(m.zentao_story_id for m in mirrors if m.zentao_story_id)
        requirements = _load_matching(self.db.query(Requirement), [
            (Requirement.zentao_story_id, story_ids),
            (Requirement.owner_id, {value for value in scope['owner_ids'] if value}),
        ])
        req_ids.update(r.id for r in requirements)
        return self.reconcile(req_ids)

    def reconcile(self, requirement_ids) -> dict:
        from app.services.retest_service import RetestService

        self.db.flush()
        ids = sorted(set(requirement_ids))
        checked = changed = 0
        for start in range(0, len(ids), 200):
            reqs = self.db.query(Requirement).options(
                joinedload(Requirement.owner), joinedload(Requirement.test_cases),
            ).filter(Requirement.id.in_(ids[start:start + 200])).populate_existing().all()
            if not reqs:
                continue
            link = WorkbenchLinkService(self.db, requirements=reqs)
            records_by_req = defaultdict(list)
            records = _load_matching(self.db.query(RequirementRetestRecord), [
                (RequirementRetestRecord.requirement_id, {r.id for r in reqs}),
            ])
            for record in records:
                records_by_req[record.requirement_id].append(record)
            for records in records_by_req.values():
                records.sort(key=lambda r: (r.updated_at or datetime.min, r.id), reverse=True)
            retest = RetestService(self.db)
            for req in reqs:
                before = tuple(getattr(req, name) for name in AGGREGATE_FIELDS)
                if not req.test_completed:
                    values = (False, None, None, None, None)
                    for name, value in zip(AGGREGATE_FIELDS, values):
                        setattr(req, name, value)
                else:
                    activated = link.activated_bug_rows(req.id)
                    problems = retest.compute_retest_problems(
                        req, activated_rows=activated,
                        evidence=link.build_retest_evidence([req], {}).get(req.id, []),
                    )
                    retest._recompute_aggregate(req, problems=problems,
                                                records=records_by_req[req.id], activated_rows=activated)
                checked += 1
                if before != tuple(getattr(req, name) for name in AGGREGATE_FIELDS):
                    changed += 1
                    if before[3] != req.retested_by_id:
                        self.db.expire(req, ['retester'])
            self.db.flush()
        return {'checked': checked, 'changed': changed}

    def reconcile_all(self, *, batch_size: int = 200, commit_batches: bool = False) -> dict:
        """Keyset scan, safe to retry; no clock-only updates on an unchanged result."""
        checked = changed = last_id = 0
        while True:
            ids = [row[0] for row in self.db.query(Requirement.id).filter(Requirement.id > last_id)
                   .order_by(Requirement.id).limit(batch_size).all()]
            if not ids:
                break
            result = self.reconcile(ids)
            checked += result['checked']
            changed += result['changed']
            last_id = ids[-1]
            if commit_batches:
                self.db.commit()
        return {'checked': checked, 'changed': changed}
