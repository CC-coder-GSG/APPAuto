from __future__ import annotations

import logging
import time
from threading import Lock
from datetime import datetime

from fastapi import HTTPException
from sqlalchemy import or_
from sqlalchemy.orm import Session, joinedload
from app.services.zentao_task_status import serialize_task_story_change

from app.models import FinalTestRecord, Requirement, RequirementRetestRecord, User, Version, VersionType
from app.services.case_review_service import CaseReviewService
from app.services.workbench_link_service import WorkbenchLinkService, _load_matching

logger = logging.getLogger(__name__)

# 进程内节流：同一大版本的禅道任务状态同步最多每 N 秒一次，避免 SSE 频繁
# 刷新工作台时反复打禅道接口。
_TASK_STATUS_SYNC_TTL_SECONDS = 20
_task_status_sync_at: dict[int, float] = {}
_task_status_sync_running: set[int] = set()
_task_status_sync_lock = Lock()


class WorkbenchService:
    def __init__(self, db: Session):
        self.db = db
        self.link_service = WorkbenchLinkService(db)

    def _maybe_sync_task_status(self, major_version_id: int | None) -> bool:
        """按大版本节流地从禅道回写子任务状态/指派人（最佳努力，失败不影响加载）。"""
        if not major_version_id:
            return False
        now = time.monotonic()
        with _task_status_sync_lock:
            last = _task_status_sync_at.get(major_version_id, 0.0)
            if major_version_id in _task_status_sync_running or now - last < _TASK_STATUS_SYNC_TTL_SECONDS:
                return False
            _task_status_sync_running.add(major_version_id)
        try:
            from app.services.zentao_task_sync_service import ZentaoTaskSyncService

            ZentaoTaskSyncService(self.db).sync_tasks_status_for_major(major_version_id)
            from app.services.zentao_task_mirror_service import ZentaoTaskMirrorService
            ZentaoTaskMirrorService(self.db).sync_one_major(major_version_id)
            return True
        except Exception as exc:  # noqa: BLE001 — 禅道侧异常绝不阻断工作台加载
            self.db.rollback()
            logger.warning("workbench task status sync major %s failed: %s", major_version_id, exc)
            return False
        finally:
            with _task_status_sync_lock:
                _task_status_sync_at[major_version_id] = time.monotonic()
                _task_status_sync_running.discard(major_version_id)

    def refresh_my_task_status(self, *, current_user: User, major_version_id: int | None = None,
                               mode: str = "version", software_id: int | None = None) -> dict:
        """Explicit background refresh; the list GET never waits for Zentao."""
        query = self.db.query(Version.id).filter(Version.version_type == VersionType.MAJOR)
        if software_id:
            query = query.filter(Version.software_id == software_id)
        if mode == "version":
            query = query.filter(Version.id == major_version_id)
        else:
            from app.models.zentao_task_mirror import ZentaoTaskMirror
            account = (current_user.zentao_account or "").strip().lower()
            owned = self.db.query(Requirement.id).filter(
                Requirement.major_version_id == Version.id,
                Requirement.owner_id == current_user.id,
                Requirement.zentao_task_id.isnot(None),
            ).exists()
            assigned = self.db.query(ZentaoTaskMirror.task_id).filter(
                ZentaoTaskMirror.execution_id == Version.zentao_execution_id,
                or_(ZentaoTaskMirror.assignee_user_id == current_user.id,
                    ZentaoTaskMirror.assigned_to == account if account else False),
            ).exists()
            query = query.filter(or_(owned, assigned))
        # Materialize IDs before sync commits expire ORM state.
        results = [self._maybe_sync_task_status(mid) for (mid,) in query.distinct().all()]
        return {"refreshed": any(results)}

    def _latest_retest_passed_map(self, req_ids: list[int]) -> dict[int, bool]:
        """req_id → 最新一条复测记录的 passed（无记录则缺键）。"""
        if not req_ids:
            return {}
        rows = _load_matching(self.db.query(RequirementRetestRecord),
                              [(RequirementRetestRecord.requirement_id, set(req_ids))])
        rows.sort(key=lambda r: (r.updated_at or datetime.min, r.id))
        out: dict[int, bool] = {}
        for rec in rows:  # 升序遍历，后写覆盖 → 留下最新
            out[rec.requirement_id] = bool(rec.passed)
        return out

    def _retest_flags_for_req(
        self,
        r: Requirement,
        bug_dicts: list[dict],
        evidence_ids: set[int],
        latest_passed: bool | None,
    ) -> dict:
        """
        复测结论改版（2026-07）的展示层判定：给卡片上的 Bug dict 就地打
        「复测问题」标记，并汇总需求级结论（与 RetestService 同口径）：

        - 问题 = 复测激活的 Bug，或测后归集且提出人≠原测试人的 Bug；
        - 勾选「取消复测结论」的问题不再生效（retest_problem=False 但保留 kind 供置灰展示）；
        - 结论 failed > passed(有通过记录 / 问题全误报) > pending。
        """
        owner_account = ""
        if r.owner and r.owner.zentao_account:
            owner_account = r.owner.zentao_account.strip().lower()
        seen: set[int] = set()
        problems_any = 0
        active_count = 0
        fail_actors: list[str] = []
        for b in bug_dicts:
            kind = None
            if b.get("zentao_deleted") or not r.test_completed:
                continue
            if b.get("retest_activated") and b.get("retest_activated_req_id", r.id) == r.id:
                kind = "activated"
            elif b["id"] in evidence_ids:
                opener = (b.get("zentao_opened_by_account") or "").strip().lower()
                # 提出人可确认是原测试人本人 → 不算复测问题（自查自纠）
                if not (owner_account and opener and opener == owner_account):
                    kind = "collected"
            if not kind:
                continue
            b["retest_problem_kind"] = kind
            active = not b.get("retest_dismissed")
            b["retest_problem"] = active
            if b["id"] in seen:
                continue
            seen.add(b["id"])
            problems_any += 1
            if active:
                active_count += 1
                actor = b.get("retest_activated_by_name") if kind == "activated" else b.get("zentao_opened_by_name")
                if actor and actor not in fail_actors:
                    fail_actors.append(actor)
        if not r.test_completed:
            conclusion = "pending"
        elif active_count:
            conclusion = "failed"
        elif latest_passed is not None:
            conclusion = "passed" if latest_passed else "failed"  # 历史打回记录保留原义
        elif problems_any:
            conclusion = "passed"  # 出现过问题但全部标记误报 → 视为通过
        else:
            conclusion = "pending"
        return {
            "retest_conclusion": conclusion,
            "retest_active_problem_count": active_count,
            "retest_fail_actors": fail_actors,
        }

    def get_my_workbench(
        self,
        *,
        current_user: User,
        major_version_id: int | None = None,
        mode: str = "version",
        software_id: int | None = None,
    ) -> list[dict]:
        # 最终测试模式：所选大版本已开启 final_test 时，无视分配，返回该版本全部
        # 需求，且勾选状态改用当前用户独立的 FinalTestRecord。
        final_test_mode = False
        if mode == "version" and major_version_id:
            ft_flag = self.db.query(Version.final_test_enabled).filter(Version.id == major_version_id).first()
            final_test_mode = bool(ft_flag[0]) if ft_flag else False

        query = self.db.query(Requirement).options(
            joinedload(Requirement.owner),
            joinedload(Requirement.major_version),
            joinedload(Requirement.test_cases),
            joinedload(Requirement.test_notes_updated_by),
        )

        if final_test_mode:
            query = query.filter(Requirement.major_version_id == major_version_id)
        else:
            from app.models.zentao_task_mirror import ZentaoTaskMirror
            account = (current_user.zentao_account or "").strip().lower()
            own_tasks = self.db.query(ZentaoTaskMirror.task_id).join(
                Version, Version.zentao_execution_id == ZentaoTaskMirror.execution_id,
            ).filter(
                Version.id == Requirement.major_version_id,
                or_(ZentaoTaskMirror.task_id == Requirement.zentao_task_id,
                    ZentaoTaskMirror.story == Requirement.zentao_story_id),
                ZentaoTaskMirror.type == "test", ZentaoTaskMirror.is_parent == 0,
                ZentaoTaskMirror.status != "cancel",
                or_(ZentaoTaskMirror.assignee_user_id == current_user.id,
                    ZentaoTaskMirror.assigned_to == account if account else False),
            ).exists()
            query = query.filter(or_(Requirement.owner_id == current_user.id, own_tasks))
            if mode == "version" and major_version_id:
                query = query.filter(Requirement.major_version_id == major_version_id)
            elif mode == "all_pending":
                pending_tasks = self.db.query(ZentaoTaskMirror.task_id).join(
                    Version, Version.zentao_execution_id == ZentaoTaskMirror.execution_id,
                ).filter(Version.id == Requirement.major_version_id,
                         or_(ZentaoTaskMirror.task_id == Requirement.zentao_task_id,
                             ZentaoTaskMirror.story == Requirement.zentao_story_id),
                         ZentaoTaskMirror.type == "test", ZentaoTaskMirror.is_parent == 0,
                         or_(ZentaoTaskMirror.status.notin_(["done", "closed", "cancel"]),
                             ZentaoTaskMirror.status.is_(None))).exists()
                query = query.filter(or_(Requirement.test_completed.is_(False), Requirement.task_story_change_pending.is_(True), pending_tasks))
            if software_id:
                query = query.join(Version, Requirement.major_version_id == Version.id).filter(Version.software_id == software_id)

        reqs = query.order_by(Requirement.id.desc()).all()
        if not reqs:
            return []
        from app.services.requirement_task_service import RequirementTaskService
        task_service = RequirementTaskService(self.db)
        task_rows = task_service.rows_by_requirement(reqs)
        if any([task_service.reconcile(r, task_rows[r.id]) for r in reqs]):
            req_ids = {r.id for r in reqs}
            self.db.commit()
            # commit expires every loaded row. Reload eagerly in bounded batches
            # instead of re-fetching requirements, cases and tasks one by one.
            reqs = sorted(_load_matching(self.db.query(Requirement).options(
                joinedload(Requirement.owner), joinedload(Requirement.major_version),
                joinedload(Requirement.test_cases), joinedload(Requirement.test_notes_updated_by),
            ), [(Requirement.id, req_ids)]), key=lambda r: r.id, reverse=True)
            task_rows = task_service.rows_by_requirement(reqs)
        test_task_map = task_service.describe(reqs, current_user, task_rows=task_rows)
        links = WorkbenchLinkService(self.db, requirements=reqs)

        # 当前用户在该版本的最终测试勾选状态
        ft_record_map: dict[int, FinalTestRecord] = {}
        ft_people = {}
        if final_test_mode and reqs:
            ft_records = _load_matching(self.db.query(FinalTestRecord).options(joinedload(FinalTestRecord.user)), [(FinalTestRecord.requirement_id, {r.id for r in reqs})])
            ft_record_map = {rec.requirement_id: rec for rec in ft_records if rec.user_id == current_user.id}
            for rec in sorted(ft_records, key=lambda item: item.user_id):
                if rec.test_completed or rec.testing:
                    ft_people.setdefault(rec.requirement_id, []).append({
                        'user_id':rec.user_id, 'user_name':rec.user.shown_name if rec.user else '未知',
                        'status':'completed' if rec.test_completed else 'testing'})

        def _case_done(r: Requirement) -> bool:
            if final_test_mode:
                rec = ft_record_map.get(r.id)
                return bool(rec.case_completed) if rec else False
            return r.case_completed

        def _test_done(r: Requirement) -> bool:
            if final_test_mode:
                rec = ft_record_map.get(r.id)
                return bool(rec.test_completed) if rec else False
            return r.test_completed

        minors = links.minor_version_name_map()
        case_view_map = links.build_requirement_case_view(reqs, minors)
        # 用例审查标签（与审查工作台联动展示）
        CaseReviewService(self.db).attach_reviews_to_case_view(case_view_map)
        free_bug_map, auto_story_bug_map = links.build_requirement_free_bug_view(
            reqs,
            minors,
            include_retest=False,
            exclude_case_view=case_view_map,
        )
        story_task_map = links.build_story_task_map(reqs)

        # 复测结论改版：给卡片 Bug 打「复测问题」标记 + 需求级结论。
        # 「归集问题」只对已测试完成的需求判定（口径=测后归集证据）。
        completed_reqs = [r for r in reqs if r.test_completed and r.test_completed_at]
        retest_evidence_ids: dict[int, set[int]] = {}
        if completed_reqs:
            for rid, bugs in links.build_retest_evidence(completed_reqs, minors).items():
                retest_evidence_ids[rid] = {b["id"] for b in bugs}
        latest_passed_map = self._latest_retest_passed_map([r.id for r in reqs])
        retest_flag_map: dict[int, dict] = {}
        for r in reqs:
            bug_dicts = [b for item in case_view_map.get(r.id, []) for b in (item.get("bugs") or [])]
            bug_dicts += list(free_bug_map.get(r.id, []))
            retest_flag_map[r.id] = self._retest_flags_for_req(
                r, bug_dicts, retest_evidence_ids.get(r.id, set()), latest_passed_map.get(r.id)
            )

        # 当前用户的禅道账号（用于判定子任务是否指派给本人）。
        my_account = (current_user.zentao_account or "").strip().lower()

        def _task_assigned_to_me(r: Requirement) -> bool:
            if not r.zentao_task_id:
                return False
            assignee = (r.zentao_task_assigned_to or "").strip().lower()
            # 未知指派人时保守视为“非本人”，避免误放开始/完成禅道任务的权限。
            return bool(my_account) and assignee == my_account

        return [
            {
                "id": r.id,
                "zentao_req_id": r.zentao_req_id,
                "title": r.title,
                "final_test": final_test_mode,
                "final_test_people": ft_people.get(r.id, []),
                "testing": bool(ft_record_map.get(r.id) and ft_record_map[r.id].testing and not ft_record_map[r.id].test_completed),
                "test_tasks": test_task_map.get(r.id, []),
                "has_multiple_test_tasks": task_service.task_count(r, task_rows[r.id]) > 1,
                "linked_test_task_total": task_service.task_count(r, task_rows[r.id]),
                "linked_test_task_done": sum(t.status in task_service.DONE for t in task_rows[r.id]) + int(task_service.missing_primary(r, task_rows[r.id]) and r.zentao_task_status_cache in task_service.DONE),
                "all_linked_test_tasks_completed": bool(r.test_completed),
                "case_completed": _case_done(r),
                "test_completed": _test_done(r),
                "major_version_id": r.major_version_id,
                "major_version_name": r.major_version.version_no if r.major_version else "",
                "zentao_story_id": r.zentao_story_id,
                "zentao_execution_id": r.major_version.zentao_execution_id if r.major_version else None,
                # 禅道子任务关联信息：前端据此显示「禅道子任务 #」标签 / 「开始」按钮，
                # 缺失会导致已建任务的需求错误显示为「未关联禅道任务」。
                "zentao_task_id": r.zentao_task_id,
                "zentao_task_status": r.zentao_task_status_cache,
                **serialize_task_story_change(r),
                "task_started_at": r.task_started_at.isoformat() if r.task_started_at else None,
                "estimated_test_hours": r.estimated_test_hours,
                "task_has_time_tracking": bool(
                    r.task_started_at
                    or float(r.task_consumed_accum or 0.0) > 0
                    or float(r.task_efforts_submitted or 0.0) > 0
                ),
                # 子任务指派人账号 + 是否指派给当前用户（前端据此决定是否显示
                # 开始/预计用时、以及勾选完成时是否联动禅道完成任务）。
                "zentao_task_assigned_to": r.zentao_task_assigned_to,
                "task_assigned_to_me": _task_assigned_to_me(r),
                # 需求 story 关联的禅道任务（标题旁标签展示，含当前指派人）
                "story_tasks": story_task_map.get(r.id, []),
                # 复测结论（pending/passed/failed）+ 未通过归属人（醒目标签展示）
                **retest_flag_map.get(r.id, {}),
                "test_notes": r.test_notes,
                "test_notes_html": r.test_notes_html,
                "test_notes_updated_at": r.test_notes_updated_at.isoformat() if r.test_notes_updated_at else None,
                "test_notes_updated_by_name": r.test_notes_updated_by.shown_name if r.test_notes_updated_by else None,
                "test_cases": case_view_map.get(r.id, []),
                "free_bugs": free_bug_map.get(r.id, []),
                "auto_linked_case_count": sum(1 for c in case_view_map.get(r.id, []) if c.get("auto_linked")),
                "auto_linked_bug_count": len(auto_story_bug_map.get(r.id, [])),
            }
            for r in reqs
        ]

    def get_review_workbench(
        self,
        *,
        major_version_id: int | None = None,
        software_id: int | None = None,
    ) -> list[dict]:
        """审查工作台：所选大版本的全部需求（不按负责人过滤），用于全员审查用例。

        与需求工作台同源的用例视图（含审查标签），但不含 Bug 列表与完成勾选。
        """
        if not major_version_id:
            return []
        self._maybe_sync_task_status(major_version_id)
        query = (
            self.db.query(Requirement)
            .options(
                joinedload(Requirement.owner),
                joinedload(Requirement.major_version),
                joinedload(Requirement.test_cases),
                joinedload(Requirement.test_notes_updated_by),
            )
            .filter(Requirement.major_version_id == major_version_id)
        )
        if software_id:
            query = query.join(Version, Requirement.major_version_id == Version.id).filter(Version.software_id == software_id)
        reqs = query.order_by(Requirement.id.desc()).all()

        minors = self.link_service.minor_version_name_map()
        case_view_map = self.link_service.build_requirement_case_view(reqs, minors)
        CaseReviewService(self.db).attach_reviews_to_case_view(case_view_map)
        # 审查台不展示 Bug：置空瘦身载荷（视图构建复用需求工作台逻辑）
        for cases in case_view_map.values():
            for c in cases:
                c["bugs"] = []

        return [
            {
                "id": r.id,
                "zentao_req_id": r.zentao_req_id,
                "title": r.title,
                "major_version_id": r.major_version_id,
                "major_version_name": r.major_version.version_no if r.major_version else "",
                "owner_id": r.owner_id,
                "owner_name": r.owner.shown_name if r.owner else "未分配",
                "zentao_story_id": r.zentao_story_id,
                "zentao_task_id": r.zentao_task_id,
                "zentao_task_status": r.zentao_task_status_cache,
                **serialize_task_story_change(r),
                "zentao_task_assigned_to": r.zentao_task_assigned_to,
                "test_notes": r.test_notes,
                "test_notes_html": r.test_notes_html,
                "test_notes_updated_at": r.test_notes_updated_at.isoformat() if r.test_notes_updated_at else None,
                "test_notes_updated_by_name": r.test_notes_updated_by.shown_name if r.test_notes_updated_by else None,
                "test_cases": case_view_map.get(r.id, []),
            }
            for r in reqs
        ]

    def get_retest_workbench(
        self,
        *,
        current_user: User,
        major_version_id: int | None = None,
        mode: str = "version",
        software_id: int | None = None,
    ) -> list[dict]:
        query = (
            self.db.query(Requirement)
            .options(
                joinedload(Requirement.owner),
                joinedload(Requirement.test_cases),
                joinedload(Requirement.retester),
                joinedload(Requirement.major_version),
                joinedload(Requirement.test_notes_updated_by),
            )
            .filter(
                Requirement.test_completed.is_(True),
                Requirement.owner_id.isnot(None),
                Requirement.owner_id != current_user.id,
            )
        )

        if mode == "version":
            if not major_version_id:
                return []
            query = query.filter(Requirement.major_version_id == major_version_id)
        elif mode == "all_pending":
            # 复测状态按用户独立：这里只过滤"当前用户尚未复测"的需求
            retested_by_me = (
                self.db.query(RequirementRetestRecord.requirement_id)
                .filter(RequirementRetestRecord.user_id == current_user.id)
                .subquery()
            )
            query = query.filter(Requirement.id.notin_(retested_by_me))
            if software_id:
                query = query.join(Version, Requirement.major_version_id == Version.id).filter(Version.software_id == software_id)
        else:
            raise HTTPException(status_code=400, detail="mode only supports version/all_pending")

        reqs = query.order_by(Requirement.id.asc()).all()

        if not reqs:
            return []

        # 拉取这批需求的全部 per-user 复测记录，构建"我的复测状态"和"已复测人"标签
        req_ids = [r.id for r in reqs]
        retest_records_map: dict[int, list[dict]] = {}
        my_record_map: dict[int, RequirementRetestRecord] = {}
        records_by_req: dict[int, list[RequirementRetestRecord]] = {}
        if req_ids:
            records = _load_matching(
                self.db.query(RequirementRetestRecord).options(joinedload(RequirementRetestRecord.user)),
                [(RequirementRetestRecord.requirement_id, set(req_ids))],
            )
            for rec in records:
                records_by_req.setdefault(rec.requirement_id, []).append(rec)
                retest_records_map.setdefault(rec.requirement_id, []).append({
                    "user_id": rec.user_id,
                    "user_name": rec.user.shown_name if rec.user else "未知",
                    "passed": bool(rec.passed),
                    "is_me": rec.user_id == current_user.id,
                })
                if rec.user_id == current_user.id:
                    my_record_map[rec.requirement_id] = rec

        # A fresh request-scoped service shares mirror/Bug rows across all views.
        link = WorkbenchLinkService(self.db, requirements=reqs)
        minors = link.minor_version_name_map()
        case_view_map = link.build_requirement_case_view(reqs, minors)
        free_bug_map, _ = link.build_requirement_free_bug_view(
            reqs, minors, include_retest=False, exclude_case_view=case_view_map
        )
        retest_bug_map, _ = link.build_requirement_free_bug_view(reqs, minors, include_retest=True)
        retest_evidence_map = link.build_retest_evidence(reqs, minors)
        story_task_map = link.build_story_task_map(reqs)
        for records in records_by_req.values():
            records.sort(key=lambda rec: (rec.updated_at or datetime.min, rec.id), reverse=True)
        latest_passed_map = {rid: bool(records[0].passed) for rid, records in records_by_req.items()}

        result = []
        for r in reqs:
            evidence = retest_evidence_map.get(r.id, [])
            existing_retest_ids = {b["id"] for b in retest_bug_map.get(r.id, [])}
            existing_free_ids = {b["id"] for b in free_bug_map.get(r.id, [])}
            existing_case_bug_ids = {
                b["id"]
                for case in case_view_map.get(r.id, [])
                for b in (case.get("bugs") or [])
            }
            # Only surface evidence that isn't already shown elsewhere on the
            # card to avoid duplicate chips.
            filtered_evidence = [
                bug for bug in evidence
                if bug["id"] not in existing_retest_ids
                and bug["id"] not in existing_free_ids
                and bug["id"] not in existing_case_bug_ids
            ]
            # Activations can belong here even when the Bug's original
            # requirement/story points elsewhere or its opened time is missing.
            visible_ids = existing_retest_ids | existing_free_ids | existing_case_bug_ids | {b["id"] for b in filtered_evidence}
            for bug in link.activated_bug_rows(r.id):
                if bug.id not in visible_ids and not bug.zentao_deleted:
                    filtered_evidence.append(link.serialize_bug_brief(bug, minors, expect_story_id=r.zentao_story_id))
                    visible_ids.add(bug.id)
            my_record = my_record_map.get(r.id)

            # 复测结论改版：给卡片各区块的 Bug 打「复测问题」标记 + 需求级结论
            card_bug_dicts = (
                [b for case in case_view_map.get(r.id, []) for b in (case.get("bugs") or [])]
                + list(free_bug_map.get(r.id, []))
                + list(retest_bug_map.get(r.id, []))
                + filtered_evidence
            )
            flags = self._retest_flags_for_req(
                r, card_bug_dicts, {b["id"] for b in evidence}, latest_passed_map.get(r.id)
            )
            result.append({
                **flags,
                "id": r.id,
                "zentao_req_id": r.zentao_req_id,
                "title": r.title,
                "major_version_id": r.major_version_id,
                "major_version_name": r.major_version.version_no if r.major_version else "未知",
                "owner": r.owner.shown_name if r.owner else None,
                # 共享聚合字段（保留给报表/状态机；前端展示改用 my_* / retest_records）
                "retest_completed": r.retest_completed,
                "retest_passed": r.retest_passed,
                "retest_minor_version_id": r.retest_minor_version_id,
                "retested_by": r.retester.shown_name if r.retester else None,
                # 当前用户自己的复测状态（前端据此显示通过/打回/未提交 + 删除线）
                "my_retest_completed": my_record is not None,
                "my_retest_passed": bool(my_record.passed) if my_record else None,
                # 所有已复测的人（用于标签展示）
                "retest_records": retest_records_map.get(r.id, []),
                "zentao_story_id": r.zentao_story_id,
                "test_completed_at": r.test_completed_at.isoformat() if r.test_completed_at else None,
                # 需求 story 关联的禅道任务（标题旁标签展示，含当前指派人）
                "story_tasks": story_task_map.get(r.id, []),
                # 原测试人填写的测试要点（复测人只读参考，不可编辑）
                "test_notes": r.test_notes,
                "test_notes_html": r.test_notes_html,
                "test_notes_updated_at": r.test_notes_updated_at.isoformat() if r.test_notes_updated_at else None,
                "test_notes_updated_by_name": r.test_notes_updated_by.shown_name if r.test_notes_updated_by else None,
                "test_cases": case_view_map.get(r.id, []),
                "free_bugs": free_bug_map.get(r.id, []),
                "retest_bugs": retest_bug_map.get(r.id, []),
                "retest_evidence_bugs": filtered_evidence,
                "auto_linked_case_count": sum(1 for c in case_view_map.get(r.id, []) if c.get("auto_linked")),
                "auto_linked_evidence_count": len(filtered_evidence),
            })
        return result
