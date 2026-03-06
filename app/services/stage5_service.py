from __future__ import annotations

from datetime import datetime

from fastapi import HTTPException
from sqlalchemy.orm import Session, joinedload

from app.models import BugSourceType, BugStage5Record, BugTracking, Requirement, TestCase, User, Version
from app.services.audit_service import audit


class Stage5Service:
    def __init__(self, db: Session):
        self.db = db

    def overview(self, major_version_id: int, current_user: User) -> dict:
        reqs = (
            self.db.query(Requirement)
            .options(joinedload(Requirement.test_cases), joinedload(Requirement.test_executions))
            .filter(Requirement.major_version_id == major_version_id)
            .all()
        )
        bugs = (
            self.db.query(BugTracking)
            .options(
                joinedload(BugTracking.stage5_records).joinedload(BugStage5Record.user),
                joinedload(BugTracking.stage5_records).joinedload(BugStage5Record.minor_version),
                joinedload(BugTracking.dispatched_to),
            )
            .filter(BugTracking.major_version_id == major_version_id)
            .all()
        )

        bug_pool = []
        for bug in bugs:
            other_records = []
            my_record = None
            for record in bug.stage5_records:
                if record.user_id != current_user.id:
                    other_records.append(
                        {
                            "username": record.user.username,
                            "minor_version_no": record.minor_version.version_no if record.minor_version else "未知",
                            "test_done": record.test_done,
                            "resolution": record.resolution,
                        }
                    )
                else:
                    my_record = record

            bug_pool.append(
                {
                    "id": bug.id,
                    "bug_id": bug.bug_id,
                    "source_type": bug.source_type.value,
                    "source_ref": bug.source_ref,
                    "requirement_id": bug.requirement_id,
                    "found_minor_version_id": bug.found_minor_version_id,
                    "fixed_minor_version_id": bug.fixed_minor_version_id,
                    "closed": bug.closed,
                    "my_test_done": my_record.test_done if my_record else False,
                    "my_resolution": my_record.resolution if my_record else "fixed",
                    "other_records": other_records,
                    "dispatched_to_name": bug.dispatched_to.username if bug.dispatched_to else None,
                    "is_retest_failed": getattr(bug, "is_retest_failed", False),
                }
            )

        return {
            "major_version_id": major_version_id,
            "requirements": [
                {
                    "id": r.id,
                    "zentao_req_id": r.zentao_req_id,
                    "title": r.title,
                    "case_ids": [c.zentao_case_id for c in r.test_cases],
                    "history_bug_ids": [e.bug_id for e in r.test_executions if e.bug_id],
                }
                for r in reqs
            ],
            "bug_pool": bug_pool,
        }

    def search_options(self, major_version_id: int) -> dict:
        reqs = self.db.query(Requirement).filter(Requirement.major_version_id == major_version_id).all()
        cases = self.db.query(TestCase).join(Requirement).filter(Requirement.major_version_id == major_version_id).all()
        legacy_bugs = self.db.query(BugTracking).filter(BugTracking.major_version_id == major_version_id).all()
        return {
            "reqs": [{"id": r.id, "label": f"{r.zentao_req_id} {r.title}"} for r in reqs],
            "cases": [{"id": c.id, "req_id": c.requirement_id, "label": c.zentao_case_id} for c in cases],
            "bugs": [{"id": b.id, "req_id": b.requirement_id, "label": b.bug_id} for b in legacy_bugs],
        }

    def submit_result(self, bug_track_id: int, *, minor_version_id: int, test_done: bool, newly_found_bug_id: str | None, resolution: str, current_user: User) -> dict:
        bug = self.db.query(BugTracking).filter(BugTracking.id == bug_track_id).first()
        if not bug:
            raise HTTPException(status_code=404, detail="Bug tracking item not found")

        old_resolution = bug.resolution
        record = self.db.query(BugStage5Record).filter(BugStage5Record.bug_tracking_id == bug_track_id, BugStage5Record.user_id == current_user.id).first()
        if not record:
            record = BugStage5Record(bug_tracking_id=bug_track_id, user_id=current_user.id)
            self.db.add(record)
        record.minor_version_id = minor_version_id
        record.test_done = test_done
        record.newly_found_bug_id = newly_found_bug_id
        record.resolution = resolution
        record.updated_at = datetime.utcnow()
        self.db.commit()

        all_records = self.db.query(BugStage5Record).filter(BugStage5Record.bug_tracking_id == bug_track_id).all()
        any_closed = any(r.test_done for r in all_records)
        bug.resolution = resolution
        bug.fixed_minor_version_id = minor_version_id if any_closed else None
        bug.closed = any_closed
        bug.closed_by_id = current_user.id if any_closed else None

        notice = None
        if test_done and old_resolution != resolution:
            res_zh_map = {"fixed": "✅修复通过", "false_alarm": "⚠️误报", "rejected": "⛔拒绝修复"}
            if resolution in ["false_alarm", "rejected"] or old_resolution in ["false_alarm", "rejected"]:
                notice = f"📢 **Bug 状态流转通知**\n> 缺陷 **{bug.bug_id}** 的处理状态被 @{current_user.username} 更新为：**{res_zh_map.get(resolution, resolution)}** (位于发包: 🏷️{minor_version_id})"

        created_bug_ids: list[str] = []
        if newly_found_bug_id:
            new_bugs = [b.strip() for b in newly_found_bug_id.split(',') if b.strip()]
            for nb in new_bugs:
                if not self.db.query(BugTracking).filter(BugTracking.bug_id == nb).first():
                    self.db.add(
                        BugTracking(
                            major_version_id=bug.major_version_id,
                            requirement_id=bug.requirement_id,
                            source_type=BugSourceType.LEGACY_BUG,
                            source_ref=bug.bug_id,
                            bug_id=nb,
                            found_minor_version_id=minor_version_id,
                            created_by_id=current_user.id,
                            dispatched_to_id=current_user.id,
                        )
                    )
                    created_bug_ids.append(nb)

        self.db.commit()
        audit(self.db, action="stage5.submit_result", target_type="bug", actor_id=current_user.id, target_id=str(bug.id), detail=f"closed={bug.closed},resolution={resolution},new={','.join(created_bug_ids)}")
        return {"message": "Stage5 result updated", "notice": notice, "created_bug_ids": created_bug_ids}

    def add_issue(self, *, major_version_id: int, requirement_id: int | None, source_type: BugSourceType, source_ref: str | None, bug_id: str, minor_version_id: int, current_user: User) -> dict:
        if self.db.query(BugTracking).filter(BugTracking.bug_id == bug_id).first():
            raise HTTPException(status_code=400, detail=f"添加失败：Bug 编号 {bug_id} 已经存在！")
        item = BugTracking(
            major_version_id=major_version_id,
            requirement_id=requirement_id,
            source_type=source_type,
            source_ref=source_ref,
            bug_id=bug_id,
            found_minor_version_id=minor_version_id,
            created_by_id=current_user.id,
        )
        self.db.add(item)
        self.db.commit()
        self.db.refresh(item)
        audit(self.db, action="stage5.add_issue", target_type="bug", actor_id=current_user.id, target_id=str(item.id), detail=item.bug_id)
        return {"id": item.id, "message": "Issue added"}

    def build_stage5_push_message(self, major_version_id: int, minor_version_id: int) -> tuple[str, int]:
        bugs = self.db.query(BugTracking).filter(BugTracking.major_version_id == major_version_id).all()
        remaining = len([b for b in bugs if not (b.closed and b.fixed_minor_version_id == minor_version_id)])
        return f"整体测试进度推送：剩余未闭环 {remaining}", remaining
