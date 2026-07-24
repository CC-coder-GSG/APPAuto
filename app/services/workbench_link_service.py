from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

from sqlalchemy import func, or_
from sqlalchemy.orm import Session, joinedload

from app.models import BugSourceType, BugTracking, Requirement, TestCase, Version, VersionType, ZentaoTaskMirror, ZentaoTestCaseMirror


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
        self._major_names: dict[int, str] | None = None
        self._product_names: dict[str, str] | None = None

    @staticmethod
    def _readable_reference_name(value: Any, reference_id: Any = None) -> str | None:
        text = str(value or "").strip()
        reference = str(reference_id or "").strip()
        if not text or text.isdecimal() or (reference and text == reference):
            return None
        return text

    def _product_name_map(self) -> dict[str, str]:
        """Resolve historical product IDs from trustworthy local human-readable names."""
        if self._product_names is not None:
            return self._product_names

        votes: dict[str, Counter[str]] = defaultdict(Counter)
        for model in (TestCase, BugTracking, ZentaoTestCaseMirror):
            rows = (
                self.db.query(model.zentao_product_id, model.zentao_product_name, func.count())
                .filter(model.zentao_product_id.isnot(None), model.zentao_product_name.isnot(None))
                .group_by(model.zentao_product_id, model.zentao_product_name)
                .all()
            )
            for product_id, product_name, count in rows:
                readable = self._readable_reference_name(product_name, product_id)
                key = str(product_id or "").strip()
                if key and readable:
                    votes[key][readable] += int(count or 0)

        self._product_names = {
            product_id: name_counts.most_common(1)[0][0]
            for product_id, name_counts in votes.items()
            if name_counts
        }
        return self._product_names

    def _case_belongs_label(
        self,
        *,
        product_id: Any = None,
        product_name: Any = None,
        module_id: Any = None,
        module_name: Any = None,
    ) -> str | None:
        product_key = str(product_id or "").strip()
        product_label = self._readable_reference_name(product_name, product_id)
        if not product_label and product_key and product_key != "0":
            product_label = self._product_name_map().get(product_key) or f"产品 #{product_key}"

        module_key = str(module_id or "").strip()
        module_label = self._readable_reference_name(module_name, module_id)
        if not module_label and module_key and module_key != "0":
            module_label = f"模块 #{module_key}"

        return " / ".join(x for x in (product_label, module_label) if x) or None

    def minor_version_name_map(self) -> dict[int, str]:
        return {
            version.id: version.version_no
            for version in self.db.query(Version).filter(Version.version_type == VersionType.MINOR).all()
        }

    def _major_name_map(self) -> dict[int, str]:
        if self._major_names is None:
            self._major_names = {
                version.id: version.version_no
                for version in self.db.query(Version).filter(Version.version_type == VersionType.MAJOR).all()
            }
        return self._major_names

    def serialize_bug_brief(
        self,
        bug: BugTracking,
        minors: dict[int, str],
        *,
        auto_linked: bool = False,
        expect_story_id: int | None = None,
    ) -> dict[str, Any]:
        # 发现于：优先本地小版本；匹配不到时回退禅道侧的影响版本/构建名
        # 字符串（zentao_affected_version），而不是一律「未知」。
        found_no = minors.get(bug.found_minor_version_id) if bug.found_minor_version_id else None
        if not found_no:
            found_no = (bug.zentao_affected_version or "").strip() or "未知"
        # 禅道校对：本地挂在某需求下的 Bug，若禅道侧标记了 story 且与该需求
        # 的 story 不一致，前端提示「归属不一致」；禅道已删除的也标出来。
        story_mismatch = bool(
            expect_story_id
            and bug.zentao_story_id
            and int(bug.zentao_story_id) != int(expect_story_id)
        )
        return {
            "zentao_deleted": bool(getattr(bug, "zentao_deleted", False)),
            "story_mismatch": story_mismatch,
            "zentao_story_id": bug.zentao_story_id,
            # 复测问题留痕：激活人/误报标记/禅道提出人（复测结论判定用）
            "retest_activated": bool(getattr(bug, "retest_activated", False)),
            "retest_activated_by_name": bug.retest_activated_by.shown_name if getattr(bug, "retest_activated_by", None) else None,
            "retest_dismissed": bool(getattr(bug, "retest_dismissed", False)),
            "zentao_opened_by_account": bug.zentao_opened_by_account,
            "zentao_opened_by_name": bug.zentao_opened_by_name,
            "id": bug.id,
            "bug_id": bug.bug_id,
            "zentao_bug_id": bug.zentao_bug_id,
            "zentao_bug_url": bug.zentao_bug_url,
            "zentao_bug_title": bug.zentao_bug_title,
            "found_minor_version_no": found_no,
            "fixed_minor_version_no": minors.get(bug.fixed_minor_version_id, "未知") if bug.fixed_minor_version_id else None,
            "dispatched_to_name": bug.dispatched_to.shown_name if getattr(bug, "dispatched_to", None) else None,
            "is_retest_failed": getattr(bug, "is_retest_failed", False),
            "closed": bool(getattr(bug, "closed", False)),
            "auto_linked": bool(auto_linked),
            # 跨大版本归集展示：前端与需求卡的 major_version_id 比对，
            # 不一致时显示「来自 X」徽章。
            "major_version_id": bug.major_version_id,
            "major_version_no": self._major_name_map().get(bug.major_version_id),
        }

    def build_requirement_case_view(
        self,
        reqs: list[Requirement],
        minors: dict[int, str],
    ) -> dict[int, list[dict[str, Any]]]:
        req_ids = [req.id for req in reqs]
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

        # 存原始行，序列化推迟到需求循环里做——序列化时要携带该需求的
        # story id，用于「禅道归属不一致」校对。
        case_bug_rows_map: dict[str, list[BugTracking]] = defaultdict(list)
        for bug in local_case_bug_rows:
            case_bug_rows_map[str(bug.source_ref or "")].append(bug)

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

        # 禅道校对：按本地用例的 numeric id 反查镜像（不排除已删除行），
        # 用于标注「禅道已删除」和「禅道归属不一致」。
        local_numeric_ids = [
            int(case.zentao_case_numeric_id)
            for req in reqs
            for case in (req.test_cases or [])
            if case.zentao_case_numeric_id
        ]
        mirror_by_numeric: dict[int, ZentaoTestCaseMirror] = {}
        if local_numeric_ids:
            for row in (
                self.db.query(ZentaoTestCaseMirror)
                .filter(ZentaoTestCaseMirror.zentao_case_numeric_id.in_(local_numeric_ids))
                .all()
            ):
                mirror_by_numeric[int(row.zentao_case_numeric_id)] = row

        # Reverse-lookup Bugs by Zentao linked-case id. Covers the "this Bug
        # was opened against a testcase but never tagged with the parent
        # story" case, which is otherwise invisible to the story-id join.
        # 不按大版本过滤：同一需求可能横跨多个大版本，其他大版本上关联的
        # Bug 也要挂出来（前端按 major_version_id 差异加「来自 X」徽章）。
        case_keyed_bug_map: dict[str, list[BugTracking]] = defaultdict(list)
        if all_mirror_case_keys:
            linked_bug_rows = (
                self.db.query(BugTracking)
                .options(joinedload(BugTracking.dispatched_to))
                .filter(
                    BugTracking.zentao_linked_case_id.in_(
                        _case_key_sql_variants(all_mirror_case_keys)
                    ),
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
            expect_story = int(req.zentao_story_id) if req.zentao_story_id else None
            items: list[dict[str, Any]] = []
            existing_case_ids: set[str] = set()
            for local_case in req.test_cases or []:
                local_key = _case_key(local_case.zentao_case_id)
                existing_case_ids.add(local_key)
                bugs_payload = [
                    self.serialize_bug_brief(bug, minors, expect_story_id=expect_story)
                    for bug in case_bug_rows_map.get(str(local_case.id), [])
                ]
                seen_bug_ids = {b["id"] for b in bugs_payload}
                # Auto-linked Bug via testcase reverse mapping.
                for bug in case_keyed_bug_map.get(local_key, []):
                    if bug.id in seen_bug_ids:
                        continue
                    bugs_payload.append(self.serialize_bug_brief(bug, minors, auto_linked=True, expect_story_id=expect_story))
                    seen_bug_ids.add(bug.id)
                # 禅道校对：镜像里该用例是否已删除 / story 归属是否与本需求一致
                mirror = mirror_by_numeric.get(int(local_case.zentao_case_numeric_id or 0))
                mirror_story = int(mirror.zentao_story_id) if mirror and mirror.zentao_story_id else None
                items.append(
                    {
                        "id": local_case.id,
                        "zentao_case_id": local_case.zentao_case_id,
                        "zentao_case_url": local_case.zentao_case_url,
                        "bugs": bugs_payload,
                        "auto_linked": False,
                        "mirror_case_numeric_id": local_case.zentao_case_numeric_id,
                        "zentao_deleted": bool(mirror.deleted) if mirror else False,
                        "story_mismatch": bool(mirror_story and expect_story and mirror_story != expect_story),
                        "mirror_story_id": mirror_story,
                        # 审查工作台条目展示：标题/创建人/所属
                        "title": local_case.zentao_case_title,
                        "creator": local_case.zentao_creator_name,
                        "belongs": self._case_belongs_label(
                            product_id=local_case.zentao_product_id,
                            product_name=local_case.zentao_product_name,
                        ),
                    }
                )

            if req.zentao_story_id:
                for mirror in mirror_by_story.get(int(req.zentao_story_id), []):
                    case_key = _case_key(mirror.zentao_case_id)
                    if case_key and case_key in existing_case_ids:
                        continue
                    auto_bugs = [
                        self.serialize_bug_brief(bug, minors, auto_linked=True, expect_story_id=expect_story)
                        for bug in case_keyed_bug_map.get(case_key, [])
                    ]
                    items.append(
                        {
                            "id": -int(mirror.zentao_case_numeric_id),
                            "zentao_case_id": mirror.zentao_case_id,
                            "zentao_case_url": mirror.zentao_case_url,
                            "bugs": auto_bugs,
                            "auto_linked": True,
                            # 镜像行本就按 story 命中且过滤了已删除，校对必然一致
                            "zentao_deleted": False,
                            "story_mismatch": False,
                            "mirror_story_id": int(mirror.zentao_story_id) if mirror.zentao_story_id else None,
                            "mirror_case_numeric_id": mirror.zentao_case_numeric_id,
                            "title": mirror.title,
                            "creator": None,  # 镜像未同步创建人；前端仅在有值时展示

                            "belongs": self._case_belongs_label(
                                product_id=mirror.zentao_product_id,
                                product_name=mirror.zentao_product_name,
                                module_id=mirror.zentao_module_id,
                                module_name=mirror.zentao_module_name,
                            ),
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
        exclude_case_view: dict[int, list[dict[str, Any]]] | None = None,
    ) -> tuple[dict[int, list[dict[str, Any]]], dict[int, list[dict[str, Any]]]]:
        req_ids = [req.id for req in reqs]
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

        # 不按大版本过滤：跨大版本的同需求 Bug 一并归集，前端加来源徽章。
        if story_filter_terms:
            story_query = (
                self.db.query(BugTracking)
                .options(joinedload(BugTracking.dispatched_to))
                .filter(
                    or_(*story_filter_terms),
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
        req_story_map: dict[int, int | None] = {}
        for req in reqs:
            req_story_map[req.id] = int(req.zentao_story_id) if req.zentao_story_id else None
            if req.zentao_story_id:
                req_by_story[int(req.zentao_story_id)].append(req)

        result: dict[int, list[dict[str, Any]]] = defaultdict(list)
        seen_bug_ids: dict[int, set[int]] = defaultdict(set)

        # 用例栏（build_requirement_case_view）已经挂出的 Bug 不再归入自由
        # Bug，否则同一 Bug 在用例和自由 Bug 两处重复显示。
        if exclude_case_view:
            for req_id, case_items in exclude_case_view.items():
                for case_item in case_items:
                    for case_bug in case_item.get("bugs") or []:
                        seen_bug_ids[req_id].add(case_bug["id"])

        for bug in local_rows:
            req_id = int(bug.requirement_id or -1)
            if req_id <= 0:
                continue
            if bug.id in seen_bug_ids[req_id]:
                continue
            result[req_id].append(
                self.serialize_bug_brief(bug, minors, auto_linked=False, expect_story_id=req_story_map.get(req_id))
            )
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
                auto_linked_story_bug_map[req.id].append(
                    self.serialize_bug_brief(
                        bug, minors, auto_linked=True,
                        expect_story_id=int(req.zentao_story_id) if req.zentao_story_id else None,
                    )
                )
                seen_bug_ids[req.id].add(bug.id)

        for req_id, rows in auto_linked_story_bug_map.items():
            result[req_id].extend(rows)

        return result, auto_linked_story_bug_map

    # 任务状态展示优先级：进行中 > 未开始 > 已暂停 > 已完成 > 取消/关闭
    _TASK_STATUS_ORDER = {"doing": 0, "changed": 0, "wait": 1, "pause": 2, "done": 3, "cancel": 4, "closed": 5}

    def build_story_task_map(self, reqs: list[Requirement]) -> dict[int, list[dict[str, Any]]]:
        """需求 → 禅道 story 关联任务列表（来自任务镜像，含当前指派人）。

        排除两类：本系统为需求创建的测试子任务及其父任务（工作台已有专属
        标签/操作按钮），以及禅道的父容器任务（子任务已逐条列出，避免重复）。
        """
        story_ids = [int(req.zentao_story_id) for req in reqs if req.zentao_story_id]
        if not story_ids:
            return {req.id: [] for req in reqs}

        rows = (
            self.db.query(ZentaoTaskMirror)
            .filter(ZentaoTaskMirror.story.in_(story_ids))
            .order_by(ZentaoTaskMirror.task_id.asc())
            .all()
        )
        by_story: dict[int, list[ZentaoTaskMirror]] = defaultdict(list)
        for task in rows:
            if task.story:
                by_story[int(task.story)].append(task)

        result: dict[int, list[dict[str, Any]]] = {}
        for req in reqs:
            own_task_ids = {int(req.zentao_task_id or 0), int(getattr(req, "zentao_parent_task_id", None) or 0)}
            items: list[dict[str, Any]] = []
            for task in by_story.get(int(req.zentao_story_id or 0), []):
                if task.task_id in own_task_ids or task.is_parent:
                    continue
                items.append(
                    {
                        "task_id": task.task_id,
                        "name": task.name,
                        "status": task.status,
                        "type": task.type,
                        "assigned_to_name": task.assigned_to_realname or task.assigned_to or None,
                        # 完成者单独给：任务完成后 assignedTo 常被流转给下一环节的人
                        "finished_by_name": task.finished_by_realname or task.finished_by or None,
                    }
                )
            items.sort(key=lambda x: (self._TASK_STATUS_ORDER.get(x["status"], 9), x["task_id"]))
            result[req.id] = items
        return result

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

        if not story_filter_terms:
            return {req.id: [] for req in reqs}

        # 不按大版本过滤：跨大版本的同需求 Bug 也纳入测后归集证据。
        candidate_rows = (
            self.db.query(BugTracking)
            .options(joinedload(BugTracking.dispatched_to))
            .filter(
                or_(*story_filter_terms),
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
                result[req.id].append(
                    self.serialize_bug_brief(
                        bug, minors, auto_linked=True,
                        expect_story_id=int(req.zentao_story_id) if req.zentao_story_id else None,
                    )
                )
                seen_bug_ids[req.id].add(bug.id)

        return result
