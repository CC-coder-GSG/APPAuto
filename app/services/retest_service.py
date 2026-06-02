from __future__ import annotations

from datetime import datetime

from fastapi import HTTPException
from sqlalchemy import or_
from sqlalchemy.orm import Session, joinedload

from app.models import BugSourceType, BugTracking, Requirement, RequirementRetestRecord, User, Version, VersionType
from app.services.audit_service import audit
from app.services.sse_service import sse_publish
from app.services.workbench_link_service import WorkbenchLinkService
from app.utils.time_utils import local_now


class RetestService:
    def __init__(self, db: Session):
        self.db = db

    def get_workbench(
        self,
        current_user: User,
        *,
        major_version_id: int | None = None,
        mode: str = "version",
        software_id: int | None = None,
    ) -> list[dict]:
        q = (
            self.db.query(Requirement)
            .options(joinedload(Requirement.owner), joinedload(Requirement.test_cases), joinedload(Requirement.retester), joinedload(Requirement.major_version))
            .filter(
                Requirement.test_completed.is_(True),
                Requirement.owner_id.isnot(None),
                Requirement.owner_id != current_user.id,
            )
        )
        if mode == "version":
            if not major_version_id:
                return []
            q = q.filter(Requirement.major_version_id == major_version_id)
        elif mode == "all_pending":
            q = q.filter(Requirement.retest_completed.is_(False))
            if software_id:
                q = q.join(Version, Requirement.major_version_id == Version.id).filter(Version.software_id == software_id)
        else:
            raise HTTPException(status_code=400, detail="mode only supports version/all_pending")

        reqs = q.order_by(Requirement.id.asc()).all()

        minors = {v.id: v.version_no for v in self.db.query(Version).filter(Version.version_type == VersionType.MINOR).all()}
        req_ids = [r.id for r in reqs]
        case_ids = [c.id for r in reqs for c in r.test_cases]

        case_bug_rows = self.db.query(BugTracking).filter(
            BugTracking.requirement_id.in_(req_ids),
            BugTracking.source_type == BugSourceType.CASE,
            BugTracking.source_ref.in_([str(x) for x in case_ids] if case_ids else ["-1"]),
        ).all() if req_ids else []
        free_bug_rows = self.db.query(BugTracking).filter(
            BugTracking.requirement_id.in_(req_ids),
            BugTracking.source_type.in_([BugSourceType.MANUAL, BugSourceType.REQUIREMENT]),
        ).all() if req_ids else []
        retest_bug_rows = self.db.query(BugTracking).filter(
            BugTracking.requirement_id.in_(req_ids),
            BugTracking.source_type == BugSourceType.RETEST,
        ).all() if req_ids else []

        case_bug_map: dict[str, list[dict]] = {}
        for bug in case_bug_rows:
            case_bug_map.setdefault(bug.source_ref or "", []).append(
                {
                    "id": bug.id,
                    "bug_id": bug.bug_id,
                    "zentao_bug_url": bug.zentao_bug_url,
                    "zentao_bug_title": bug.zentao_bug_title,
                    "found_minor_version_no": minors.get(bug.found_minor_version_id, "未知"),
                    "is_retest_failed": bug.is_retest_failed,
                }
            )

        free_bug_map: dict[int, list[dict]] = {}
        for bug in free_bug_rows:
            free_bug_map.setdefault(bug.requirement_id or -1, []).append(
                {
                    "id": bug.id,
                    "bug_id": bug.bug_id,
                    "zentao_bug_url": bug.zentao_bug_url,
                    "zentao_bug_title": bug.zentao_bug_title,
                    "found_minor_version_no": minors.get(bug.found_minor_version_id, "未知"),
                    "is_retest_failed": bug.is_retest_failed,
                }
            )

        retest_bug_map: dict[int, list[dict]] = {}
        for bug in retest_bug_rows:
            retest_bug_map.setdefault(bug.requirement_id or -1, []).append(
                {
                    "id": bug.id,
                    "bug_id": bug.bug_id,
                    "zentao_bug_url": bug.zentao_bug_url,
                    "zentao_bug_title": bug.zentao_bug_title,
                    "found_minor_version_no": minors.get(bug.found_minor_version_id, "未知"),
                    "is_retest_failed": bug.is_retest_failed,
                    "closed": bool(bug.closed),
                }
            )

        return [
            {
                "id": r.id,
                "zentao_req_id": r.zentao_req_id,
                "title": r.title,
                "major_version_id": r.major_version_id,
                "major_version_name": r.major_version.version_no if r.major_version else "未知",
                "owner": r.owner.shown_name if r.owner else None,
                "retest_completed": r.retest_completed,
                "retest_passed": r.retest_passed,
                "retest_minor_version_id": r.retest_minor_version_id,
                "retested_by": r.retester.shown_name if r.retester else None,
                "test_cases": [{"id": c.id, "zentao_case_id": c.zentao_case_id, "zentao_case_url": c.zentao_case_url, "bugs": case_bug_map.get(str(c.id), [])} for c in r.test_cases],
                "free_bugs": free_bug_map.get(r.id, []),
                "retest_bugs": retest_bug_map.get(r.id, []),
            }
            for r in reqs
        ]

    def _has_open_problem(self, req: Requirement) -> bool:
        """
        该需求是否仍存在"未闭环的问题"——即可作为打回证据、且会阻止标记通过的依据。

        统一口径（前后端一致）：
        1. 被勾选「标记未修好」的旧 Bug（is_retest_failed）
        2. 未闭环的 RETEST 来源 Bug
        3. 未闭环的"测试完成后自动归集 Bug"（retest_evidence_bugs）
        已闭环的 Bug 视为已修复，不再算作证据。
        """
        has_failed_old = (
            self.db.query(BugTracking)
            .filter(BugTracking.requirement_id == req.id, BugTracking.is_retest_failed.is_(True))
            .first()
        )
        if has_failed_old:
            return True

        has_open_retest = (
            self.db.query(BugTracking)
            .filter(
                BugTracking.requirement_id == req.id,
                BugTracking.source_type == BugSourceType.RETEST,
                or_(BugTracking.closed.is_(False), BugTracking.closed.is_(None)),
            )
            .first()
        )
        if has_open_retest:
            return True

        # 测后自动归集 Bug（禅道侧在需求测试完成后新增的、归属到该需求的 Bug）
        link = WorkbenchLinkService(self.db)
        minors = link.minor_version_name_map()
        evidence = link.build_retest_evidence([req], minors).get(req.id, [])
        if any(not bug.get("closed") for bug in evidence):
            return True
        return False

    def _recompute_aggregate(self, req: Requirement) -> None:
        """
        基于剩余的 per-user 复测记录，刷新 Requirement 行上的共享聚合字段
        （供需求状态机 / 报表 / 看板沿用）。取最新一条记录作为聚合结论。
        """
        records = (
            self.db.query(RequirementRetestRecord)
            .filter(RequirementRetestRecord.requirement_id == req.id)
            .order_by(RequirementRetestRecord.updated_at.desc(), RequirementRetestRecord.id.desc())
            .all()
        )
        if records:
            latest = records[0]
            req.retest_completed = True
            req.retest_passed = bool(latest.passed)
            req.retest_minor_version_id = latest.minor_version_id
            req.retested_by_id = latest.user_id
            req.retested_at = latest.updated_at or local_now()
        else:
            req.retest_completed = False
            req.retest_passed = None
            req.retest_minor_version_id = None
            req.retested_by_id = None
            req.retested_at = None

    def submit_retest(
        self,
        requirement_id: int,
        *,
        retest_completed: bool,
        retest_passed: bool | None,
        retest_minor_version_id: int | None,
        current_user: User,
    ) -> dict:
        req = self.db.query(Requirement).filter(Requirement.id == requirement_id).first()
        if not req:
            raise HTTPException(status_code=404, detail="Requirement not found")
        if req.owner_id == current_user.id:
            raise HTTPException(status_code=403, detail="Self-tested requirement cannot be cross-retested by self")

        if retest_completed and retest_passed is False:
            if not self._has_open_problem(req):
                raise HTTPException(status_code=400, detail="打回无效：请至少勾选一个未修好的旧 Bug，或存在未闭环的复测/漏测 Bug 作为证据！")

        if retest_completed and retest_passed is True:
            if self._has_open_problem(req):
                raise HTTPException(
                    status_code=400,
                    detail="无法标记通过：该需求仍存在未修好的旧 Bug，或未闭环的复测/测后归集 Bug，请先处理后再标记通过。",
                )

        # 写入"当前用户"的复测记录（per-user），不再直接共享给所有人
        record = (
            self.db.query(RequirementRetestRecord)
            .filter(
                RequirementRetestRecord.requirement_id == req.id,
                RequirementRetestRecord.user_id == current_user.id,
            )
            .first()
        )
        if retest_completed:
            if not record:
                record = RequirementRetestRecord(requirement_id=req.id, user_id=current_user.id)
                self.db.add(record)
            record.passed = bool(retest_passed)
            record.minor_version_id = retest_minor_version_id
            record.updated_at = local_now()
        elif record:
            # 撤销当前用户的复测结论
            self.db.delete(record)
        self.db.flush()

        # 刷新共享聚合字段，供需求状态机 / 报表 / 看板沿用
        self._recompute_aggregate(req)
        self.db.commit()
        audit(self.db, action="retest.submit", target_type="requirement", actor_id=current_user.id, target_id=str(req.id), detail=f"passed={retest_passed if retest_completed else None},minor={retest_minor_version_id if retest_completed else None}")
        sse_publish(
            "retest_requirement_status_changed",
            {
                "requirement_id": req.id,
                "retest_completed": retest_completed,
                "retest_passed": retest_passed if retest_completed else None,
                "retested_by_id": current_user.id if retest_completed else None,
            },
            channels=["global"],
        )
        return {"message": "Retest status updated"}

    def build_retest_push_message(self, major_version_id: int, current_user: User) -> tuple[str, int]:
        # 基于"当前用户自己"的 per-user 复测记录汇总（不再读共享聚合字段）
        records = (
            self.db.query(RequirementRetestRecord)
            .join(Requirement, RequirementRetestRecord.requirement_id == Requirement.id)
            .options(
                joinedload(RequirementRetestRecord.requirement).joinedload(Requirement.owner),
                joinedload(RequirementRetestRecord.minor_version),
            )
            .filter(
                Requirement.major_version_id == major_version_id,
                RequirementRetestRecord.user_id == current_user.id,
            )
            .all()
        )
        if not records:
            raise HTTPException(status_code=400, detail="No retested requirements by current user")

        msg_lines = [f"### 复测结果专项通报 (复测人: @{current_user.shown_name})"]
        for rec in records:
            req = rec.requirement
            owner_name = req.owner.shown_name if req and req.owner else "未知"
            minor_ver = rec.minor_version.version_no if rec.minor_version else "未知"
            if rec.passed:
                msg_lines.append(f"> ✅ **[通过]** {req.zentao_req_id} (原测试: @{owner_name} | 验证发包: {minor_ver})")
            else:
                msg_lines.append(f"> ❌ **[打回]** <font color=\"warning\">{req.zentao_req_id}</font> (原测试: @{owner_name} | 验证发包: {minor_ver}) - *存在漏测或未修复问题！*")

        return "\n".join(msg_lines), len(records)
