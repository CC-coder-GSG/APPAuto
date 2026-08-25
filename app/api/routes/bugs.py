from __future__ import annotations

import re
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.models import BugSourceType, User
from app.services.activity_service import ActivityService
from app.services.bug_service import BugService
from app.services.permission_service import ensure_admin, ensure_tab_access
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


class BugRetestActivatedPayload(BaseModel):
    requirement_id: int


class BugRetestDismissPayload(BaseModel):
    requirement_id: int
    dismissed: bool


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


@router.patch("/bugs/{bug_id}/retest-activated")
def mark_bug_retest_activated(bug_id: int, payload: BugRetestActivatedPayload, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """复测激活留痕（禅道真实激活由 /zentao/bugs/{id}/active 先行完成）。"""
    from app.services.retest_service import RetestService

    return RetestService(db).mark_bug_activated(bug_id, payload.requirement_id, current_user)


@router.patch("/bugs/{bug_id}/retest-dismiss")
def toggle_bug_retest_dismiss(bug_id: int, payload: BugRetestDismissPayload, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """勾选/取消「取消复测结论」（复测问题误报标记）。"""
    from app.services.retest_service import RetestService

    return RetestService(db).toggle_bug_dismissed(bug_id, payload.requirement_id, payload.dismissed, current_user)


@router.get("/bugs/search")
def search_bug(bug_id: str, software_id: Optional[int] = None, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    ensure_tab_access(current_user, "dispatch")
    service = BugService(db)
    return service.search_bug(bug_id, software_id=software_id)


@router.post("/bugs/{bug_id}/dispatch")
async def dispatch_bug(bug_id: int, payload: DispatchPayload, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    ensure_tab_access(current_user, "dispatch")
    service = BugService(db)
    push_service = PushService(db)
    result, user, bug = service.dispatch_bug(bug_id, payload.user_id, actor_id=current_user.id)
    if user:
        await push_service.push_bug_dispatch_notice(bug.bug_id, user.shown_name)
    return result


@router.get("/bugs/dispatched-to-me")
def dispatched_to_me(major_version_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    service = BugService(db)
    return service.dispatched_to_me(major_version_id, current_user)


@router.get("/bugs/dispatched-all")
def get_all_dispatched_bugs(software_id: Optional[int] = None, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    ensure_tab_access(current_user, "dispatch")
    service = BugService(db)
    return service.dispatched_all(software_id=software_id)


@router.get("/bugs/{bug_id}/timeline")
def bug_timeline(bug_id: int, _: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return ActivityService(db).bug_timeline(bug_id)
