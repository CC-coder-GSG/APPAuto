from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.orm import Session, joinedload

from app.models import FinalTestRecord, Requirement, RequirementRetestRecord, User, Version
from app.services.workbench_link_service import WorkbenchLinkService


class WorkbenchService:
    def __init__(self, db: Session):
        self.db = db
        self.link_service = WorkbenchLinkService(db)

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
            joinedload(Requirement.major_version),
            joinedload(Requirement.test_cases),
            joinedload(Requirement.test_notes_updated_by),
        )

        if final_test_mode:
            query = query.filter(Requirement.major_version_id == major_version_id)
        else:
            query = query.filter(Requirement.owner_id == current_user.id)
            if mode == "version" and major_version_id:
                query = query.filter(Requirement.major_version_id == major_version_id)
            elif mode == "all_pending":
                query = query.filter(Requirement.test_completed.is_(False))
            if software_id:
                query = query.join(Version, Requirement.major_version_id == Version.id).filter(Version.software_id == software_id)

        reqs = query.order_by(Requirement.id.desc()).all()

        # 当前用户在该版本的最终测试勾选状态
        ft_record_map: dict[int, FinalTestRecord] = {}
        if final_test_mode and reqs:
            ft_records = (
                self.db.query(FinalTestRecord)
                .filter(
                    FinalTestRecord.user_id == current_user.id,
                    FinalTestRecord.requirement_id.in_([r.id for r in reqs]),
                )
                .all()
            )
            ft_record_map = {rec.requirement_id: rec for rec in ft_records}

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

        minors = self.link_service.minor_version_name_map()
        case_view_map = self.link_service.build_requirement_case_view(reqs, minors)
        free_bug_map, auto_story_bug_map = self.link_service.build_requirement_free_bug_view(
            reqs,
            minors,
            include_retest=False,
        )

        return [
            {
                "id": r.id,
                "zentao_req_id": r.zentao_req_id,
                "title": r.title,
                "final_test": final_test_mode,
                "case_completed": _case_done(r),
                "test_completed": _test_done(r),
                "major_version_id": r.major_version_id,
                "major_version_name": r.major_version.version_no if r.major_version else "",
                "zentao_story_id": r.zentao_story_id,
                "test_notes": r.test_notes,
                "test_notes_updated_at": r.test_notes_updated_at.isoformat() if r.test_notes_updated_at else None,
                "test_notes_updated_by_name": r.test_notes_updated_by.shown_name if r.test_notes_updated_by else None,
                "test_cases": case_view_map.get(r.id, []),
                "free_bugs": free_bug_map.get(r.id, []),
                "auto_linked_case_count": sum(1 for c in case_view_map.get(r.id, []) if c.get("auto_linked")),
                "auto_linked_bug_count": len(auto_story_bug_map.get(r.id, [])),
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

        # 拉取这批需求的全部 per-user 复测记录，构建"我的复测状态"和"已复测人"标签
        req_ids = [r.id for r in reqs]
        retest_records_map: dict[int, list[dict]] = {}
        my_record_map: dict[int, RequirementRetestRecord] = {}
        if req_ids:
            records = (
                self.db.query(RequirementRetestRecord)
                .options(joinedload(RequirementRetestRecord.user))
                .filter(RequirementRetestRecord.requirement_id.in_(req_ids))
                .all()
            )
            for rec in records:
                retest_records_map.setdefault(rec.requirement_id, []).append({
                    "user_id": rec.user_id,
                    "user_name": rec.user.shown_name if rec.user else "未知",
                    "passed": bool(rec.passed),
                    "is_me": rec.user_id == current_user.id,
                })
                if rec.user_id == current_user.id:
                    my_record_map[rec.requirement_id] = rec

        minors = self.link_service.minor_version_name_map()
        case_view_map = self.link_service.build_requirement_case_view(reqs, minors)
        free_bug_map, _ = self.link_service.build_requirement_free_bug_view(reqs, minors, include_retest=False)
        retest_bug_map, _ = self.link_service.build_requirement_free_bug_view(reqs, minors, include_retest=True)
        retest_evidence_map = self.link_service.build_retest_evidence(reqs, minors)

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
            my_record = my_record_map.get(r.id)
            result.append({
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
                "test_cases": case_view_map.get(r.id, []),
                "free_bugs": free_bug_map.get(r.id, []),
                "retest_bugs": retest_bug_map.get(r.id, []),
                "retest_evidence_bugs": filtered_evidence,
                "auto_linked_case_count": sum(1 for c in case_view_map.get(r.id, []) if c.get("auto_linked")),
                "auto_linked_evidence_count": len(filtered_evidence),
            })
        return result
