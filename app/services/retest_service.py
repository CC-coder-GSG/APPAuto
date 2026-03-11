from __future__ import annotations

from datetime import datetime

from fastapi import HTTPException
from sqlalchemy.orm import Session, joinedload

from app.models import BugSourceType, BugTracking, Requirement, User, Version, VersionType
from app.services.audit_service import audit


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
            BugTracking.source_type == BugSourceType.MANUAL,
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
                    "found_minor_version_no": minors.get(bug.found_minor_version_id, "未知"),
                    "is_retest_failed": bug.is_retest_failed,
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
                "test_cases": [{"id": c.id, "zentao_case_id": c.zentao_case_id, "bugs": case_bug_map.get(str(c.id), [])} for c in r.test_cases],
                "free_bugs": free_bug_map.get(r.id, []),
                "retest_bugs": retest_bug_map.get(r.id, []),
            }
            for r in reqs
        ]

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
            has_failed_old = self.db.query(BugTracking).filter(BugTracking.requirement_id == requirement_id, BugTracking.is_retest_failed.is_(True)).first()
            has_new_retest = self.db.query(BugTracking).filter(BugTracking.requirement_id == requirement_id, BugTracking.source_type == BugSourceType.RETEST).first()
            if not has_failed_old and not has_new_retest:
                raise HTTPException(status_code=400, detail="打回无效：请至少勾选一个未修好的旧 Bug，或新增一个漏测 Bug 作为证据！")

        req.retest_completed = retest_completed
        req.retest_passed = retest_passed if retest_completed else None
        req.retest_minor_version_id = retest_minor_version_id if retest_completed else None
        req.retested_by_id = current_user.id if retest_completed else None
        req.retested_at = datetime.utcnow() if retest_completed else None
        self.db.commit()
        audit(self.db, action="retest.submit", target_type="requirement", actor_id=current_user.id, target_id=str(req.id), detail=f"passed={req.retest_passed},minor={req.retest_minor_version_id}")
        return {"message": "Retest status updated"}

    def build_retest_push_message(self, major_version_id: int, current_user: User) -> tuple[str, int]:
        rows = (
            self.db.query(Requirement)
            .options(joinedload(Requirement.owner), joinedload(Requirement.retest_minor_version))
            .filter(
                Requirement.major_version_id == major_version_id,
                Requirement.retest_completed.is_(True),
                Requirement.retested_by_id == current_user.id,
            )
            .all()
        )
        if not rows:
            raise HTTPException(status_code=400, detail="No retested requirements by current user")

        msg_lines = [f"### 复测结果专项通报 (复测人: @{current_user.shown_name})"]
        for row in rows:
            owner_name = row.owner.shown_name if row.owner else "未知"
            minor_ver = row.retest_minor_version.version_no if row.retest_minor_version else "未知"
            if row.retest_passed:
                msg_lines.append(f"> ✅ **[通过]** {row.zentao_req_id} (原测试: @{owner_name} | 验证发包: {minor_ver})")
            else:
                msg_lines.append(f"> ❌ **[打回]** <font color=\"warning\">{row.zentao_req_id}</font> (原测试: @{owner_name} | 验证发包: {minor_ver}) - *存在漏测或未修复问题！*")

        return "\n".join(msg_lines), len(rows)
