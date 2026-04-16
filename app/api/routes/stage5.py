from __future__ import annotations

import re
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.integrations.wecom import send_markdown
from app.models import BugSourceType, User
from app.services.stage5_service import Stage5Service

router = APIRouter()

B_PATTERN = re.compile(r"^b#\d+$")


class Stage5ResultPayload(BaseModel):
    minor_version_id: int
    test_done: bool
    newly_found_bug_id: Optional[str] = None
    resolution: str = "fixed"


class Stage5IssueCreatePayload(BaseModel):
    major_version_id: int
    requirement_id: Optional[int] = None
    source_type: BugSourceType = BugSourceType.MANUAL
    source_ref: Optional[str] = None
    bug_id: str
    minor_version_id: Optional[int] = None


class Stage5ZentaoSyncPayload(BaseModel):
    major_version_id: int
    force: bool = False


@router.get("/stage5/overview")
def stage5_overview(major_version_id: int, software_id: Optional[int] = None, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    service = Stage5Service(db)
    return service.overview(major_version_id, current_user, software_id=software_id)


@router.get("/stage5/search-options")
def get_stage5_search_options(major_version_id: int, _: User = Depends(get_current_user), db: Session = Depends(get_db)):
    service = Stage5Service(db)
    return service.search_options(major_version_id)


@router.post("/stage5/sync-zentao-bugs")
def sync_stage5_zentao_bugs(payload: Stage5ZentaoSyncPayload, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    service = Stage5Service(db)
    return service.sync_zentao_major_bugs(
        major_version_id=payload.major_version_id,
        current_user=current_user,
        force=payload.force,
    )


@router.put("/stage5/bugs/{bug_track_id}/result")
async def submit_stage5_result(bug_track_id: int, payload: Stage5ResultPayload, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    service = Stage5Service(db)
    result = service.submit_result(
        bug_track_id,
        minor_version_id=payload.minor_version_id,
        test_done=payload.test_done,
        newly_found_bug_id=payload.newly_found_bug_id,
        resolution=payload.resolution,
        current_user=current_user,
    )
    if result["notice"]:
        await send_markdown(result["notice"])
    return {"message": result["message"]}


@router.post("/stage5/issues")
async def add_stage5_issue(payload: Stage5IssueCreatePayload, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not B_PATTERN.match(payload.bug_id):
        raise HTTPException(status_code=400, detail="bug_id must be like b#xxxx")
    service = Stage5Service(db)
    return service.add_issue(
        major_version_id=payload.major_version_id,
        requirement_id=payload.requirement_id,
        source_type=payload.source_type,
        source_ref=payload.source_ref,
        bug_id=payload.bug_id,
        minor_version_id=payload.minor_version_id,
        current_user=current_user,
    )
