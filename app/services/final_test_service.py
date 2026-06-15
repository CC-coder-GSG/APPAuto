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

        if case_completed is not None:
            record.case_completed = bool(case_completed)
        if test_completed is not None:
            was_done = bool(record.test_completed)
            record.test_completed = bool(test_completed)
            if test_completed and not was_done:
                record.test_completed_at = local_now()
            elif not test_completed:
                record.test_completed_at = None

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
