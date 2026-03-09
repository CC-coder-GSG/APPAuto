from __future__ import annotations

from datetime import datetime

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from app.models import Requirement, RequirementStatus, RequirementStatusHistory, TestCase, TestExecution, User, Version, VersionType
from app.services.audit_service import audit
from app.utils.state_machine import ensure_requirement_transition
from app.utils.validators import validate_req_id


class RequirementService:
    def __init__(self, db: Session):
        self.db = db

    def ensure_major_version_exists(self, major_version_id: int) -> Version:
        version = self.db.query(Version).filter(Version.id == major_version_id).first()
        if not version:
            raise HTTPException(status_code=400, detail="关联的大版本不存在")
        if version.version_type != VersionType.MAJOR:
            raise HTTPException(status_code=400, detail="major_version_id 必须指向大版本")
        return version

    def create_requirement(self, zentao_req_id: str, title: str, major_version_id: int, actor_id: int | None = None) -> dict:
        self.ensure_major_version_exists(major_version_id)
        if not validate_req_id(zentao_req_id):
            raise HTTPException(status_code=400, detail="zentao_req_id must be like r#xxxx")
        if self.db.query(Requirement).filter(Requirement.zentao_req_id == zentao_req_id).first():
            raise HTTPException(status_code=400, detail="Requirement already exists")

        requirement = Requirement(
            zentao_req_id=zentao_req_id,
            title=title,
            major_version_id=major_version_id,
            status=RequirementStatus.PENDING,
        )
        self.db.add(requirement)
        self.db.commit()
        self.db.refresh(requirement)
        self._record_status_history(requirement, None, RequirementStatus.PENDING, actor_id=actor_id)
        self.db.commit()
        audit(
            self.db,
            action="requirement.create",
            target_type="requirement",
            actor_id=actor_id,
            target_id=str(requirement.id),
            detail=requirement.zentao_req_id,
        )
        return {
            "id": requirement.id,
            "zentao_req_id": requirement.zentao_req_id,
            "title": requirement.title,
            "status": requirement.status,
        }

    def batch_create_requirements(self, major_version_id: int, items: list[dict], actor_id: int | None = None) -> dict:
        self.ensure_major_version_exists(major_version_id)
        existing_reqs = {r[0] for r in self.db.query(Requirement.zentao_req_id).all()}

        new_reqs: list[Requirement] = []
        for item in items:
            req_id = item["zentao_req_id"]
            title = item["title"]
            if not validate_req_id(req_id) or req_id in existing_reqs:
                continue
            new_reqs.append(
                Requirement(
                    zentao_req_id=req_id,
                    title=title,
                    major_version_id=major_version_id,
                    status=RequirementStatus.PENDING,
                )
            )
            existing_reqs.add(req_id)

        if new_reqs:
            self.db.bulk_save_objects(new_reqs)
            self.db.commit()
            created_rows = (
                self.db.query(Requirement)
                .filter(Requirement.major_version_id == major_version_id, Requirement.zentao_req_id.in_([r.zentao_req_id for r in new_reqs]))
                .all()
            )
            for row in created_rows:
                self._record_status_history(row, None, RequirementStatus.PENDING, actor_id=actor_id)
            self.db.commit()
            audit(
                self.db,
                action="requirement.batch_create",
                target_type="version",
                actor_id=actor_id,
                target_id=str(major_version_id),
                detail=f"count={len(new_reqs)}",
            )

        return {"message": "导入成功", "count": len(new_reqs)}

    def recalculate_requirement_status(self, requirement: Requirement, actor_id: int | None = None) -> Requirement:
        old_status_obj = requirement.status
        current_status = old_status_obj.value if hasattr(old_status_obj, "value") else str(old_status_obj)
        if requirement.retest_completed:
            next_status = RequirementStatus.RETEST_DONE
        elif requirement.test_completed:
            next_status = RequirementStatus.TEST_DONE
        elif requirement.case_completed:
            next_status = RequirementStatus.CASE_DONE
        elif requirement.owner_id:
            next_status = RequirementStatus.ASSIGNED
        else:
            next_status = RequirementStatus.PENDING

        next_status_value = next_status.value if hasattr(next_status, "value") else str(next_status)
        try:
            ensure_requirement_transition(current_status, next_status_value)
        except Exception:
            # 保守迁移：历史数据存在旧状态时，不阻断线上流程，先统一回写到目标状态。
            pass
        requirement.status = next_status
        if old_status_obj != next_status:
            self._record_status_history(requirement, old_status_obj, next_status, actor_id=actor_id)
        return requirement

    def list_requirements(self, major_version_id: int) -> list[dict]:
        rows = (
            self.db.query(Requirement)
            .options(joinedload(Requirement.owner), joinedload(Requirement.retester), joinedload(Requirement.test_cases))
            .filter(Requirement.major_version_id == major_version_id)
            .order_by(Requirement.id.asc())
            .all()
        )
        return [
            {
                "id": r.id,
                "zentao_req_id": r.zentao_req_id,
                "title": r.title,
                "owner": r.owner.username if r.owner else None,
                "owner_id": r.owner_id,
                "case_completed": r.case_completed,
                "test_completed": r.test_completed,
                "retest_completed": r.retest_completed,
                "retested_by": r.retester.username if r.retester else None,
                "status": r.status,
                "case_ids": [c.zentao_case_id for c in r.test_cases],
            }
            for r in rows
        ]

    def update_requirement(self, requirement_id: int, zentao_req_id: str, title: str, major_version_id: int, actor_id: int | None = None) -> dict:
        req = self.db.query(Requirement).filter(Requirement.id == requirement_id).first()
        if not req:
            raise HTTPException(status_code=404, detail="Requirement not found")
        self.ensure_major_version_exists(major_version_id)
        if not validate_req_id(zentao_req_id):
            raise HTTPException(status_code=400, detail="zentao_req_id must be like r#xxxx")
        req.zentao_req_id = zentao_req_id
        req.title = title
        req.major_version_id = major_version_id
        self.db.commit()
        audit(self.db, action="requirement.update", target_type="requirement", actor_id=actor_id, target_id=str(req.id), detail=req.zentao_req_id)
        return {"message": "Requirement updated"}

    def delete_requirement(self, requirement_id: int, actor_id: int | None = None) -> dict:
        req = self.db.query(Requirement).filter(Requirement.id == requirement_id).first()
        if not req:
            raise HTTPException(status_code=404, detail="Requirement not found")
        req_id = req.id
        req_no = req.zentao_req_id
        self.db.delete(req)
        self.db.commit()
        audit(self.db, action="requirement.delete", target_type="requirement", actor_id=actor_id, target_id=str(req_id), detail=req_no)
        return {"message": "Requirement deleted"}

    def assign_and_publish(self, major_version_id: int, assignments: list[dict], users_map: dict[int, str], actor_id: int | None = None) -> dict:
        req_map = {r.id: r for r in self.db.query(Requirement).filter(Requirement.major_version_id == major_version_id).all()}
        change_msgs: list[str] = []

        for item in assignments:
            req = req_map.get(item["requirement_id"])
            if not req:
                continue

            old_owner_id = req.owner_id
            new_owner_id = item.get("owner_id")
            if old_owner_id == new_owner_id:
                continue

            req.owner_id = new_owner_id
            req.case_completed = False
            req.test_completed = False
            req.retest_completed = False
            self.recalculate_requirement_status(req, actor_id=actor_id)

            if new_owner_id:
                old_name = users_map.get(old_owner_id, "未分配")
                new_name = users_map.get(new_owner_id, "未知")
                if old_owner_id:
                    change_msgs.append(f"> **{req.zentao_req_id}** ({req.title}) 已从 @{old_name} 移交给了 @{new_name}")

            audit(
                self.db,
                action="requirement.assign",
                target_type="requirement",
                actor_id=actor_id,
                target_id=str(req.id),
                detail=f"{old_owner_id}->{new_owner_id}",
            )

        self.db.commit()
        return {"message": "Assignments updated", "change_msgs": change_msgs}

    def patch_requirement_status(
        self,
        requirement_id: int,
        current_user: User,
        case_completed: bool | None = None,
        test_completed: bool | None = None,
    ) -> dict:
        requirement = self.db.query(Requirement).filter(Requirement.id == requirement_id).first()
        if not requirement:
            raise HTTPException(status_code=404, detail="Requirement not found")
        if requirement.owner_id and requirement.owner_id != current_user.id and current_user.role.value != "admin":
            raise HTTPException(status_code=403, detail="Only owner can update status")

        if case_completed is not None:
            requirement.case_completed = case_completed
        if test_completed is not None:
            requirement.test_completed = test_completed

        self.recalculate_requirement_status(requirement, actor_id=current_user.id)
        self.db.commit()
        audit(
            self.db,
            action="requirement.patch_status",
            target_type="requirement",
            actor_id=current_user.id,
            target_id=str(requirement.id),
            detail=f"case={requirement.case_completed},test={requirement.test_completed},status={requirement.status.value}",
        )
        return {
            "message": "Requirement status updated",
            "case_completed": requirement.case_completed,
            "test_completed": requirement.test_completed,
            "status": requirement.status,
        }

    def update_requirement_cases(self, requirement_id: int, case_ids: list[str], case_completed: bool, actor_id: int | None = None) -> dict:
        requirement = self.db.query(Requirement).filter(Requirement.id == requirement_id).first()
        if not requirement:
            raise HTTPException(status_code=404, detail="Requirement not found")
        self.db.query(TestCase).filter(TestCase.requirement_id == requirement_id).delete()
        for cid in case_ids:
            self.db.add(TestCase(requirement_id=requirement.id, zentao_case_id=cid, creator_id=actor_id))
        requirement.case_completed = case_completed
        self.recalculate_requirement_status(requirement, actor_id=actor_id)
        self.db.commit()
        audit(
            self.db,
            action="requirement.update_cases",
            target_type="requirement",
            actor_id=actor_id,
            target_id=str(requirement.id),
            detail=f"case_count={len(case_ids)}",
        )
        return {"message": "Cases updated", "case_ids": case_ids}

    def update_test_execution_completion(self, requirement_id: int, test_completed: bool, actor_id: int | None = None) -> Requirement:
        requirement = self.db.query(Requirement).filter(Requirement.id == requirement_id).first()
        if not requirement:
            raise HTTPException(status_code=404, detail="Requirement not found")
        requirement.test_completed = test_completed
        self.recalculate_requirement_status(requirement, actor_id=actor_id)
        self.db.commit()
        audit(
            self.db,
            action="requirement.update_test_completion",
            target_type="requirement",
            actor_id=actor_id,
            target_id=str(requirement.id),
            detail=f"test_completed={test_completed}",
        )
        return requirement

    def add_case_to_requirement(self, req_id: int, zentao_case_id: str, actor_id: int | None = None) -> dict:
        req = self.db.query(Requirement).filter(Requirement.id == req_id).first()
        if not req:
            raise HTTPException(status_code=404, detail="Requirement not found")
        if req.case_completed:
            raise HTTPException(status_code=400, detail="用例已封板（已勾选完成），无法新增！请先取消勾选。")

        case = TestCase(requirement_id=req_id, zentao_case_id=zentao_case_id, creator_id=actor_id)
        self.db.add(case)
        self.db.commit()
        self.db.refresh(case)
        audit(self.db, action="requirement.add_case", target_type="requirement", actor_id=actor_id, target_id=str(req_id), detail=zentao_case_id)
        return {"id": case.id, "zentao_case_id": case.zentao_case_id}

    def update_case_identifier(self, case_id: int, zentao_case_id: str, actor_id: int | None = None) -> dict:
        case = self.db.query(TestCase).filter(TestCase.id == case_id).first()
        if not case:
            raise HTTPException(status_code=404, detail="Case not found")
        req = self.db.query(Requirement).filter(Requirement.id == case.requirement_id).first()
        if req and req.case_completed:
            raise HTTPException(status_code=400, detail="用例已封板（已勾选完成），无法修改！请先取消勾选。")

        old_id = case.zentao_case_id
        case.zentao_case_id = zentao_case_id
        self.db.commit()
        audit(self.db, action="requirement.update_case", target_type="test_case", actor_id=actor_id, target_id=str(case.id), detail=f"{old_id}->{zentao_case_id}")
        return {"message": "Case updated"}

    def delete_case(self, case_id: int, actor_id: int | None = None) -> dict:
        case = self.db.query(TestCase).filter(TestCase.id == case_id).first()
        if not case:
            raise HTTPException(status_code=404, detail="Case not found")
        req = self.db.query(Requirement).filter(Requirement.id == case.requirement_id).first()
        if req and req.case_completed:
            raise HTTPException(status_code=400, detail="用例已封板（已勾选完成），无法删除！请先取消勾选。")

        case_no = case.zentao_case_id
        self.db.delete(case)
        self.db.commit()
        audit(self.db, action="requirement.delete_case", target_type="test_case", actor_id=actor_id, target_id=str(case_id), detail=case_no)
        return {"message": "Case deleted"}

    def upsert_test_execution(
        self,
        requirement_id: int,
        minor_version_id: int,
        bug_id: str | None,
        source_case_id: str | None,
        result_status: str,
        notes: str | None,
        test_completed: bool,
        actor_id: int | None = None,
    ) -> dict:
        requirement = self.db.query(Requirement).filter(Requirement.id == requirement_id).first()
        if not requirement:
            raise HTTPException(status_code=404, detail="Requirement not found")
        execution = self.db.query(TestExecution).filter(
            TestExecution.requirement_id == requirement_id,
            TestExecution.minor_version_id == minor_version_id,
        ).first()
        if not execution:
            execution = TestExecution(requirement_id=requirement_id, minor_version_id=minor_version_id)
            self.db.add(execution)
        execution.bug_id = bug_id
        execution.source_case_id = source_case_id
        execution.result_status = result_status
        execution.notes = notes
        execution.executed_by_id = actor_id
        execution.executed_at = datetime.utcnow()
        self.db.flush()
        self.update_test_execution_completion(requirement_id, test_completed, actor_id=actor_id)
        return {"message": "Test execution updated"}

    def build_case_progress_message(self, major_version_id: int, current_user: User) -> str:
        major = self.db.query(Version).filter(Version.id == major_version_id).first()
        major_name = major.version_no if major else f"ID:{major_version_id}"

        created_cases = (
            self.db.query(func.count(TestCase.id))
            .join(Requirement, TestCase.requirement_id == Requirement.id)
            .filter(Requirement.major_version_id == major_version_id, TestCase.creator_id == current_user.id)
            .scalar()
            or 0
        )
        req_case_pending = (
            self.db.query(func.count(Requirement.id))
            .filter(Requirement.major_version_id == major_version_id, Requirement.owner_id == current_user.id, Requirement.case_completed.is_(False))
            .scalar()
            or 0
        )
        req_case_done = (
            self.db.query(func.count(Requirement.id))
            .filter(Requirement.major_version_id == major_version_id, Requirement.owner_id == current_user.id, Requirement.case_completed.is_(True))
            .scalar()
            or 0
        )
        return "\n".join(
            [
                "### 需求测试进度",
                f"> 大版本：{major_name}",
                f"> 提交人：@{current_user.username}",
                f"> 当前人员填写用例：{created_cases} 个",
                f"> 需求用例未完成：{req_case_pending} 个",
                f"> 需求用例已完成：{req_case_done} 个",
            ]
        )

    def build_test_progress_message(self, major_version_id: int, minor_version_id: int, current_user: User) -> str:
        major = self.db.query(Version).filter(Version.id == major_version_id).first()
        minor = self.db.query(Version).filter(Version.id == minor_version_id).first()
        major_name = major.version_no if major else f"ID:{major_version_id}"
        minor_name = minor.version_no if minor else f"ID:{minor_version_id}"

        req_test_done = (
            self.db.query(func.count(Requirement.id))
            .filter(Requirement.major_version_id == major_version_id, Requirement.owner_id == current_user.id, Requirement.test_completed.is_(True))
            .scalar()
            or 0
        )
        req_test_pending = (
            self.db.query(func.count(Requirement.id))
            .filter(Requirement.major_version_id == major_version_id, Requirement.owner_id == current_user.id, Requirement.test_completed.is_(False))
            .scalar()
            or 0
        )
        return "\n".join(
            [
                "### 需求测试进度",
                f"> 大版本：{major_name} | 当前发包：{minor_name}",
                f"> 提交人：@{current_user.username}",
                f"> 当前人员已完成测试需求：{req_test_done} 个",
                f"> 需求测试未完成：{req_test_pending} 个",
                f"> 需求测试已完成：{req_test_done} 个",
            ]
        )
    def _record_status_history(
        self,
        requirement: Requirement,
        from_status: RequirementStatus | None,
        to_status: RequirementStatus,
        actor_id: int | None = None,
    ) -> None:
        self.db.add(
            RequirementStatusHistory(
                requirement_id=requirement.id,
                from_status=from_status,
                to_status=to_status,
                changed_by_id=actor_id,
                changed_at=datetime.utcnow(),
            )
        )
