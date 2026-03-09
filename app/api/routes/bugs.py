from __future__ import annotations

import re
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.models import BugSourceType, User
from app.services.bug_service import BugService
from app.services.permission_service import ensure_admin
from app.services.push_service import PushService

router = APIRouter()

B_PATTERN = re.compile(r"^b#\d+$")


class ExecutionBugPayload(BaseModel):
    bug_id: str
    minor_version_id: int
    requirement_id: int
    source_type: BugSourceType
    source_ref: Optional[str] = None


class DispatchPayload(BaseModel):
    user_id: int


class BugRetestFailPayload(BaseModel):
    is_retest_failed: bool


@router.post("/bugs/execution")
def create_execution_bug(payload: ExecutionBugPayload, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not B_PATTERN.match(payload.bug_id):
        raise HTTPException(status_code=400, detail="bug_id must be like b#xxxx")
    if payload.source_type not in [BugSourceType.CASE, BugSourceType.MANUAL, BugSourceType.RETEST]:
        raise HTTPException(status_code=400, detail="source_type only supports case/manual/retest")
    service = BugService(db)
    return service.create_execution_bug(
        bug_id=payload.bug_id,
        minor_version_id=payload.minor_version_id,
        requirement_id=payload.requirement_id,
        source_type=payload.source_type,
        source_ref=payload.source_ref,
        actor=current_user,
    )


@router.put("/bugs/{bug_id}")
def update_bug(bug_id: int, new_bug_id: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not B_PATTERN.match(new_bug_id):
        raise HTTPException(status_code=400, detail="Invalid bug format")
    service = BugService(db)
    return service.update_bug(bug_id, new_bug_id, actor_id=current_user.id)


@router.delete("/bugs/{bug_id}")
def delete_bug(bug_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    service = BugService(db)
    return service.delete_bug(bug_id, actor_id=current_user.id)


@router.patch("/bugs/{bug_id}/retest-fail")
def toggle_bug_retest_fail(bug_id: int, payload: BugRetestFailPayload, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    service = BugService(db)
    return service.toggle_retest_fail(bug_id, payload.is_retest_failed, actor_id=current_user.id)


@router.get("/bugs/search")
def search_bug(bug_id: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    ensure_admin(current_user)
    service = BugService(db)
    return service.search_bug(bug_id)


@router.post("/bugs/{bug_id}/dispatch")
async def dispatch_bug(bug_id: int, payload: DispatchPayload, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    ensure_admin(current_user)
    service = BugService(db)
    push_service = PushService(db)
    result, user, bug = service.dispatch_bug(bug_id, payload.user_id, actor_id=current_user.id)
    if user:
        await push_service.push_bug_dispatch_notice(bug.bug_id, user.username)
    return result


@router.get("/bugs/dispatched-to-me")
def dispatched_to_me(major_version_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    service = BugService(db)
    return service.dispatched_to_me(major_version_id, current_user)


@router.get("/bugs/dispatched-all")
def get_all_dispatched_bugs(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    ensure_admin(current_user)
    service = BugService(db)
    return service.dispatched_all()
