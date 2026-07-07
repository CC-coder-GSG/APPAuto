from __future__ import annotations

from collections import defaultdict
from typing import Any

from sqlalchemy import or_
from sqlalchemy.orm import Session, joinedload

from app.models import BugSourceType, BugTracking, Requirement, Version, VersionType, ZentaoTestCaseMirror


def _case_key(value: Any) -> str:
    """用例关联键归一化：'u#19712' / 'U#19712' / '19712' → '19712'。

    镜像的 zentao_case_id 带 'u#' 前缀，而 bug_tracking.zentao_linked_case_id
    历史上存过裸数字，两种格式并存——统一剥前缀后按数字部分匹配，否则
    「禅道 Bug 关联了用例但工作台用例栏不显示」。
    """
    key = str(value or "").strip().lower()
    if key.startswith("u#"):
        key = key[2:]
    return key


def _case_key_sql_variants(keys: set[str]) -> list[str]:
    """SQL in_ 过滤用：归一化键的两种落库形态（裸数字 + u# 前缀）都要能命中。"""
    out: set[str] = set()
    for k in keys:
        out.add(k)
        out.add(f"u#{k}")
    return list(out)


class WorkbenchLinkService:
    def __init__(self, db: Session):
        self.db = db

    def minor_version_name_map(self) -> dict[int, str]:
        return {
            version.id: version.version_no
            for version in self.db.query(Version).filter(Version.version_type == VersionType.MINOR).all()
        }

    def serialize_bug_brief(
        self,
        bug: BugTracking,
        minors: dict[int, str],
        *,
        auto_linked: bool = False,
    ) -> dict[str, Any]:
        return {
            "id": bug.id,
            "bug_id": bug.bug_id,
            "zentao_bug_id": bug.zentao_bug_id,
            "zentao_bug_url": bug.zentao_bug_url,
            "zentao_bug_title": bug.zentao_bug_title,
            "found_minor_version_no": minors.get(bug.found_minor_version_id, "未知") if bug.found_minor_version_id else "未知",
            "fixed_minor_version_no": minors.get(bug.fixed_minor_version_id, "未知") if bug.fixed_minor_version_id else None,
            "dispatched_to_name": bug.dispatched_to.shown_name if getattr(bug, "dispatched_to", None) else None,
            "is_retest_failed": getattr(bug, "is_retest_failed", False),
            "closed": bool(getattr(bug, "closed", False)),
            "auto_linked": bool(auto_linked),
        }

    def build_requirement_case_view(
        self,
        reqs: list[Requirement],
        minors: dict[int, str],
    ) -> dict[int, list[dict[str, Any]]]:
        req_ids = [req.id for req in reqs]
        major_ids = [req.major_version_id for req in reqs if req.major_version_id]
        local_case_ids = [case.id for req in reqs for case in (req.test_cases or [])]
        local_case_bug_rows = (
            self.db.query(BugTracking)
            .options(joinedload(BugTracking.dispatched_to))
            .filter(
                BugTracking.requirement_id.in_(req_ids),
                BugTracking.source_type == BugSourceType.CASE,
                BugTracking.source_ref.in_([str(case_id) for case_id in local_case_ids] if local_case_ids else ["-1"]),
            )
            .all()
            if req_ids
            else []
        )

        case_bug_map: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for bug in local_case_bug_rows:
            case_bug_map[str(bug.source_ref or "")].append(self.serialize_bug_brief(bug, minors))

        story_ids = [int(req.zentao_story_id) for req in reqs if req.zentao_story_id]
        mirror_rows = (
            self.db.query(ZentaoTestCaseMirror)
            .filter(
                ZentaoTestCaseMirror.zentao_story_id.in_(story_ids),
                ZentaoTestCaseMirror.deleted.isnot(True),
            )
            .order_by(ZentaoTestCaseMirror.zentao_case_numeric_id.asc())
            .all()
            if story_ids
            else []
        )
        mirror_by_story: dict[int, list[ZentaoTestCaseMirror]] = defaultdict(list)
        all_mirror_case_keys: set[str] = set()
        for row in mirror_rows:
            if row.zentao_story_id:
                mirror_by_story[int(row.zentao_story_id)].append(row)
            key = _case_key(row.zentao_case_id)
            if key:
                all_mirror_case_keys.add(key)

        # Reverse-lookup Bugs by Zentao linked-case id. Covers the "this Bug
        # was opened against a testcase but never tagged with the parent
        # story" case, which is otherwise invisible to the story-id join.
        case_keyed_bug_map: dict[str, list[BugTracking]] = defaultdict(list)
        if all_mirror_case_keys and major_ids:
            linked_bug_rows = (
                self.db.query(BugTracking)
                .options(joinedload(BugTracking.dispatched_to))
                .filter(
                    BugTracking.zentao_linked_case_id.isnot(None),
                    BugTracking.major_version_id.in_(major_ids),
                    BugTracking.zentao_deleted.isnot(True),
                )
                .all()
            )
            for bug in linked_bug_rows:
                key = _case_key(bug.zentao_linked_case_id)
                if not key:
                    continue
                if key in all_mirror_case_keys:
                    case_keyed_bug_map[key].append(bug)

        result: dict[int, list[dict[str, Any]]] = {}
        for req in reqs:
            items: list[dict[str, Any]] = []
            existing_case_ids: set[str] = set()
            for local_case in req.test_cases or []:
                local_key = _case_key(local_case.zentao_case_id)
                existing_case_ids.add(local_key)
                bugs_payload = list(case_bug_map.get(str(local_case.id), []))
                seen_bug_ids = {b["id"] for b in bugs_payload}
                # Auto-linked Bug via testcase reverse mapping.
                for bug in case_keyed_bug_map.get(local_key, []):
                    if bug.id in seen_bug_ids:
                        continue
                    bugs_payload.append(self.serialize_bug_brief(bug, minors, auto_linked=True))
                    seen_bug_ids.add(bug.id)
                items.append(
                    {
                        "id": local_case.id,
                        "zentao_case_id": local_case.zentao_case_id,
                        "zentao_case_url": local_case.zentao_case_url,
                        "bugs": bugs_payload,
                        "auto_linked": False,
                        "mirror_case_numeric_id": local_case.zentao_case_numeric_id,
                    }
                )

            if req.zentao_story_id:
                for mirror in mirror_by_story.get(int(req.zentao_story_id), []):
                    case_key = _case_key(mirror.zentao_case_id)
                    if case_key and case_key in existing_case_ids:
                        continue
                    auto_bugs = [
                        self.serialize_bug_brief(bug, minors, auto_linked=True)
                        for bug in case_keyed_bug_map.get(case_key, [])
                    ]
                    items.append(
                        {
                            "id": -int(mirror.zentao_case_numeric_id),
                            "zentao_case_id": mirror.zentao_case_id,
                            "zentao_case_url": mirror.zentao_case_url,
                            "bugs": auto_bugs,
                            "auto_linked": True,
                            "mirror_case_numeric_id": mirror.zentao_case_numeric_id,
                        }
                    )
            result[req.id] = items
        return result

    def build_requirement_free_bug_view(
        self,
        reqs: list[Requirement],
        minors: dict[int, str],
        *,
        include_retest: bool = False,
    ) -> tuple[dict[int, list[dict[str, Any]]], dict[int, list[dict[str, Any]]]]:
        req_ids = [req.id for req in reqs]
        major_ids = [req.major_version_id for req in reqs]
        story_ids = [int(req.zentao_story_id) for req in reqs if req.zentao_story_id]

        local_query = (
            self.db.query(BugTracking)
            .options(joinedload(BugTracking.dispatched_to))
            .filter(BugTracking.requirement_id.in_(req_ids))
        )
        if include_retest:
            local_query = local_query.filter(BugTracking.source_type == BugSourceType.RETEST)
        else:
            local_query = local_query.filter(BugTracking.source_type.in_([BugSourceType.MANUAL, BugSourceType.REQUIREMENT]))
        local_rows = local_query.all() if req_ids else []

        # Build a story_id → mirror_case_keys map so we can also pull in
        # auto-linked Bugs that only reference the testcase (no story tag).
        mirror_keys_by_story: dict[int, set[str]] = defaultdict(set)
        if story_ids:
            for row in (
                self.db.query(ZentaoTestCaseMirror.zentao_story_id, ZentaoTestCaseMirror.zentao_case_id)
                .filter(
                    ZentaoTestCaseMirror.zentao_story_id.in_(story_ids),
                    ZentaoTestCaseMirror.deleted.isnot(True),
                )
                .all()
            ):
                story_id_val, case_id_val = row
                key = _case_key(case_id_val)
                if story_id_val and key:
                    mirror_keys_by_story[int(story_id_val)].add(key)
        all_mirror_keys: set[str] = set()
        for keys in mirror_keys_by_story.values():
            all_mirror_keys.update(keys)

        story_filter_terms = []
        if story_ids:
            story_filter_terms.append(BugTracking.zentao_story_id.in_(story_ids))
        if all_mirror_keys:
            story_filter_terms.append(BugTracking.zentao_linked_case_id.in_(_case_key_sql_variants(all_mirror_keys)))

        if story_filter_terms and major_ids:
            story_query = (
                self.db.query(BugTracking)
                .options(joinedload(BugTracking.dispatched_to))
                .filter(
                    or_(*story_filter_terms),
                    BugTracking.major_version_id.in_(major_ids),
                    BugTracking.zentao_deleted.isnot(True),
                )
            )
            if include_retest:
                story_query = story_query.filter(BugTracking.source_type == BugSourceType.RETEST)
            else:
                story_query = story_query.filter(
                    BugTracking.source_type.in_([BugSourceType.MANUAL, BugSourceType.REQUIREMENT])
                )
            story_rows = story_query.all()
        else:
            story_rows = []

        req_by_story: dict[int, list[Requirement]] = defaultdict(list)
        for req in reqs:
            if req.zentao_story_id:
                req_by_story[int(req.zentao_story_id)].append(req)

        result: dict[int, list[dict[str, Any]]] = defaultdict(list)
        seen_bug_ids: dict[int, set[int]] = defaultdict(set)

        for bug in local_rows:
            req_id = int(bug.requirement_id or -1)
            if req_id <= 0:
                continue
            result[req_id].append(self.serialize_bug_brief(bug, minors, auto_linked=False))
            seen_bug_ids[req_id].add(bug.id)

        auto_linked_story_bug_map: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for bug in story_rows:
            bug_story_id = int(bug.zentao_story_id or 0)
            bug_case_key = _case_key(bug.zentao_linked_case_id)

            target_reqs: list[Requirement] = []
            if bug_story_id and bug_story_id in req_by_story:
                target_reqs = list(req_by_story[bug_story_id])
            elif bug_case_key:
                # Fall back to story->case map: a Bug with a linked_case_id
                # owns whichever req(s) have that case in scope.
                for sid, keys in mirror_keys_by_story.items():
                    if bug_case_key in keys:
                        target_reqs.extend(req_by_story.get(sid, []))

            for req in target_reqs:
                if bug.id in seen_bug_ids[req.id]:
                    continue
                auto_linked_story_bug_map[req.id].append(self.serialize_bug_brief(bug, minors, auto_linked=True))
                seen_bug_ids[req.id].add(bug.id)

        for req_id, rows in auto_linked_story_bug_map.items():
            result[req_id].extend(rows)

        return result, auto_linked_story_bug_map

    def build_retest_evidence(
        self,
        reqs: list[Requirement],
        minors: dict[int, str],
    ) -> dict[int, list[dict[str, Any]]]:
        """
        Auto-collect candidate retest evidence: Bugs filed in Zentao after
        the requirement was marked test_completed, scoped to the requirement
        via story_id or via testcase reverse-lookup.

        Returned shape: req_id → list of bug-brief dicts, each tagged with
        `auto_linked=True` so the retest UI can present them as candidates.
        """
        if not reqs:
            return {}

        major_ids = [req.major_version_id for req in reqs if req.major_version_id]
        story_ids = [int(req.zentao_story_id) for req in reqs if req.zentao_story_id]

        # Same story → mirror keys map as in build_requirement_free_bug_view.
        mirror_keys_by_story: dict[int, set[str]] = defaultdict(set)
        all_mirror_keys: set[str] = set()
        if story_ids:
            for story_id_val, case_id_val in (
                self.db.query(ZentaoTestCaseMirror.zentao_story_id, ZentaoTestCaseMirror.zentao_case_id)
                .filter(
                    ZentaoTestCaseMirror.zentao_story_id.in_(story_ids),
                    ZentaoTestCaseMirror.deleted.isnot(True),
                )
                .all()
            ):
                key = _case_key(case_id_val)
                if story_id_val and key:
                    mirror_keys_by_story[int(story_id_val)].add(key)
                    all_mirror_keys.add(key)

        story_filter_terms = []
        if story_ids:
            story_filter_terms.append(BugTracking.zentao_story_id.in_(story_ids))
        if all_mirror_keys:
            story_filter_terms.append(BugTracking.zentao_linked_case_id.in_(_case_key_sql_variants(all_mirror_keys)))

        if not (story_filter_terms and major_ids):
            return {req.id: [] for req in reqs}

        candidate_rows = (
            self.db.query(BugTracking)
            .options(joinedload(BugTracking.dispatched_to))
            .filter(
                or_(*story_filter_terms),
                BugTracking.major_version_id.in_(major_ids),
                BugTracking.zentao_deleted.isnot(True),
            )
            .all()
        )

        req_by_story: dict[int, list[Requirement]] = defaultdict(list)
        for req in reqs:
            if req.zentao_story_id:
                req_by_story[int(req.zentao_story_id)].append(req)

        result: dict[int, list[dict[str, Any]]] = {req.id: [] for req in reqs}
        seen_bug_ids: dict[int, set[int]] = defaultdict(set)

        for bug in candidate_rows:
            target_reqs: list[Requirement] = []
            bug_story_id = int(bug.zentao_story_id or 0)
            bug_case_key = _case_key(bug.zentao_linked_case_id)
            if bug_story_id and bug_story_id in req_by_story:
                target_reqs = list(req_by_story[bug_story_id])
            elif bug_case_key:
                for sid, keys in mirror_keys_by_story.items():
                    if bug_case_key in keys:
                        target_reqs.extend(req_by_story.get(sid, []))

            for req in target_reqs:
                # Only include Bugs filed AFTER the requirement was
                # test-completed — earlier Bugs are part of the original test
                # phase and already covered by the existing free-bug view.
                cutoff = req.test_completed_at
                opened_at = bug.zentao_opened_at
                if cutoff and opened_at and opened_at <= cutoff:
                    continue
                # If we have neither cutoff nor opened_at we can't make a
                # safe call about whether this is "new" — skip rather than
                # show a stale Bug as a fresh leak.
                if not opened_at:
                    continue
                if bug.id in seen_bug_ids[req.id]:
                    continue
                result[req.id].append(self.serialize_bug_brief(bug, minors, auto_linked=True))
                seen_bug_ids[req.id].add(bug.id)

        return result
