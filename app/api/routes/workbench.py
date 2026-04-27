from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.models import User
from app.services.permission_service import ensure_admin
from app.services.workbench_refresh_service import WorkbenchRefreshService
from app.services.workbench_service import WorkbenchService
from app.services.zentao_background_sync_service import ZentaoBackgroundSyncService

router = APIRouter(prefix="/workbench", tags=["workbench"])


class WorkbenchPreflightPayload(BaseModel):
    software_id: int
    include_bugs: bool = True
    include_testcases: bool = True
    force: bool = False


@router.get("/mine")
def my_workbench_v2(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    major_version_id: Optional[int] = None,
    mode: str = "version",
    software_id: Optional[int] = None,
):
    return WorkbenchService(db).get_my_workbench(
        current_user=current_user,
        major_version_id=major_version_id,
        mode=mode,
        software_id=software_id,
    )


@router.get("/retest")
def retest_workbench_v2(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    major_version_id: Optional[int] = None,
    mode: str = "version",
    software_id: Optional[int] = None,
):
    return WorkbenchService(db).get_retest_workbench(
        current_user=current_user,
        major_version_id=major_version_id,
        mode=mode,
        software_id=software_id,
    )


@router.post("/preflight-refresh")
def preflight_refresh(
    payload: WorkbenchPreflightPayload,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return WorkbenchRefreshService(db).preflight_refresh(
        software_id=payload.software_id,
        current_user=current_user,
        include_bugs=payload.include_bugs,
        include_testcases=payload.include_testcases,
        force=payload.force,
    )


@router.post("/admin/run-recent-sync")
def run_recent_sync(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    ensure_admin(current_user)
    return ZentaoBackgroundSyncService(db).run_recent_sync_for_all_software()


@router.post("/admin/run-nightly-full-sync")
def run_nightly_full_sync(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    ensure_admin(current_user)
    return ZentaoBackgroundSyncService(db).run_nightly_full_sync_for_all_software()
