from __future__ import annotations

import re
from datetime import date, datetime
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import asc, desc, func, or_
from sqlalchemy.orm import Session, joinedload

from app.models import (
    BugSourceType,
    BugTracking,
    FieldTestBugLink,
    FieldTestPurposeType,
    FieldTestRecord,
    FieldTestResultStatus,
    Requirement,
    User,
    UserRole,
    Version,
    VersionType,
)
from app.services.audit_service import audit
from app.services.sse_service import sse_publish

B_PATTERN = re.compile(r"^b#\d+$")


class FieldTestService:
    def __init__(self, db: Session):
        self.db = db

    def _resolve_major_scope(self, major_version_id: int | None, software_id: int | None) -> list[int] | None:
        if major_version_id:
            return [major_version_id]
        if software_id:
            rows = self.db.query(Version.id).filter(Version.version_type == VersionType.MAJOR, Version.software_id == software_id).all()
            return [int(r[0]) for r in rows]
        return None

    def _validate_major_minor(self, major_version_id: int, minor_version_id: int) -> tuple[Version, Version]:
        major = self.db.query(Version).filter(Version.id == major_version_id, Version.version_type == VersionType.MAJOR).first()
        if not major:
            raise HTTPException(status_code=400, detail="大版本无效")
        minor = self.db.query(Version).filter(Version.id == minor_version_id, Version.version_type == VersionType.MINOR).first()
        if not minor:
            raise HTTPException(status_code=400, detail="小版本无效")
        if minor.parent_id != major.id:
            raise HTTPException(status_code=400, detail="小版本不属于当前大版本")
        return major, minor

    def _validate_requirement(self, requirement_id: int, major_version_id: int) -> Requirement:
        req = self.db.query(Requirement).filter(Requirement.id == requirement_id).first()
        if not req:
            raise HTTPException(status_code=404, detail="关联需求不存在")
        if req.major_version_id != major_version_id:
            raise HTTPException(status_code=400, detail="关联需求不属于当前大版本")
        return req

    def _calc_duration_minutes(self, start_time: datetime, end_time: datetime) -> int:
        if start_time >= end_time:
            raise HTTPException(status_code=400, detail="开始时间必须早于结束时间")
        duration_minutes = int((end_time - start_time).total_seconds() // 60)
        if duration_minutes <= 0:
            raise HTTPException(status_code=400, detail="测试时长必须大于 0 分钟")
        if duration_minutes > 24 * 60:
            raise HTTPException(status_code=400, detail="测试时长超过 24 小时，请检查开始/结束时间")
        return duration_minutes

    def _normalize_bug_ids(self, bug_ids: list[str] | None) -> list[str]:
        if not bug_ids:
            return []
        seen = set()
        cleaned: list[str] = []
        for raw in bug_ids:
            bug_id = (raw or "").strip()
            if not bug_id:
                continue
            if not B_PATTERN.match(bug_id):
                raise HTTPException(status_code=400, detail=f"Bug 编号格式错误：{bug_id}，应为 b#数字")
            if bug_id in seen:
                continue
            seen.add(bug_id)
            cleaned.append(bug_id)
        return cleaned

    def _ensure_access(self, row: FieldTestRecord, actor: User) -> None:
        if actor.role == UserRole.ADMIN:
            return
        if row.tester_id != actor.id:
            raise HTTPException(status_code=403, detail="无权限操作他人的外业测试记录")

    def _record_to_dict(self, row: FieldTestRecord) -> dict:
        return {
            "id": row.id,
            "major_version_id": row.major_version_id,
            "major_version_no": row.major_version.version_no if row.major_version else None,
            "minor_version_id": row.minor_version_id,
            "minor_version_no": row.minor_version.version_no if row.minor_version else None,
            "purpose_type": row.purpose_type.value,
            "requirement_id": row.requirement_id,
            "requirement_label": (f"{row.requirement.zentao_req_id} {row.requirement.title}" if row.requirement else None),
            "test_content": row.test_content,
            "start_time": row.start_time.isoformat(),
            "end_time": row.end_time.isoformat(),
            "duration_minutes": row.duration_minutes,
            "result_status": row.result_status.value,
            "tester_id": row.tester_id,
            "tester_name": row.tester.shown_name if row.tester else None,
            "notes": row.notes,
            "created_at": row.created_at.isoformat(),
            "updated_at": row.updated_at.isoformat(),
            "bugs": [
                {
                    "id": link.bug.id,
                    "bug_id": link.bug.bug_id,
                    "zentao_bug_url": link.bug.zentao_bug_url,
                    "zentao_bug_title": link.bug.zentao_bug_title,
                }
                for link in (row.bug_links or [])
                if link.bug
            ],
            "bug_count": len([1 for link in (row.bug_links or []) if link.bug]),
        }

    def _attach_bugs(self, row: FieldTestRecord, bug_ids: list[str], actor: User) -> list[str]:
        created_bug_ids: list[str] = []
        for bug_id in bug_ids:
            bug = self.db.query(BugTracking).filter(BugTracking.bug_id == bug_id).first()
            if bug and bug.major_version_id != row.major_version_id:
                raise HTTPException(status_code=400, detail=f"Bug 编号 {bug_id} 已存在且不属于当前大版本，无法关联")
            if not bug:
                bug = BugTracking(
                    major_version_id=row.major_version_id,
                    requirement_id=row.requirement_id if row.purpose_type == FieldTestPurposeType.REQUIREMENT else None,
                    source_type=BugSourceType.FIELD_TEST,
                    source_ref=f"field_test:{row.id}",
                    bug_id=bug_id,
                    found_minor_version_id=row.minor_version_id,
                    created_by_id=actor.id,
                )
                self.db.add(bug)
                self.db.flush()
                created_bug_ids.append(bug_id)
            link_exists = self.db.query(FieldTestBugLink).filter(
                FieldTestBugLink.field_test_record_id == row.id,
                FieldTestBugLink.bug_tracking_id == bug.id,
            ).first()
            if not link_exists:
                self.db.add(FieldTestBugLink(field_test_record_id=row.id, bug_tracking_id=bug.id))
        return created_bug_ids

    def create_record(
        self,
        *,
        major_version_id: int,
        minor_version_id: int,
        purpose_type: FieldTestPurposeType,
        requirement_id: int | None,
        test_content: str | None,
        start_time: datetime,
        end_time: datetime,
        result_status: FieldTestResultStatus,
        bug_ids: list[str] | None,
        notes: str | None,
        actor: User,
    ) -> dict:
        self._validate_major_minor(major_version_id, minor_version_id)
        duration_minutes = self._calc_duration_minutes(start_time, end_time)

        if purpose_type == FieldTestPurposeType.REQUIREMENT:
            if not requirement_id:
                raise HTTPException(status_code=400, detail="需求测试必须选择关联需求")
            self._validate_requirement(requirement_id, major_version_id)
            test_content = None
        else:
            if not (test_content or "").strip():
                raise HTTPException(status_code=400, detail="功能测试必须填写测试内容")
            requirement_id = None

        normalized_bug_ids = self._normalize_bug_ids(bug_ids)
        if result_status == FieldTestResultStatus.FAILED and len(normalized_bug_ids) == 0:
            raise HTTPException(status_code=400, detail="测试未通过时至少需要录入 1 个 Bug")

        row = FieldTestRecord(
            major_version_id=major_version_id,
            minor_version_id=minor_version_id,
            purpose_type=purpose_type,
            requirement_id=requirement_id,
            test_content=(test_content or "").strip() or None,
            start_time=start_time,
            end_time=end_time,
            duration_minutes=duration_minutes,
            result_status=result_status,
            tester_id=actor.id,
            notes=(notes or "").strip() or None,
        )
        self.db.add(row)
        self.db.flush()

        created_bug_ids = self._attach_bugs(row, normalized_bug_ids, actor)
        self.db.commit()
        self.db.refresh(row)
        if created_bug_ids:
            created_rows = self.db.query(BugTracking).filter(BugTracking.bug_id.in_(created_bug_ids)).all()
            for bug in created_rows:
                audit(self.db, action="bug.create", target_type="bug", actor_id=actor.id, target_id=str(bug.id), detail=bug.bug_id)

        audit(
            self.db,
            action="field_test.create",
            target_type="field_test",
            actor_id=actor.id,
            target_id=str(row.id),
            detail=f"result={row.result_status.value},bugs={len(normalized_bug_ids)}",
        )
        sse_publish(
            "field_test_record_created",
            {
                "id": row.id,
                "major_version_id": row.major_version_id,
                "minor_version_id": row.minor_version_id,
                "tester_id": row.tester_id,
                "result_status": row.result_status.value,
            },
            channels=["global"],
        )
        return {
            "message": "外业测试记录已创建",
            "record": self.get_detail(row.id, actor),
            "created_bug_ids": created_bug_ids,
            "bug_policy": "已创建和已关联的 Bug 默认保留，不会自动删除",
        }

    def update_record(
        self,
        record_id: int,
        *,
        major_version_id: int,
        minor_version_id: int,
        purpose_type: FieldTestPurposeType,
        requirement_id: int | None,
        test_content: str | None,
        start_time: datetime,
        end_time: datetime,
        result_status: FieldTestResultStatus,
        bug_ids: list[str] | None,
        notes: str | None,
        actor: User,
    ) -> dict:
        row = (
            self.db.query(FieldTestRecord)
            .options(joinedload(FieldTestRecord.bug_links).joinedload(FieldTestBugLink.bug))
            .filter(FieldTestRecord.id == record_id)
            .first()
        )
        if not row:
            raise HTTPException(status_code=404, detail="外业测试记录不存在")
        self._ensure_access(row, actor)

        self._validate_major_minor(major_version_id, minor_version_id)
        duration_minutes = self._calc_duration_minutes(start_time, end_time)

        if purpose_type == FieldTestPurposeType.REQUIREMENT:
            if not requirement_id:
                raise HTTPException(status_code=400, detail="需求测试必须选择关联需求")
            self._validate_requirement(requirement_id, major_version_id)
            test_content = None
        else:
            if not (test_content or "").strip():
                raise HTTPException(status_code=400, detail="功能测试必须填写测试内容")
            requirement_id = None

        row.major_version_id = major_version_id
        row.minor_version_id = minor_version_id
        row.purpose_type = purpose_type
        row.requirement_id = requirement_id
        row.test_content = (test_content or "").strip() or None
        row.start_time = start_time
        row.end_time = end_time
        row.duration_minutes = duration_minutes
        row.result_status = result_status
        row.notes = (notes or "").strip() or None

        normalized_bug_ids = self._normalize_bug_ids(bug_ids)
        created_bug_ids = self._attach_bugs(row, normalized_bug_ids, actor)

        if row.result_status == FieldTestResultStatus.FAILED:
            has_any_linked = (
                self.db.query(FieldTestBugLink)
                .filter(FieldTestBugLink.field_test_record_id == row.id)
                .first()
            ) is not None
            if not has_any_linked:
                raise HTTPException(status_code=400, detail="测试未通过时至少需要录入 1 个 Bug")

        self.db.commit()
        if created_bug_ids:
            created_rows = self.db.query(BugTracking).filter(BugTracking.bug_id.in_(created_bug_ids)).all()
            for bug in created_rows:
                audit(self.db, action="bug.create", target_type="bug", actor_id=actor.id, target_id=str(bug.id), detail=bug.bug_id)

        audit(
            self.db,
            action="field_test.update",
            target_type="field_test",
            actor_id=actor.id,
            target_id=str(row.id),
            detail=f"result={row.result_status.value},added_bugs={len(created_bug_ids)}",
        )
        sse_publish(
            "field_test_record_updated",
            {
                "id": row.id,
                "major_version_id": row.major_version_id,
                "minor_version_id": row.minor_version_id,
                "tester_id": row.tester_id,
                "result_status": row.result_status.value,
            },
            channels=["global"],
        )
        return {
            "message": "外业测试记录已更新",
            "record": self.get_detail(row.id, actor),
            "created_bug_ids": created_bug_ids,
            "bug_policy": "历史已关联 Bug 不会自动解除，编辑仅做增量关联",
        }

    def list_records(
        self,
        *,
        current_user: User,
        major_version_id: int | None = None,
        minor_version_id: int | None = None,
        purpose_type: FieldTestPurposeType | None = None,
        tester_id: int | None = None,
        software_id: int | None = None,
        keyword: str | None = None,
    ) -> list[dict]:
        q = self.db.query(FieldTestRecord).options(
            joinedload(FieldTestRecord.major_version),
            joinedload(FieldTestRecord.minor_version),
            joinedload(FieldTestRecord.requirement),
            joinedload(FieldTestRecord.tester),
            joinedload(FieldTestRecord.bug_links).joinedload(FieldTestBugLink.bug),
        )

        major_scope = self._resolve_major_scope(major_version_id, software_id)
        if major_scope is not None:
            q = q.filter(FieldTestRecord.major_version_id.in_(major_scope if major_scope else [-1]))

        if minor_version_id:
            q = q.filter(FieldTestRecord.minor_version_id == minor_version_id)
        if purpose_type:
            q = q.filter(FieldTestRecord.purpose_type == purpose_type)
        q = self._apply_keyword_filter(q, keyword)

        if current_user.role != UserRole.ADMIN:
            q = q.filter(FieldTestRecord.tester_id == current_user.id)
        elif tester_id:
            q = q.filter(FieldTestRecord.tester_id == tester_id)

        rows = q.order_by(FieldTestRecord.start_time.desc(), FieldTestRecord.id.desc()).all()
        return [self._record_to_dict(r) for r in rows]

    def _apply_keyword_filter(self, q, keyword: str | None):
        kw_raw = (keyword or "").strip()
        if not kw_raw:
            return q

        kw = f"%{kw_raw}%"
        bug_record_subq = (
            self.db.query(FieldTestBugLink.field_test_record_id)
            .join(BugTracking, FieldTestBugLink.bug_tracking_id == BugTracking.id)
            .filter(BugTracking.bug_id.like(kw))
            .subquery()
        )

        return (
            q.outerjoin(Requirement, FieldTestRecord.requirement_id == Requirement.id)
            .outerjoin(User, FieldTestRecord.tester_id == User.id)
            .filter(
                or_(
                    FieldTestRecord.test_content.like(kw),
                    FieldTestRecord.notes.like(kw),
                    Requirement.zentao_req_id.like(kw),
                    Requirement.title.like(kw),
                    User.display_name.like(kw),
                    User.username.like(kw),
                    FieldTestRecord.id.in_(self.db.query(bug_record_subq.c.field_test_record_id)),
                )
            )
        )

    def list_records_paged(
        self,
        *,
        current_user: User,
        major_version_id: int | None = None,
        minor_version_id: int | None = None,
        purpose_type: FieldTestPurposeType | None = None,
        tester_id: int | None = None,
        software_id: int | None = None,
        keyword: str | None = None,
        page: int = 1,
        page_size: int = 20,
        sort_by: str = "start_time",
        sort_order: str = "desc",
    ) -> dict:
        q = self.db.query(FieldTestRecord).options(
            joinedload(FieldTestRecord.major_version),
            joinedload(FieldTestRecord.minor_version),
            joinedload(FieldTestRecord.requirement),
            joinedload(FieldTestRecord.tester),
            joinedload(FieldTestRecord.bug_links).joinedload(FieldTestBugLink.bug),
        )
        major_scope = self._resolve_major_scope(major_version_id, software_id)
        if major_scope is not None:
            q = q.filter(FieldTestRecord.major_version_id.in_(major_scope if major_scope else [-1]))
        if minor_version_id:
            q = q.filter(FieldTestRecord.minor_version_id == minor_version_id)
        if purpose_type:
            q = q.filter(FieldTestRecord.purpose_type == purpose_type)
        q = self._apply_keyword_filter(q, keyword)
        if current_user.role != UserRole.ADMIN:
            q = q.filter(FieldTestRecord.tester_id == current_user.id)
        elif tester_id:
            q = q.filter(FieldTestRecord.tester_id == tester_id)

        sort_map = {
            "start_time": FieldTestRecord.start_time,
            "end_time": FieldTestRecord.end_time,
            "duration_minutes": FieldTestRecord.duration_minutes,
            "created_at": FieldTestRecord.created_at,
            "updated_at": FieldTestRecord.updated_at,
        }
        col = sort_map.get(sort_by, FieldTestRecord.start_time)
        q = q.order_by(desc(col) if sort_order == "desc" else asc(col), FieldTestRecord.id.desc())

        page = max(1, page)
        page_size = max(1, min(100, page_size))
        total = q.count()
        rows = q.offset((page - 1) * page_size).limit(page_size).all()
        return {
            "items": [self._record_to_dict(r) for r in rows],
            "total": total,
            "page": page,
            "page_size": page_size,
        }

    def get_detail(self, record_id: int, current_user: User) -> dict:
        row = (
            self.db.query(FieldTestRecord)
            .options(
                joinedload(FieldTestRecord.major_version),
                joinedload(FieldTestRecord.minor_version),
                joinedload(FieldTestRecord.requirement),
                joinedload(FieldTestRecord.tester),
                joinedload(FieldTestRecord.bug_links).joinedload(FieldTestBugLink.bug),
            )
            .filter(FieldTestRecord.id == record_id)
            .first()
        )
        if not row:
            raise HTTPException(status_code=404, detail="外业测试记录不存在")
        self._ensure_access(row, current_user)
        return self._record_to_dict(row)

    def unlink_bug(self, record_id: int, bug_tracking_id: int, current_user: User) -> dict:
        row = self.db.query(FieldTestRecord).filter(FieldTestRecord.id == record_id).first()
        if not row:
            raise HTTPException(status_code=404, detail="外业测试记录不存在")
        self._ensure_access(row, current_user)

        link = (
            self.db.query(FieldTestBugLink)
            .filter(
                FieldTestBugLink.field_test_record_id == record_id,
                FieldTestBugLink.bug_tracking_id == bug_tracking_id,
            )
            .first()
        )
        if not link:
            raise HTTPException(status_code=404, detail="该 Bug 关联不存在")

        self.db.delete(link)
        self.db.commit()
        audit(
            self.db,
            action="field_test.unlink_bug",
            target_type="field_test",
            actor_id=current_user.id,
            target_id=str(record_id),
            detail=f"bug={bug_tracking_id}",
        )
        return {"message": "已解除该 Bug 关联"}

    def add_bug_by_no(self, record_id: int, bug_id: str, current_user: User) -> dict:
        row = self.db.query(FieldTestRecord).filter(FieldTestRecord.id == record_id).first()
        if not row:
            raise HTTPException(status_code=404, detail="外业测试记录不存在")
        self._ensure_access(row, current_user)

        bug_no = (bug_id or "").strip()
        if not B_PATTERN.match(bug_no):
            raise HTTPException(status_code=400, detail="Bug 编号格式错误，应为 b#数字")

        bug = self.db.query(BugTracking).filter(BugTracking.bug_id == bug_no).first()
        if bug and bug.major_version_id != row.major_version_id:
            raise HTTPException(status_code=400, detail=f"Bug 编号 {bug_no} 已存在且不属于当前大版本，无法关联")
        created = False
        if not bug:
            bug = BugTracking(
                major_version_id=row.major_version_id,
                requirement_id=row.requirement_id if row.purpose_type == FieldTestPurposeType.REQUIREMENT else None,
                source_type=BugSourceType.FIELD_TEST,
                source_ref=f"field_test:{row.id}",
                bug_id=bug_no,
                found_minor_version_id=row.minor_version_id,
                created_by_id=current_user.id,
            )
            self.db.add(bug)
            self.db.flush()
            created = True

        link = self.db.query(FieldTestBugLink).filter(
            FieldTestBugLink.field_test_record_id == row.id,
            FieldTestBugLink.bug_tracking_id == bug.id,
        ).first()
        if not link:
            self.db.add(FieldTestBugLink(field_test_record_id=row.id, bug_tracking_id=bug.id))
            self.db.commit()
            if created:
                audit(self.db, action="bug.create", target_type="bug", actor_id=current_user.id, target_id=str(bug.id), detail=bug.bug_id)
            audit(
                self.db,
                action="field_test.add_bug",
                target_type="field_test",
                actor_id=current_user.id,
                target_id=str(row.id),
                detail=f"bug={bug_no}",
            )
            return {
                "message": "已新增并关联 Bug" if created else "已关联现有 Bug",
                "bug_id": bug.id,
                "bug_no": bug.bug_id,
                "created": created,
            }

        return {"message": "该 Bug 已关联", "bug_id": bug.id, "bug_no": bug.bug_id, "created": created}

    def search_existing_bugs(self, record_id: int, keyword: str | None = None, limit: int = 20, current_user: User | None = None) -> list[dict]:
        row = self.db.query(FieldTestRecord).filter(FieldTestRecord.id == record_id).first()
        if not row:
            raise HTTPException(status_code=404, detail="外业测试记录不存在")
        if current_user is not None:
            self._ensure_access(row, current_user)

        q = self.db.query(BugTracking).filter(BugTracking.major_version_id == row.major_version_id)
        kw = (keyword or "").strip()
        if kw:
            q = q.filter(BugTracking.bug_id.like(f"%{kw}%"))
        rows = q.order_by(BugTracking.id.desc()).limit(max(1, min(limit, 100))).all()
        return [{"id": b.id, "bug_id": b.bug_id} for b in rows]

    def link_existing_bug(self, record_id: int, bug_tracking_id: int, current_user: User) -> dict:
        row = self.db.query(FieldTestRecord).filter(FieldTestRecord.id == record_id).first()
        if not row:
            raise HTTPException(status_code=404, detail="外业测试记录不存在")
        self._ensure_access(row, current_user)

        bug = self.db.query(BugTracking).filter(BugTracking.id == bug_tracking_id).first()
        if not bug:
            raise HTTPException(status_code=404, detail="Bug 不存在")
        if bug.major_version_id != row.major_version_id:
            raise HTTPException(status_code=400, detail="该 Bug 不属于当前外业记录的大版本，无法关联")

        link = self.db.query(FieldTestBugLink).filter(
            FieldTestBugLink.field_test_record_id == row.id,
            FieldTestBugLink.bug_tracking_id == bug.id,
        ).first()
        if link:
            return {"message": "该 Bug 已关联", "bug_id": bug.id, "bug_no": bug.bug_id}

        self.db.add(FieldTestBugLink(field_test_record_id=row.id, bug_tracking_id=bug.id))
        self.db.commit()
        audit(
            self.db,
            action="field_test.link_bug",
            target_type="field_test",
            actor_id=current_user.id,
            target_id=str(row.id),
            detail=f"bug={bug.bug_id}",
        )
        return {"message": "已关联现有 Bug", "bug_id": bug.id, "bug_no": bug.bug_id}

    def options(self, major_version_id: int) -> dict:
        major = self.db.query(Version).filter(Version.id == major_version_id, Version.version_type == VersionType.MAJOR).first()
        if not major:
            raise HTTPException(status_code=400, detail="大版本无效")
        minors = self.db.query(Version).filter(Version.version_type == VersionType.MINOR, Version.parent_id == major.id).order_by(Version.id.asc()).all()
        reqs = self.db.query(Requirement).filter(Requirement.major_version_id == major.id).order_by(Requirement.id.asc()).all()
        return {
            "minors": [{"id": m.id, "version_no": m.version_no} for m in minors],
            "requirements": [{"id": r.id, "label": f"{r.zentao_req_id} {r.title}"} for r in reqs],
        }

    def report_stats(
        self,
        *,
        start_date: date,
        end_date: date,
        current_user: User,
        user_id: int | None = None,
        major_version_id: int | None = None,
        software_id: int | None = None,
    ) -> dict:
        all_users_mode = user_id in (None, 0)
        if all_users_mode and current_user.role != UserRole.ADMIN:
            target_user_id = current_user.id
            all_users_mode = False
        else:
            target_user_id = current_user.id if user_id is None else user_id

        if (not all_users_mode) and target_user_id != current_user.id and current_user.role != UserRole.ADMIN:
            raise HTTPException(status_code=403, detail="无权限查看其他人的外业测试统计")

        sdt = datetime.combine(start_date, datetime.min.time())
        edt = datetime.combine(end_date, datetime.max.time())

        major_scope = self._resolve_major_scope(major_version_id, software_id)
        team_ids: list[int] = []
        if all_users_mode:
            team_ids = [u.id for u in self.db.query(User).filter(User.is_team_member.is_(True)).all()]

        q = self.db.query(FieldTestRecord).filter(FieldTestRecord.start_time >= sdt, FieldTestRecord.start_time <= edt)
        if major_scope is not None:
            q = q.filter(FieldTestRecord.major_version_id.in_(major_scope if major_scope else [-1]))
        if all_users_mode:
            q = q.filter(FieldTestRecord.tester_id.in_(team_ids if team_ids else [-1]))
        else:
            q = q.filter(FieldTestRecord.tester_id == target_user_id)

        rows = q.all()
        by_day: dict[str, int] = {}
        by_purpose: dict[str, dict] = {}
        by_user: dict[int, dict] = {}

        user_ids = list({r.tester_id for r in rows if r.tester_id})
        users = self.db.query(User).filter(User.id.in_(user_ids)).all() if user_ids else []
        user_map = {u.id: u.shown_name for u in users}

        total_minutes = 0
        for r in rows:
            minutes = int(r.duration_minutes or 0)
            total_minutes += minutes
            day_key = r.start_time.date().isoformat()
            by_day[day_key] = by_day.get(day_key, 0) + minutes

            pkey = r.purpose_type.value if hasattr(r.purpose_type, "value") else str(r.purpose_type)
            bucket = by_purpose.get(pkey, {"minutes": 0, "count": 0})
            bucket["minutes"] += minutes
            bucket["count"] += 1
            by_purpose[pkey] = bucket

            ub = by_user.get(r.tester_id, {"minutes": 0, "count": 0})
            ub["minutes"] += minutes
            ub["count"] += 1
            by_user[r.tester_id] = ub

        day_list = []
        cur = start_date
        while cur <= end_date:
            key = cur.isoformat()
            day_list.append({"date": key, "minutes": by_day.get(key, 0)})
            cur = cur.fromordinal(cur.toordinal() + 1)

        return {
            "overview": {
                "total_records": len(rows),
                "total_minutes": total_minutes,
                "total_hours": round(total_minutes / 60.0, 2),
            },
            "by_day": day_list,
            "by_purpose": [
                {"purpose_type": k, "minutes": v["minutes"], "count": v["count"]}
                for k, v in by_purpose.items()
            ],
            "by_user": [
                {
                    "user_id": uid,
                    "username": user_map.get(uid, f"用户#{uid}"),
                    "minutes": info["minutes"],
                    "count": info["count"],
                }
                for uid, info in by_user.items()
            ] if all_users_mode else [],
            "mode": "all_users" if all_users_mode else "personal",
            "target_user_id": None if all_users_mode else target_user_id,
        }
