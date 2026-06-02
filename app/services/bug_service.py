from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.orm import Session, joinedload

from app.models import BugSourceType, BugStage5Record, BugTracking, Requirement, User, Version
from app.services.audit_service import audit
from app.services.sse_service import sse_publish


class BugService:
    def __init__(self, db: Session):
        self.db = db

    def create_execution_bug(
        self,
        *,
        bug_id: str,
        minor_version_id: int,
        requirement_id: int,
        source_type: BugSourceType,
        actor: User,
        source_ref: str | None = None,
    ) -> dict:
        if self.db.query(BugTracking).filter(BugTracking.bug_id == bug_id).first():
            raise HTTPException(status_code=400, detail=f"添加失败：Bug 编号 {bug_id} 已经存在！")

        req = self.db.query(Requirement).filter(Requirement.id == requirement_id).first()
        if not req:
            raise HTTPException(status_code=404, detail="Requirement not found")
        if req.test_completed and source_type != BugSourceType.RETEST:
            raise HTTPException(status_code=400, detail="测试已封板（已勾选完成），无法继续添加 Bug！请先取消勾选。")

        bug = BugTracking(
            major_version_id=req.major_version_id,
            requirement_id=requirement_id,
            source_type=source_type,
            source_ref=source_ref,
            bug_id=bug_id,
            found_minor_version_id=minor_version_id,
            created_by_id=actor.id,
        )
        self.db.add(bug)
        self.db.commit()
        self.db.refresh(bug)
        audit(self.db, action="bug.create", target_type="bug", actor_id=actor.id, target_id=str(bug.id), detail=bug.bug_id)
        sse_publish(
            "overall_bug_created" if source_type == BugSourceType.MANUAL and requirement_id is None else "bug_created",
            {
                "id": bug.id,
                "bug_id": bug.bug_id,
                "source_type": source_type.value if hasattr(source_type, "value") else str(source_type),
                "requirement_id": requirement_id,
                "major_version_id": req.major_version_id,
                "minor_version_id": minor_version_id,
            },
            channels=["global"],
        )
        return {"id": bug.id, "message": "Bug recorded"}

    def update_bug(self, bug_id: int, new_bug_id: str, actor_id: int | None = None) -> dict:
        existing = self.db.query(BugTracking).filter(BugTracking.bug_id == new_bug_id).first()
        if existing and existing.id != bug_id:
            raise HTTPException(status_code=400, detail=f"修改失败：Bug 编号 {new_bug_id} 已存在！")

        bug = self.db.query(BugTracking).filter(BugTracking.id == bug_id).first()
        if not bug:
            raise HTTPException(status_code=404, detail="Bug not found")
        old_bug_id = bug.bug_id
        bug.bug_id = new_bug_id
        self.db.commit()
        audit(self.db, action="bug.update", target_type="bug", actor_id=actor_id, target_id=str(bug.id), detail=f"{old_bug_id}->{new_bug_id}")
        return {"message": "Bug updated"}

    def delete_bug(self, bug_id: int, actor_id: int | None = None) -> dict:
        bug = self.db.query(BugTracking).filter(BugTracking.id == bug_id).first()
        if not bug:
            raise HTTPException(status_code=404, detail="Bug not found")
        bug_no = bug.bug_id
        self.db.delete(bug)
        self.db.commit()
        audit(self.db, action="bug.delete", target_type="bug", actor_id=actor_id, target_id=str(bug_id), detail=bug_no)
        return {"message": "Bug deleted"}

    def toggle_retest_fail(self, bug_id: int, is_retest_failed: bool, actor_id: int | None = None) -> dict:
        bug = self.db.query(BugTracking).filter(BugTracking.id == bug_id).first()
        if not bug:
            raise HTTPException(status_code=404, detail="Bug not found")
        bug.is_retest_failed = is_retest_failed
        self.db.commit()
        audit(self.db, action="bug.toggle_retest_fail", target_type="bug", actor_id=actor_id, target_id=str(bug.id), detail=str(is_retest_failed))
        return {"message": "Bug retest status updated"}

    def search_bug(self, bug_id: str, software_id: int | None = None) -> dict:
        query = self.db.query(BugTracking).options(joinedload(BugTracking.requirement)).filter(BugTracking.bug_id == bug_id)
        if software_id:
            query = query.join(Version, BugTracking.major_version_id == Version.id).filter(Version.software_id == software_id)
        bug = query.first()
        if not bug:
            raise HTTPException(status_code=404, detail="未找到该 Bug 编号")
        return {
            "id": bug.id,
            "bug_id": bug.bug_id,
            "zentao_bug_url": bug.zentao_bug_url,
            "zentao_bug_title": bug.zentao_bug_title,
            "req_title": bug.requirement.title if bug.requirement else "无关联需求 / 自由Bug",
            "dispatched_to_id": bug.dispatched_to_id,
        }

    def dispatch_bug(self, bug_id: int, user_id: int, actor_id: int | None = None) -> tuple[dict, User | None, BugTracking]:
        bug = self.db.query(BugTracking).filter(BugTracking.id == bug_id).first()
        if not bug:
            raise HTTPException(status_code=404, detail="Bug not found")
        bug.dispatched_to_id = user_id
        self.db.commit()
        user = self.db.query(User).filter(User.id == user_id).first()
        audit(self.db, action="bug.dispatch", target_type="bug", actor_id=actor_id, target_id=str(bug.id), detail=f"dispatch_to={user_id}")
        sse_publish(
            "bug_dispatch_created",
            {"id": bug.id, "bug_id": bug.bug_id, "dispatched_to_id": user_id},
            channels=["global", f"user:{user_id}"],
        )
        return {"message": "特派成功"}, user, bug

    def dispatched_to_me(self, major_version_id: int, current_user: User) -> list[dict]:
        bugs = (
            self.db.query(BugTracking)
            .options(joinedload(BugTracking.requirement))
            .filter(BugTracking.major_version_id == major_version_id, BugTracking.dispatched_to_id == current_user.id)
            .all()
        )
        records = self.db.query(BugStage5Record).filter(BugStage5Record.user_id == current_user.id).all()
        record_map = {r.bug_tracking_id: r for r in records}
        rows = []
        for bug in bugs:
            record = record_map.get(bug.id)
            rows.append(
                {
                    "id": bug.id,
                    "bug_id": bug.bug_id,
                    "zentao_bug_id": bug.zentao_bug_id,
                    "closed": bool(bug.closed),
                    "source_type": bug.source_type.value,
                    "req_title": bug.requirement.title if bug.requirement else "无关联需求 / 自由Bug",
                    "test_done": record.test_done if record else False,
                    "resolution": record.resolution if record else "fixed",
                    "newly_found_bug_id": record.newly_found_bug_id if record else "",
                }
            )
        return rows

    def dispatched_all(self, software_id: int | None = None) -> list[dict]:
        query = (
            self.db.query(BugTracking)
            .options(joinedload(BugTracking.dispatched_to))
            .filter(BugTracking.dispatched_to_id.isnot(None))
            .order_by(BugTracking.id.desc())
        )
        if software_id:
            query = query.join(Version, BugTracking.major_version_id == Version.id).filter(Version.software_id == software_id)
        bugs = query.all()
        return [
            {
                "id": b.id,
                "bug_id": b.bug_id,
                "zentao_bug_url": b.zentao_bug_url,
                "zentao_bug_title": b.zentao_bug_title,
                "dispatched_to_name": b.dispatched_to.shown_name if b.dispatched_to else "未知",
                "closed": b.closed,
                "resolution": b.resolution,
            }
            for b in bugs
        ]
