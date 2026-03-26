from __future__ import annotations

import mimetypes
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import HTTPException, UploadFile
from sqlalchemy import asc, desc
from sqlalchemy.orm import Session, joinedload

from app.models import AuditLog, BugSourceType, BugTracking, FeedbackAttachment, FeedbackBugLink, FeedbackRecord, FeedbackStatus, User, Version
from app.services.audit_service import audit
from app.services.sse_service import sse_publish

F_PATTERN = re.compile(r"^\d+$")
B_PATTERN = re.compile(r"^b#\d+$")
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".svg"}
UPLOAD_ROOT = Path(__file__).resolve().parents[2] / "uploads" / "feedback"


class FeedbackService:
    def __init__(self, db: Session):
        self.db = db

    def _ensure_version_pair(self, major_version_id: int, minor_version_id: int) -> tuple[Version, Version]:
        major = self.db.query(Version).filter(Version.id == major_version_id).first()
        minor = self.db.query(Version).filter(Version.id == minor_version_id).first()
        if not major or major.version_type.value != "major":
            raise HTTPException(status_code=400, detail="大版本无效")
        if not minor or minor.version_type.value != "minor":
            raise HTTPException(status_code=400, detail="小版本无效")
        if minor.parent_id != major.id:
            raise HTTPException(status_code=400, detail="小版本不属于所选大版本")
        return major, minor

    def _normalize_feedback_no(self, raw_num: Optional[str]) -> Optional[str]:
        if raw_num is None:
            return None
        raw_num = raw_num.strip()
        if raw_num == "":
            return None
        if not F_PATTERN.match(raw_num):
            raise HTTPException(status_code=400, detail="反馈编号只能填写数字")
        feedback_no = f"f#{raw_num}"
        existing = self.db.query(FeedbackRecord).filter(FeedbackRecord.feedback_no == feedback_no).first()
        if existing:
            raise HTTPException(status_code=400, detail=f"反馈编号 {feedback_no} 已存在")
        return feedback_no

    def _assert_status_transition(self, old_status: FeedbackStatus, new_status: FeedbackStatus, actor: User) -> None:
        if old_status == new_status:
            return
        if actor.role.value == "admin":
            allowed = {
                FeedbackStatus.PENDING: {FeedbackStatus.PROCESSING, FeedbackStatus.RESOLVED, FeedbackStatus.CLOSED},
                FeedbackStatus.PROCESSING: {FeedbackStatus.PENDING, FeedbackStatus.RESOLVED, FeedbackStatus.CLOSED},
                FeedbackStatus.RESOLVED: {FeedbackStatus.PROCESSING, FeedbackStatus.CLOSED},
                FeedbackStatus.CLOSED: {FeedbackStatus.PROCESSING},
            }
        else:
            allowed = {
                FeedbackStatus.PENDING: {FeedbackStatus.PROCESSING},
                FeedbackStatus.PROCESSING: {FeedbackStatus.RESOLVED},
                FeedbackStatus.RESOLVED: {FeedbackStatus.PROCESSING},
                FeedbackStatus.CLOSED: set(),
            }
        if new_status not in allowed.get(old_status, set()):
            raise HTTPException(status_code=400, detail=f"非法状态流转：{old_status.value} -> {new_status.value}")

    def list_feedbacks(
        self,
        *,
        keyword: Optional[str] = None,
        status: Optional[FeedbackStatus] = None,
        assignee_id: Optional[int] = None,
        major_version_id: Optional[int] = None,
        minor_version_id: Optional[int] = None,
        software_id: Optional[int] = None,
    ) -> list[dict]:
        q = self.db.query(FeedbackRecord).options(
            joinedload(FeedbackRecord.creator),
            joinedload(FeedbackRecord.assignee),
            joinedload(FeedbackRecord.major_version),
            joinedload(FeedbackRecord.minor_version),
        )
        if keyword:
            kw = f"%{keyword.strip()}%"
            q = q.filter((FeedbackRecord.feedback_no.like(kw)) | (FeedbackRecord.summary.like(kw)))
        if status:
            q = q.filter(FeedbackRecord.status == status)
        if assignee_id:
            q = q.filter(FeedbackRecord.assignee_id == assignee_id)
        if major_version_id:
            q = q.filter(FeedbackRecord.major_version_id == major_version_id)
        elif software_id:
            q = q.join(Version, FeedbackRecord.major_version_id == Version.id).filter(Version.software_id == software_id)
        if minor_version_id:
            q = q.filter(FeedbackRecord.minor_version_id == minor_version_id)
        rows = q.order_by(FeedbackRecord.id.desc()).all()
        return [
            {
                "id": r.id,
                "feedback_no": r.feedback_no,
                "summary": r.summary,
                "status": r.status.value,
                "major_version_id": r.major_version_id,
                "major_version_no": r.major_version.version_no if r.major_version else None,
                "minor_version_id": r.minor_version_id,
                "minor_version_no": r.minor_version.version_no if r.minor_version else None,
                "creator_id": r.creator_id,
                "creator_name": r.creator.shown_name if r.creator else None,
                "assignee_id": r.assignee_id,
                "assignee_name": r.assignee.shown_name if r.assignee else None,
                "created_at": r.created_at.isoformat(),
            }
            for r in rows
        ]

    def list_feedbacks_paged(
        self,
        *,
        keyword: Optional[str] = None,
        status: Optional[FeedbackStatus] = None,
        assignee_id: Optional[int] = None,
        major_version_id: Optional[int] = None,
        minor_version_id: Optional[int] = None,
        software_id: Optional[int] = None,
        page: int = 1,
        page_size: int = 10,
        sort_by: str = "created_at",
        sort_order: str = "desc",
    ) -> dict:
        q = self.db.query(FeedbackRecord).options(
            joinedload(FeedbackRecord.creator),
            joinedload(FeedbackRecord.assignee),
            joinedload(FeedbackRecord.major_version),
            joinedload(FeedbackRecord.minor_version),
        )
        if keyword:
            kw = f"%{keyword.strip()}%"
            q = q.filter((FeedbackRecord.feedback_no.like(kw)) | (FeedbackRecord.summary.like(kw)))
        if status:
            q = q.filter(FeedbackRecord.status == status)
        if assignee_id:
            q = q.filter(FeedbackRecord.assignee_id == assignee_id)
        if major_version_id:
            q = q.filter(FeedbackRecord.major_version_id == major_version_id)
        elif software_id:
            q = q.join(Version, FeedbackRecord.major_version_id == Version.id).filter(Version.software_id == software_id)
        if minor_version_id:
            q = q.filter(FeedbackRecord.minor_version_id == minor_version_id)

        sort_fields = {
            "created_at": FeedbackRecord.created_at,
            "updated_at": FeedbackRecord.updated_at,
            "status": FeedbackRecord.status,
            "feedback_no": FeedbackRecord.feedback_no,
        }
        sort_col = sort_fields.get(sort_by, FeedbackRecord.created_at)
        q = q.order_by(desc(sort_col) if sort_order == "desc" else asc(sort_col))

        page = max(1, page)
        page_size = max(1, min(100, page_size))
        total = q.count()
        rows = q.offset((page - 1) * page_size).limit(page_size).all()
        items = [
            {
                "id": r.id,
                "feedback_no": r.feedback_no,
                "summary": r.summary,
                "status": r.status.value,
                "major_version_id": r.major_version_id,
                "major_version_no": r.major_version.version_no if r.major_version else None,
                "minor_version_id": r.minor_version_id,
                "minor_version_no": r.minor_version.version_no if r.minor_version else None,
                "creator_id": r.creator_id,
                "creator_name": r.creator.shown_name if r.creator else None,
                "assignee_id": r.assignee_id,
                "assignee_name": r.assignee.shown_name if r.assignee else None,
                "created_at": r.created_at.isoformat(),
            }
            for r in rows
        ]
        return {"items": items, "total": total, "page": page, "page_size": page_size}

    def create_feedback(self, *, creator: User, feedback_no_num: Optional[str], major_version_id: int, minor_version_id: int, summary: str) -> dict:
        self._ensure_version_pair(major_version_id, minor_version_id)
        feedback_no = self._normalize_feedback_no(feedback_no_num)
        row = FeedbackRecord(
            feedback_no=feedback_no,
            major_version_id=major_version_id,
            minor_version_id=minor_version_id,
            summary=summary.strip(),
            status=FeedbackStatus.PENDING,
            creator_id=creator.id,
        )
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        audit(self.db, action="feedback.create", target_type="feedback", actor_id=creator.id, target_id=str(row.id), detail=row.feedback_no or "-")
        sse_publish(
            "feedback_task_created",
            {
                "id": row.id,
                "status": row.status.value,
                "summary": row.summary,
                "assignee_id": row.assignee_id,
                "creator_id": row.creator_id,
            },
            channels=["global"],
        )
        return {"id": row.id, "message": "反馈已创建"}

    def get_feedback_detail(self, feedback_id: int) -> dict:
        row = (
            self.db.query(FeedbackRecord)
            .options(
                joinedload(FeedbackRecord.creator),
                joinedload(FeedbackRecord.assignee),
                joinedload(FeedbackRecord.handler),
                joinedload(FeedbackRecord.major_version),
                joinedload(FeedbackRecord.minor_version),
                joinedload(FeedbackRecord.handled_major_version),
                joinedload(FeedbackRecord.handled_minor_version),
                joinedload(FeedbackRecord.attachments),
                joinedload(FeedbackRecord.bug_links).joinedload(FeedbackBugLink.bug),
            )
            .filter(FeedbackRecord.id == feedback_id)
            .first()
        )
        if not row:
            raise HTTPException(status_code=404, detail="反馈不存在")
        return {
            "id": row.id,
            "feedback_no": row.feedback_no,
            "summary": row.summary,
            "status": row.status.value,
            "major_version_id": row.major_version_id,
            "major_version_no": row.major_version.version_no if row.major_version else None,
            "minor_version_id": row.minor_version_id,
            "minor_version_no": row.minor_version.version_no if row.minor_version else None,
            "creator_name": row.creator.shown_name if row.creator else None,
            "assignee_id": row.assignee_id,
            "assignee_name": row.assignee.shown_name if row.assignee else None,
            "handling_result": row.handling_result,
            "handled_major_version_id": row.handled_major_version_id,
            "handled_major_version_no": row.handled_major_version.version_no if row.handled_major_version else None,
            "handled_minor_version_id": row.handled_minor_version_id,
            "handled_minor_version_no": row.handled_minor_version.version_no if row.handled_minor_version else None,
            "handled_by_name": row.handler.shown_name if row.handler else None,
            "handled_at": row.handled_at.isoformat() if row.handled_at else None,
            "created_at": row.created_at.isoformat(),
            "attachments": [
                {
                    "id": a.id,
                    "original_name": a.original_name,
                    "file_type": a.file_type,
                    "file_ext": a.file_ext,
                    "file_size": a.file_size,
                    "is_image": a.is_image,
                    "created_at": a.created_at.isoformat(),
                }
                for a in row.attachments
            ],
            "bugs": [{"id": l.bug.id, "bug_id": l.bug.bug_id, "zentao_bug_url": l.bug.zentao_bug_url, "zentao_bug_title": l.bug.zentao_bug_title} for l in row.bug_links if l.bug],
        }

    def assign_feedback(self, *, feedback_id: int, assignee_id: int, status: Optional[FeedbackStatus], actor: User) -> tuple[dict, FeedbackRecord, User]:
        row = self.db.query(FeedbackRecord).filter(FeedbackRecord.id == feedback_id).first()
        if not row:
            raise HTTPException(status_code=404, detail="反馈不存在")
        user = self.db.query(User).filter(User.id == assignee_id).first()
        if not user:
            raise HTTPException(status_code=404, detail="处理人不存在")
        row.assignee_id = assignee_id
        target_status = status or FeedbackStatus.PROCESSING
        self._assert_status_transition(row.status, target_status, actor)
        row.status = target_status
        self.db.commit()
        audit(self.db, action="feedback.assign", target_type="feedback", actor_id=actor.id, target_id=str(row.id), detail=f"assignee={assignee_id}")
        sse_publish(
            "feedback_task_updated",
            {"id": row.id, "status": row.status.value, "assignee_id": row.assignee_id},
            channels=["global", f"user:{assignee_id}"],
        )
        return {"message": "反馈已指派"}, row, user

    def handle_feedback(
        self,
        *,
        feedback_id: int,
        handling_result: str,
        handled_major_version_id: int,
        handled_minor_version_id: int,
        status: FeedbackStatus,
        actor: User,
    ) -> dict:
        row = self.db.query(FeedbackRecord).filter(FeedbackRecord.id == feedback_id).first()
        if not row:
            raise HTTPException(status_code=404, detail="反馈不存在")
        if row.assignee_id and row.assignee_id != actor.id and actor.role.value != "admin":
            raise HTTPException(status_code=403, detail="仅指派处理人可填写处理结果")
        self._ensure_version_pair(handled_major_version_id, handled_minor_version_id)
        row.handling_result = handling_result.strip()
        row.handled_major_version_id = handled_major_version_id
        row.handled_minor_version_id = handled_minor_version_id
        row.handled_by_id = actor.id
        row.handled_at = datetime.utcnow()
        self._assert_status_transition(row.status, status, actor)
        row.status = status
        self.db.commit()
        audit(self.db, action="feedback.handle", target_type="feedback", actor_id=actor.id, target_id=str(row.id), detail=f"status={status.value}")
        sse_publish(
            "feedback_task_updated",
            {"id": row.id, "status": row.status.value, "assignee_id": row.assignee_id},
            channels=["global", f"user:{row.assignee_id}"] if row.assignee_id else ["global"],
        )
        return {"message": "处理结果已保存"}

    def update_status(self, *, feedback_id: int, status: FeedbackStatus, actor: User) -> dict:
        row = self.db.query(FeedbackRecord).filter(FeedbackRecord.id == feedback_id).first()
        if not row:
            raise HTTPException(status_code=404, detail="反馈不存在")
        if actor.role.value != "admin" and (not row.assignee_id or row.assignee_id != actor.id):
            raise HTTPException(status_code=403, detail="仅管理员或指派处理人可修改状态")
        self._assert_status_transition(row.status, status, actor)
        row.status = status
        self.db.commit()
        audit(self.db, action="feedback.status", target_type="feedback", actor_id=actor.id, target_id=str(row.id), detail=status.value)
        sse_publish(
            "feedback_task_updated",
            {"id": row.id, "status": row.status.value, "assignee_id": row.assignee_id},
            channels=["global", f"user:{row.assignee_id}"] if row.assignee_id else ["global"],
        )
        return {"message": "状态已更新"}

    def save_attachment(self, *, feedback_id: int, upload_file: UploadFile, actor: User) -> dict:
        row = self.db.query(FeedbackRecord).filter(FeedbackRecord.id == feedback_id).first()
        if not row:
            raise HTTPException(status_code=404, detail="反馈不存在")
        now = datetime.utcnow()
        folder = UPLOAD_ROOT / now.strftime("%Y") / now.strftime("%m")
        folder.mkdir(parents=True, exist_ok=True)
        suffix = Path(upload_file.filename or "").suffix.lower()
        stored_name = f"{uuid.uuid4().hex}{suffix}"
        full_path = folder / stored_name

        content = upload_file.file.read()
        with open(full_path, "wb") as f:
            f.write(content)

        guessed_type = upload_file.content_type or mimetypes.guess_type(upload_file.filename or "")[0] or "application/octet-stream"
        file_size = len(content)
        is_image = suffix in IMAGE_EXTS or guessed_type.startswith("image/")
        rel_path = str(full_path.relative_to(Path(__file__).resolve().parents[2])).replace("\\", "/")

        att = FeedbackAttachment(
            feedback_id=feedback_id,
            original_name=upload_file.filename or stored_name,
            stored_name=stored_name,
            file_path=rel_path,
            file_type=guessed_type,
            file_ext=suffix,
            file_size=file_size,
            is_image=is_image,
            uploaded_by_id=actor.id,
        )
        self.db.add(att)
        self.db.commit()
        self.db.refresh(att)
        audit(self.db, action="feedback.upload_attachment", target_type="feedback", actor_id=actor.id, target_id=str(feedback_id), detail=att.original_name)
        return {"id": att.id, "message": "附件上传成功"}

    def list_attachments(self, feedback_id: int) -> list[dict]:
        rows = self.db.query(FeedbackAttachment).filter(FeedbackAttachment.feedback_id == feedback_id).order_by(FeedbackAttachment.id.desc()).all()
        return [
            {
                "id": a.id,
                "original_name": a.original_name,
                "file_type": a.file_type,
                "file_ext": a.file_ext,
                "file_size": a.file_size,
                "is_image": a.is_image,
                "created_at": a.created_at.isoformat(),
            }
            for a in rows
        ]

    def get_attachment(self, attachment_id: int) -> FeedbackAttachment:
        row = self.db.query(FeedbackAttachment).filter(FeedbackAttachment.id == attachment_id).first()
        if not row:
            raise HTTPException(status_code=404, detail="附件不存在")
        return row

    def delete_attachment(self, attachment_id: int, actor: User) -> dict:
        row = self.db.query(FeedbackAttachment).options(joinedload(FeedbackAttachment.feedback)).filter(FeedbackAttachment.id == attachment_id).first()
        if not row:
            raise HTTPException(status_code=404, detail="附件不存在")
        feedback = row.feedback
        if actor.role.value != "admin":
            allowed = {row.uploaded_by_id}
            if feedback:
                if feedback.creator_id:
                    allowed.add(feedback.creator_id)
                if feedback.assignee_id:
                    allowed.add(feedback.assignee_id)
            if actor.id not in allowed:
                raise HTTPException(status_code=403, detail="无权限删除该附件")
        root = Path(__file__).resolve().parents[2]
        full_path = root / row.file_path
        if full_path.exists():
            try:
                full_path.unlink()
            except Exception:
                pass
        fid = row.feedback_id
        oname = row.original_name
        self.db.delete(row)
        self.db.commit()
        audit(self.db, action="feedback.delete_attachment", target_type="feedback", actor_id=actor.id, target_id=str(fid), detail=oname)
        return {"message": "附件已删除"}

    def link_existing_bug(self, *, feedback_id: int, bug_id: int, actor: User) -> dict:
        feedback = self.db.query(FeedbackRecord).filter(FeedbackRecord.id == feedback_id).first()
        if not feedback:
            raise HTTPException(status_code=404, detail="反馈不存在")
        bug = self.db.query(BugTracking).filter(BugTracking.id == bug_id).first()
        if not bug:
            raise HTTPException(status_code=404, detail="Bug 不存在")
        exists = self.db.query(FeedbackBugLink).filter(FeedbackBugLink.feedback_id == feedback_id, FeedbackBugLink.bug_id == bug_id).first()
        if exists:
            return {"message": "Bug 已关联"}
        self.db.add(FeedbackBugLink(feedback_id=feedback_id, bug_id=bug_id, created_by_id=actor.id))
        self.db.commit()
        audit(self.db, action="feedback.link_bug", target_type="feedback", actor_id=actor.id, target_id=str(feedback_id), detail=f"bug={bug_id}")
        return {"message": "关联成功"}

    def create_bug_and_link(self, *, feedback_id: int, bug_no: str, actor: User) -> dict:
        feedback = self.db.query(FeedbackRecord).filter(FeedbackRecord.id == feedback_id).first()
        if not feedback:
            raise HTTPException(status_code=404, detail="反馈不存在")
        if not B_PATTERN.match(bug_no):
            raise HTTPException(status_code=400, detail="bug_id 格式应为 b#数字")
        if self.db.query(BugTracking).filter(BugTracking.bug_id == bug_no).first():
            raise HTTPException(status_code=400, detail=f"Bug 编号 {bug_no} 已存在")

        bug = BugTracking(
            major_version_id=feedback.major_version_id,
            requirement_id=None,
            source_type=BugSourceType.MANUAL,
            source_ref=f"feedback:{feedback.id}",
            bug_id=bug_no,
            found_minor_version_id=feedback.minor_version_id,
            created_by_id=actor.id,
        )
        self.db.add(bug)
        self.db.flush()
        self.db.add(FeedbackBugLink(feedback_id=feedback_id, bug_id=bug.id, created_by_id=actor.id))
        self.db.commit()
        audit(self.db, action="bug.create", target_type="bug", actor_id=actor.id, target_id=str(bug.id), detail=bug.bug_id)
        audit(self.db, action="feedback.create_bug_link", target_type="feedback", actor_id=actor.id, target_id=str(feedback_id), detail=bug_no)
        return {"bug_id": bug.id, "message": "已新建 Bug 并关联"}

    def list_linked_bugs(self, feedback_id: int) -> list[dict]:
        rows = (
            self.db.query(FeedbackBugLink)
            .options(joinedload(FeedbackBugLink.bug))
            .filter(FeedbackBugLink.feedback_id == feedback_id)
            .order_by(FeedbackBugLink.id.desc())
            .all()
        )
        return [{"id": l.bug.id, "bug_id": l.bug.bug_id, "zentao_bug_url": l.bug.zentao_bug_url, "zentao_bug_title": l.bug.zentao_bug_title} for l in rows if l.bug]

    def unlink_bug(self, feedback_id: int, bug_id: int, actor: User) -> dict:
        link = self.db.query(FeedbackBugLink).filter(FeedbackBugLink.feedback_id == feedback_id, FeedbackBugLink.bug_id == bug_id).first()
        if not link:
            raise HTTPException(status_code=404, detail="关联关系不存在")
        self.db.delete(link)
        self.db.commit()
        audit(self.db, action="feedback.unlink_bug", target_type="feedback", actor_id=actor.id, target_id=str(feedback_id), detail=f"bug={bug_id}")
        return {"message": "已解除 Bug 关联"}

    def search_existing_bugs(self, keyword: Optional[str] = None, software_id: Optional[int] = None, limit: int = 20) -> list[dict]:
        q = self.db.query(BugTracking)
        if keyword:
            kw = f"%{keyword.strip()}%"
            q = q.filter(BugTracking.bug_id.like(kw))
        if software_id:
            q = q.join(Version, BugTracking.major_version_id == Version.id).filter(Version.software_id == software_id)
        rows = q.order_by(BugTracking.id.desc()).limit(max(1, min(limit, 100))).all()
        return [{"id": r.id, "bug_id": r.bug_id, "zentao_bug_url": r.zentao_bug_url, "zentao_bug_title": r.zentao_bug_title} for r in rows]

    def list_my_todo(self, current_user: User, major_version_id: Optional[int] = None) -> list[dict]:
        q = self.db.query(FeedbackRecord).options(joinedload(FeedbackRecord.major_version), joinedload(FeedbackRecord.minor_version)).filter(
            FeedbackRecord.assignee_id == current_user.id,
            FeedbackRecord.status.in_([FeedbackStatus.PENDING, FeedbackStatus.PROCESSING]),
        )
        if major_version_id:
            q = q.filter(FeedbackRecord.major_version_id == major_version_id)
        rows = q.order_by(FeedbackRecord.updated_at.desc(), FeedbackRecord.id.desc()).all()
        return [
            {
                "id": r.id,
                "feedback_no": r.feedback_no,
                "summary": r.summary,
                "status": r.status.value,
                "major_version_no": r.major_version.version_no if r.major_version else None,
                "minor_version_no": r.minor_version.version_no if r.minor_version else None,
                "created_at": r.created_at.isoformat(),
                "updated_at": r.updated_at.isoformat(),
            }
            for r in rows
        ]

    def timeline(self, feedback_id: int) -> list[dict]:
        logs = (
            self.db.query(AuditLog)
            .filter(AuditLog.target_type == "feedback", AuditLog.target_id == str(feedback_id))
            .order_by(AuditLog.id.desc())
            .all()
        )
        actor_ids = list({log.actor_id for log in logs if log.actor_id})
        users = self.db.query(User).filter(User.id.in_(actor_ids)).all() if actor_ids else []
        umap = {u.id: u.shown_name for u in users}
        return [
            {
                "id": log.id,
                "action": log.action,
                "detail": log.detail,
                "actor_name": umap.get(log.actor_id, "系统"),
                "created_at": log.created_at.isoformat(),
            }
            for log in logs
        ]
