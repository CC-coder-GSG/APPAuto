from __future__ import annotations

from datetime import datetime

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from app.models import Requirement, RequirementStatus, RequirementStatusHistory, TestCase, TestExecution, TestResultStatus, User, Version, VersionType
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
        if self.db.query(Requirement).filter(Requirement.major_version_id == major_version_id, Requirement.zentao_req_id == zentao_req_id).first():
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
        existing_reqs = {
            r[0] for r in self.db.query(Requirement.zentao_req_id).filter(Requirement.major_version_id == major_version_id).all()
        }

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

    def list_requirements_for_link(self, source_major_version_id: int, target_major_version_id: int) -> list[dict]:
        self.ensure_major_version_exists(source_major_version_id)
        self.ensure_major_version_exists(target_major_version_id)
        source_rows = (
            self.db.query(Requirement)
            .options(joinedload(Requirement.owner), joinedload(Requirement.test_cases))
            .filter(Requirement.major_version_id == source_major_version_id)
            .order_by(Requirement.id.asc())
            .all()
        )
        target_ids = {
            r[0]
            for r in self.db.query(Requirement.zentao_req_id).filter(Requirement.major_version_id == target_major_version_id).all()
        }
        return [
            {
                "id": r.id,
                "zentao_req_id": r.zentao_req_id,
                "title": r.title,
                "owner_id": r.owner_id,
                "owner_name": r.owner.shown_name if r.owner else "未分配",
                "case_count": len(r.test_cases or []),
                "already_linked": r.zentao_req_id in target_ids,
            }
            for r in source_rows
        ]

    def link_requirements_from_major(
        self,
        *,
        target_major_version_id: int,
        source_major_version_id: int,
        source_requirement_ids: list[int],
        actor_id: int | None = None,
    ) -> dict:
        if target_major_version_id == source_major_version_id:
            raise HTTPException(status_code=400, detail="来源大版本与目标大版本不能相同")
        self.ensure_major_version_exists(target_major_version_id)
        self.ensure_major_version_exists(source_major_version_id)

        source_rows = (
            self.db.query(Requirement)
            .options(joinedload(Requirement.test_cases))
            .filter(Requirement.major_version_id == source_major_version_id, Requirement.id.in_(source_requirement_ids))
            .all()
        )
        if not source_rows:
            return {"message": "没有可关联的需求", "created_count": 0, "skipped_count": 0}

        existing_ids = {
            r[0]
            for r in self.db.query(Requirement.zentao_req_id).filter(Requirement.major_version_id == target_major_version_id).all()
        }

        created_count = 0
        skipped_count = 0
        for src in source_rows:
            if src.zentao_req_id in existing_ids:
                skipped_count += 1
                continue

            new_req = Requirement(
                zentao_req_id=src.zentao_req_id,
                title=src.title,
                major_version_id=target_major_version_id,
                owner_id=src.owner_id,
                case_completed=src.case_completed,
                test_completed=src.test_completed,
                retest_completed=src.retest_completed,
                retested_by_id=src.retested_by_id,
                retested_at=src.retested_at,
                retest_minor_version_id=None,
                retest_passed=src.retest_passed,
                status=src.status,
            )
            self.db.add(new_req)
            self.db.flush()

            for c in (src.test_cases or []):
                self.db.add(
                    TestCase(
                        requirement_id=new_req.id,
                        zentao_case_id=c.zentao_case_id,
                        creator_id=c.creator_id,
                    )
                )
            created_count += 1
            existing_ids.add(src.zentao_req_id)
            audit(
                self.db,
                action="requirement.link_major",
                target_type="requirement",
                actor_id=actor_id,
                target_id=str(new_req.id),
                detail=f"from_major={source_major_version_id},from_req={src.id}",
            )

        self.db.commit()
        return {
            "message": f"关联完成：新增 {created_count} 条，跳过 {skipped_count} 条",
            "created_count": created_count,
            "skipped_count": skipped_count,
        }

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
                "owner": r.owner.shown_name if r.owner else None,
                "owner_id": r.owner_id,
                "case_completed": r.case_completed,
                "test_completed": r.test_completed,
                "retest_completed": r.retest_completed,
                "retested_by": r.retester.shown_name if r.retester else None,
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
        allowed_result_status = {e.value for e in TestResultStatus}
        requirement = self.db.query(Requirement).filter(Requirement.id == requirement_id).first()
        if not requirement:
            raise HTTPException(status_code=404, detail="需求不存在")
        actor = self.db.query(User).filter(User.id == actor_id).first() if actor_id else None
        if requirement.owner_id and actor and requirement.owner_id != actor.id and actor.role.value != "admin":
            raise HTTPException(status_code=403, detail="仅需求负责人或管理员可提交测试执行记录")

        minor_version = self.db.query(Version).filter(Version.id == minor_version_id).first()
        if not minor_version:
            raise HTTPException(status_code=400, detail="小版本不存在")
        if minor_version.version_type != VersionType.MINOR:
            raise HTTPException(status_code=400, detail="minor_version_id 必须是小版本")
        if minor_version.parent_id != requirement.major_version_id:
            raise HTTPException(status_code=400, detail="小版本与需求所属大版本不匹配")

        if result_status not in allowed_result_status:
            raise HTTPException(status_code=400, detail="测试结果非法，仅支持：passed/failed/blocked/partial/untested")

        execution = self.db.query(TestExecution).filter(
            TestExecution.requirement_id == requirement_id,
            TestExecution.minor_version_id == minor_version_id,
        ).first()
        is_create = execution is None
        if not execution:
            execution = TestExecution(requirement_id=requirement_id, minor_version_id=minor_version_id)
            self.db.add(execution)
        execution.bug_id = bug_id
        execution.source_case_id = source_case_id
        execution.result_status = TestResultStatus(result_status)
        execution.notes = notes
        execution.executed_by_id = actor_id
        execution.executed_at = datetime.utcnow()
        requirement.test_completed = test_completed
        self.recalculate_requirement_status(requirement, actor_id=actor_id)
        self.db.commit()
        self.db.refresh(execution)
        audit(
            self.db,
            action="requirement.upsert_test_execution",
            target_type="requirement",
            actor_id=actor_id,
            target_id=str(requirement.id),
            detail=f"minor={minor_version_id},result={result_status},test_completed={test_completed}",
        )
        return {
            "message": "测试执行记录已保存" if is_create else "测试执行记录更新成功",
            "execution_id": execution.id,
            "requirement_id": requirement.id,
            "minor_version_id": execution.minor_version_id,
            "result_status": execution.result_status.value if hasattr(execution.result_status, "value") else str(execution.result_status),
            "test_completed": requirement.test_completed,
            "executed_at": execution.executed_at.isoformat() if execution.executed_at else None,
        }

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
                f"> 提交人：@{current_user.shown_name}",
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
                f"> 提交人：@{current_user.shown_name}",
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
