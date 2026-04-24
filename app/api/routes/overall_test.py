"""
Overall-Test API routes (canonical).

Replaces the legacy `/stage5/*` endpoints. The old routes still exist as
thin compatibility wrappers — see `app/api/routes/stage5.py` — so any
caller pinned to the previous URLs keeps working. New frontend code
should use `/overall-test/*`.
"""
from __future__ import annotations

import re
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.integrations.wecom import send_markdown
from app.models import BugSourceType, User
from app.services.overall_test_service import OverallTestService

router = APIRouter()

B_PATTERN = re.compile(r"^b#\d+$")


class OverallTestResultPayload(BaseModel):
    minor_version_id: int
    test_done: bool
    newly_found_bug_id: Optional[str] = None
    resolution: str = "fixed"


class OverallTestIssueCreatePayload(BaseModel):
    major_version_id: int
    requirement_id: Optional[int] = None
    source_type: BugSourceType = BugSourceType.MANUAL
    source_ref: Optional[str] = None
    bug_id: str
    minor_version_id: Optional[int] = None


class OverallTestZentaoSyncPayload(BaseModel):
    major_version_id: int
    force: bool = False


class OverallTestZentaoSyncAllPayload(BaseModel):
    software_id: int
    force: bool = False


@router.get("/overall-test/overview")
def overall_test_overview(
    major_version_id: int,
    software_id: Optional[int] = None,
    statuses: Optional[str] = Query(
        None,
        description="Comma-separated subset of {active,closed,resolved,local}",
    ),
    keyword: Optional[str] = Query(
        None,
        description="Fuzzy match against bug_id / zentao_bug_id (b# prefix stripped)",
    ),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    service = OverallTestService(db)
    return service.overview(
        major_version_id,
        current_user,
        software_id=software_id,
        statuses=statuses,
        keyword=keyword,
    )


@router.get("/overall-test/search-options")
def get_overall_test_search_options(
    major_version_id: int,
    _: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    service = OverallTestService(db)
    return service.search_options(major_version_id)


@router.post("/overall-test/sync-zentao-bugs")
def sync_overall_test_zentao_bugs(
    payload: OverallTestZentaoSyncPayload,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    service = OverallTestService(db)
    return service.sync_zentao_major_bugs(
        major_version_id=payload.major_version_id,
        current_user=current_user,
        force=payload.force,
    )


@router.post("/overall-test/sync-zentao-all")
def sync_overall_test_zentao_all(
    payload: OverallTestZentaoSyncAllPayload,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Pull ALL bugs (including closed/deleted) for a whole software product.

    Uses /products/{id}/bugs?status=all under the hood (the previous
    execution+build walk only returned active/resolved bugs, so the local DB
    was missing thousands of closed entries).
    """
    service = OverallTestService(db)
    return service.sync_all_zentao_bugs_by_software(
        software_id=payload.software_id,
        current_user=current_user,
        force=payload.force,
    )


@router.put("/overall-test/bugs/{bug_track_id}/result")
async def submit_overall_test_result(
    bug_track_id: int,
    payload: OverallTestResultPayload,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    service = OverallTestService(db)
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


@router.post("/overall-test/issues")
async def add_overall_test_issue(
    payload: OverallTestIssueCreatePayload,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not B_PATTERN.match(payload.bug_id):
        raise HTTPException(status_code=400, detail="bug_id must be like b#xxxx")
    service = OverallTestService(db)
    return service.add_issue(
        major_version_id=payload.major_version_id,
        requirement_id=payload.requirement_id,
        source_type=payload.source_type,
        source_ref=payload.source_ref,
        bug_id=payload.bug_id,
        minor_version_id=payload.minor_version_id,
        current_user=current_user,
    )
