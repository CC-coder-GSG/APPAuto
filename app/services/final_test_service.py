from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.orm import Session, joinedload

from app.models import FinalTestRecord, Requirement, User, Version, VersionType
from app.services.audit_service import audit
from app.services.sse_service import sse_publish
from app.utils.time_utils import local_now


class FinalTestService:
    """业务逻辑：最终测试阶段开关、每人独立勾选、分配台进度聚合。"""

    def __init__(self, db: Session):
        self.db = db

    # ---- helpers -------------------------------------------------------
    def _get_major(self, major_version_id: int) -> Version:
        version = self.db.query(Version).filter(Version.id == major_version_id).first()
        if not version:
            raise HTTPException(status_code=404, detail="大版本不存在")
        if version.version_type != VersionType.MAJOR:
            raise HTTPException(status_code=400, detail="major_version_id 必须指向大版本")
        return version

    @staticmethod
    def _is_task_assignee(requirement: Requirement, user: User) -> bool:
        """当前用户的禅道账号是否等于该子任务指派人账号（无任务/无指派人则 False）。"""
        if not requirement.zentao_task_id:
            return False
        assignee = (requirement.zentao_task_assigned_to or "").strip().lower()
        acc = (getattr(user, "zentao_account", None) or "").strip().lower()
        return bool(assignee) and bool(acc) and assignee == acc

    def is_enabled(self, major_version_id: int | None) -> bool:
        if not major_version_id:
            return False
        row = self.db.query(Version.final_test_enabled).filter(Version.id == major_version_id).first()
        return bool(row[0]) if row else False

    # ---- toggle / status ----------------------------------------------
    def toggle(self, major_version_id: int, enabled: bool, current_user: User) -> dict:
        major = self._get_major(major_version_id)
        major.final_test_enabled = bool(enabled)
        if enabled:
            major.final_test_started_at = local_now()
        self.db.commit()

        audit(
            self.db,
            action="final_test.enable" if enabled else "final_test.disable",
            target_type="version",
            actor_id=current_user.id,
            target_id=str(major_version_id),
            detail=f"final_test_enabled={enabled}",
        )
        sse_publish(
            "final_test_toggled",
            {
                "major_version_id": major_version_id,
                "enabled": bool(enabled),
            },
            channels=["global"],
        )
        return self.status(major_version_id)

    def status(self, major_version_id: int) -> dict:
        major = self._get_major(major_version_id)
        return {
            "major_version_id": major.id,
            "major_version_name": major.version_no,
            "enabled": bool(major.final_test_enabled),
            "started_at": major.final_test_started_at.isoformat() if major.final_test_started_at else None,
        }

    # ---- per-user checkbox ---------------------------------------------
    def upsert_record(
        self,
        requirement_id: int,
        current_user: User,
        case_completed: bool | None = None,
        test_completed: bool | None = None,
    ) -> dict:
        requirement = (
            self.db.query(Requirement)
            .options(joinedload(Requirement.major_version))
            .filter(Requirement.id == requirement_id)
            .first()
        )
        if not requirement:
            raise HTTPException(status_code=404, detail="需求不存在")
        if not (requirement.major_version and requirement.major_version.final_test_enabled):
            raise HTTPException(status_code=400, detail="该需求所属大版本未处于最终测试阶段")

        record = (
            self.db.query(FinalTestRecord)
            .filter(
                FinalTestRecord.requirement_id == requirement_id,
                FinalTestRecord.user_id == current_user.id,
            )
            .first()
        )
        if not record:
            record = FinalTestRecord(requirement_id=requirement_id, user_id=current_user.id)
            self.db.add(record)

        test_completed_changed = False
        finish_zentao = False
        if test_completed is not None:
            was_done = bool(record.test_completed)
            if test_completed and not was_done:
                test_completed_changed = True
                finish_zentao = True
            elif not test_completed and was_done:
                test_completed_changed = True
                finish_zentao = False

        # 仅当子任务指派人是当前用户本人时，勾选/取消完成才联动禅道任务；否则只留本地记录。
        # 先确认禅道状态，再写最终测试记录，避免取消勾选后禅道仍停留在完成态。
        if test_completed_changed and self._is_task_assignee(requirement, current_user):
            try:
                from app.services.zentao_task_sync_service import ZentaoTaskSyncService

                svc = ZentaoTaskSyncService(self.db)
                if finish_zentao:
                    result = svc.finish_requirement_task(requirement, acting_user=current_user)
                else:
                    result = svc.reactivate_requirement_task(requirement, acting_user=current_user)
                if not result.get("ok"):
                    errors = [str(item) for item in (result.get("errors") or []) if item]
                    raise HTTPException(
                        status_code=502,
                        detail="；".join(errors) or "禅道任务状态未切换",
                    )
            except HTTPException:
                raise
            except Exception as exc:  # noqa: BLE001
                import logging

                logging.getLogger(__name__).warning(
                    "final_test zentao task sync req=%s finished=%s failed: %s",
                    requirement_id, finish_zentao, exc,
                )
                raise HTTPException(status_code=502, detail=f"禅道任务同步失败：{exc}") from exc

        if case_completed is not None:
            record.case_completed = bool(case_completed)
        if test_completed is not None:
            record.test_completed = bool(test_completed)
            record.test_completed_at = local_now() if test_completed else None

        self.db.commit()
        self.db.refresh(record)
        audit(
            self.db,
            action="final_test.patch_status",
            target_type="requirement",
            actor_id=current_user.id,
            target_id=str(requirement_id),
            detail=f"case={record.case_completed},test={record.test_completed}",
        )
        sse_publish(
            "final_test_requirement_status_changed",
            {
                "requirement_id": requirement_id,
                "user_id": current_user.id,
                "case_completed": bool(record.case_completed),
                "test_completed": bool(record.test_completed),
            },
            channels=["global", f"user:{current_user.id}"],
        )
        return {
            "message": "最终测试状态已更新",
            "requirement_id": requirement_id,
            "case_completed": record.case_completed,
            "test_completed": record.test_completed,
        }

    # ---- assign-board aggregate ---------------------------------------
    def progress_for_major(self, major_version_id: int) -> dict:
        """每个人在该大版本最终测试阶段的勾选进度（仅当已开启时有意义）。"""
        major = self._get_major(major_version_id)
        reqs = (
            self.db.query(Requirement)
            .filter(Requirement.major_version_id == major_version_id)
            .order_by(Requirement.id.asc())
            .all()
        )
        req_ids = [r.id for r in reqs]
        total_reqs = len(req_ids)

        records: list[FinalTestRecord] = []
        if req_ids:
            records = (
                self.db.query(FinalTestRecord)
                .options(joinedload(FinalTestRecord.user))
                .filter(FinalTestRecord.requirement_id.in_(req_ids))
                .all()
            )

        # group by user
        users_map: dict[int, dict] = {}
        for rec in records:
            bucket = users_map.setdefault(
                rec.user_id,
                {
                    "user_id": rec.user_id,
                    "user_name": rec.user.shown_name if rec.user else "未知",
                    "case_done": 0,
                    "test_done": 0,
                    "req_status": {},  # requirement_id -> {case, test}
                },
            )
            if rec.case_completed:
                bucket["case_done"] += 1
            if rec.test_completed:
                bucket["test_done"] += 1
            bucket["req_status"][rec.requirement_id] = {
                "case_completed": rec.case_completed,
                "test_completed": rec.test_completed,
            }

        people = sorted(users_map.values(), key=lambda x: x["user_name"])

        return {
            "enabled": bool(major.final_test_enabled),
            "major_version_id": major.id,
            "major_version_name": major.version_no,
            "started_at": major.final_test_started_at.isoformat() if major.final_test_started_at else None,
            "total_requirements": total_reqs,
            "requirements": [
                {"id": r.id, "zentao_req_id": r.zentao_req_id, "title": r.title} for r in reqs
            ],
            "people": people,
        }
