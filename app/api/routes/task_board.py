from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.core.exceptions import AppError
from app.models import User
from app.services.permission_service import ensure_tab_access
from app.services.task_board_service import TaskBoardService, can_manage_board
from app.utils.time_utils import local_now

router = APIRouter(prefix="/task-board", tags=["task-board"])


class TaskCreatePayload(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: Optional[str] = None
    board_date: Optional[date] = None
    status: Optional[str] = None
    priority: Optional[str] = None
    assignee_id: Optional[int] = None
    software_id: Optional[int] = None
    major_version_id: Optional[int] = None
    target_type: Optional[str] = None
    target_id: Optional[int] = None
    due_at: Optional[datetime] = None


class TaskUpdatePayload(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    board_date: Optional[date] = None
    priority: Optional[str] = None
    assignee_id: Optional[int] = None
    software_id: Optional[int] = None
    major_version_id: Optional[int] = None
    target_type: Optional[str] = None
    target_id: Optional[int] = None
    due_at: Optional[datetime] = None
    sort_order: Optional[int] = None

    class Config:
        # Treat explicit nulls as "set to null"; missing keys are ignored.
        # Pydantic v2 helper: we filter in the route.
        extra = "ignore"


class TaskStatusPayload(BaseModel):
    status: str
    progress: Optional[str] = None


class TaskProgressPayload(BaseModel):
    content: str = Field(min_length=1)


class TaskCarryOverPayload(BaseModel):
    to_date: date


def _ensure_read(current_user: User) -> None:
    ensure_tab_access(current_user, "task-board", "无权限访问任务看板")


@router.get("/tasks")
def list_tasks(
    board_date: Optional[date] = Query(default=None),
    software_id: Optional[int] = Query(default=None),
    major_version_id: Optional[int] = Query(default=None),
    assignee_id: Optional[int] = Query(default=None),
    status: Optional[str] = Query(default=None),
    mine: bool = Query(default=False),
    include_archived: bool = Query(default=False),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_read(current_user)
    target_date = board_date or local_now().date()
    service = TaskBoardService(db)
    data = service.list_board(
        board_date=target_date,
        software_id=software_id,
        major_version_id=major_version_id,
        assignee_id=assignee_id,
        status=status,
        mine_user_id=current_user.id if mine else None,
        include_archived=include_archived,
    )
    data["can_manage"] = can_manage_board(current_user)
    return data


@router.get("/tasks/{task_id}")
def get_task(task_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _ensure_read(current_user)
    service = TaskBoardService(db)
    try:
        detail = service.get_detail(task_id)
    except AppError as exc:
        raise _to_http(exc)
    return detail


@router.get("/team-candidates")
def list_team_candidates(
    current_user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    _ensure_read(current_user)
    return TaskBoardService(db).list_team_candidates()


@router.post("/tasks", status_code=201)
def create_task(
    payload: TaskCreatePayload,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_read(current_user)
    service = TaskBoardService(db)
    try:
        return service.create_task(
            actor=current_user,
            title=payload.title,
            description=payload.description,
            board_date=payload.board_date,
            status=payload.status,
            priority=payload.priority,
            assignee_id=payload.assignee_id,
            software_id=payload.software_id,
            major_version_id=payload.major_version_id,
            target_type=payload.target_type,
            target_id=payload.target_id,
            due_at=payload.due_at,
        )
    except AppError as exc:
        raise _to_http(exc)


@router.patch("/tasks/{task_id}")
def update_task(
    task_id: int,
    payload: TaskUpdatePayload,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_read(current_user)
    raw = payload.model_dump(exclude_unset=True)
    service = TaskBoardService(db)
    try:
        return service.update_task(actor=current_user, task_id=task_id, patch=raw)
    except AppError as exc:
        raise _to_http(exc)


@router.patch("/tasks/{task_id}/status")
def update_status(
    task_id: int,
    payload: TaskStatusPayload,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_read(current_user)
    service = TaskBoardService(db)
    try:
        return service.update_status(
            actor=current_user,
            task_id=task_id,
            status=payload.status,
            progress=payload.progress,
        )
    except AppError as exc:
        raise _to_http(exc)


@router.post("/tasks/{task_id}/updates", status_code=201)
def add_progress(
    task_id: int,
    payload: TaskProgressPayload,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_read(current_user)
    service = TaskBoardService(db)
    try:
        return service.add_progress(actor=current_user, task_id=task_id, content=payload.content)
    except AppError as exc:
        raise _to_http(exc)


@router.post("/tasks/{task_id}/archive")
def archive_task(
    task_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_read(current_user)
    service = TaskBoardService(db)
    try:
        return service.archive_task(actor=current_user, task_id=task_id)
    except AppError as exc:
        raise _to_http(exc)


@router.post("/tasks/{task_id}/carry-over")
def carry_over(
    task_id: int,
    payload: TaskCarryOverPayload,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_read(current_user)
    service = TaskBoardService(db)
    try:
        return service.carry_over(actor=current_user, task_id=task_id, to_date=payload.to_date)
    except AppError as exc:
        raise _to_http(exc)


def _to_http(exc: AppError):
    from fastapi import HTTPException
    return HTTPException(status_code=exc.status_code, detail=exc.message)
