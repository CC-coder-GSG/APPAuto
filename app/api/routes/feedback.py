from __future__ import annotations

from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, Query, UploadFile
from fastapi import HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.models import FeedbackStatus, User
from app.schemas.feedback import (
    FeedbackAssignPayload,
    FeedbackCreateBugPayload,
    FeedbackCreatePayload,
    FeedbackHandlePayload,
    FeedbackLinkBugPayload,
    FeedbackStatusPayload,
)
from app.services.feedback_service import FeedbackService
from app.services.permission_service import ensure_admin
from app.services.push_service import PushService

router = APIRouter()


@router.get("/feedbacks")
def list_feedbacks(
    keyword: Optional[str] = None,
    status: Optional[FeedbackStatus] = None,
    assignee_id: Optional[int] = None,
    major_version_id: Optional[int] = None,
    minor_version_id: Optional[int] = None,
    _: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return FeedbackService(db).list_feedbacks(
        keyword=keyword,
        status=status,
        assignee_id=assignee_id,
        major_version_id=major_version_id,
        minor_version_id=minor_version_id,
    )

@router.get("/feedbacks/paged")
def list_feedbacks_paged(
    keyword: Optional[str] = None,
    status: Optional[FeedbackStatus] = None,
    assignee_id: Optional[int] = None,
    major_version_id: Optional[int] = None,
    minor_version_id: Optional[int] = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=10, ge=1, le=100),
    sort_by: str = Query(default="created_at"),
    sort_order: str = Query(default="desc"),
    _: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return FeedbackService(db).list_feedbacks_paged(
        keyword=keyword,
        status=status,
        assignee_id=assignee_id,
        major_version_id=major_version_id,
        minor_version_id=minor_version_id,
        page=page,
        page_size=page_size,
        sort_by=sort_by,
        sort_order=sort_order,
    )


@router.post("/feedbacks")
def create_feedback(payload: FeedbackCreatePayload, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return FeedbackService(db).create_feedback(
        creator=current_user,
        feedback_no_num=payload.feedback_no_num,
        major_version_id=payload.major_version_id,
        minor_version_id=payload.minor_version_id,
        summary=payload.summary,
    )


@router.post("/feedbacks/{feedback_id}/assign")
async def assign_feedback(
    feedback_id: int,
    payload: FeedbackAssignPayload,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    ensure_admin(current_user)
    service = FeedbackService(db)
    result, row, assignee = service.assign_feedback(
        feedback_id=feedback_id,
        assignee_id=payload.assignee_id,
        status=payload.status,
        actor=current_user,
    )
    push = PushService(db)
    await push.push_feedback_assignment_notice(
        feedback_no=row.feedback_no or f"ID:{row.id}",
        summary=row.summary,
        major_version_no=row.major_version.version_no if row.major_version else "-",
        minor_version_no=row.minor_version.version_no if row.minor_version else "-",
        assignee_username=assignee.shown_name,
    )
    return result


@router.post("/feedbacks/{feedback_id}/handle")
def handle_feedback(
    feedback_id: int,
    payload: FeedbackHandlePayload,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return FeedbackService(db).handle_feedback(
        feedback_id=feedback_id,
        handling_result=payload.handling_result,
        handled_major_version_id=payload.handled_major_version_id,
        handled_minor_version_id=payload.handled_minor_version_id,
        status=payload.status,
        actor=current_user,
    )


@router.patch("/feedbacks/{feedback_id}/status")
def update_feedback_status(
    feedback_id: int,
    payload: FeedbackStatusPayload,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return FeedbackService(db).update_status(feedback_id=feedback_id, status=payload.status, actor=current_user)


@router.post("/feedbacks/{feedback_id}/attachments")
def upload_feedback_attachment(
    feedback_id: int,
    file: UploadFile,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return FeedbackService(db).save_attachment(feedback_id=feedback_id, upload_file=file, actor=current_user)


@router.get("/feedbacks/{feedback_id}/attachments")
def list_feedback_attachments(feedback_id: int, _: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return FeedbackService(db).list_attachments(feedback_id)


@router.get("/feedbacks/attachments/{attachment_id}/download")
def download_feedback_attachment(attachment_id: int, _: User = Depends(get_current_user), db: Session = Depends(get_db)):
    row = FeedbackService(db).get_attachment(attachment_id)
    root = Path(__file__).resolve().parents[3]
    full_path = root / row.file_path
    if not full_path.exists():
        raise HTTPException(status_code=404, detail="文件不存在或已被清理")
    return FileResponse(
        str(full_path),
        filename=row.original_name,
        media_type=row.file_type or "application/octet-stream",
    )


@router.delete("/feedbacks/attachments/{attachment_id}")
def delete_feedback_attachment(attachment_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return FeedbackService(db).delete_attachment(attachment_id, current_user)


@router.get("/feedbacks/my-todo")
def list_my_feedback_todo(
    major_version_id: Optional[int] = Query(default=None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return FeedbackService(db).list_my_todo(current_user=current_user, major_version_id=major_version_id)


@router.post("/feedbacks/{feedback_id}/bugs/link")
def link_existing_bug(
    feedback_id: int,
    payload: FeedbackLinkBugPayload,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return FeedbackService(db).link_existing_bug(feedback_id=feedback_id, bug_id=payload.bug_id, actor=current_user)


@router.post("/feedbacks/{feedback_id}/bugs/create-and-link")
def create_bug_and_link(
    feedback_id: int,
    payload: FeedbackCreateBugPayload,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return FeedbackService(db).create_bug_and_link(feedback_id=feedback_id, bug_no=payload.bug_id, actor=current_user)


@router.get("/feedbacks/{feedback_id}/bugs")
def list_feedback_bugs(feedback_id: int, _: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return FeedbackService(db).list_linked_bugs(feedback_id)


@router.delete("/feedbacks/{feedback_id}/bugs/{bug_id}")
def unlink_feedback_bug(feedback_id: int, bug_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return FeedbackService(db).unlink_bug(feedback_id, bug_id, current_user)


@router.get("/feedbacks/bug-options")
def search_bug_options(
    keyword: Optional[str] = None,
    limit: int = 20,
    _: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return FeedbackService(db).search_existing_bugs(keyword=keyword, limit=limit)


@router.get("/feedbacks/{feedback_id}")
def get_feedback_detail(feedback_id: int, _: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return FeedbackService(db).get_feedback_detail(feedback_id)


@router.get("/feedbacks/{feedback_id}/timeline")
def feedback_timeline(feedback_id: int, _: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return FeedbackService(db).timeline(feedback_id)
