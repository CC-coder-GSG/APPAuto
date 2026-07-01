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


class TaskOperatePayload(BaseModel):
    action: str  # start | finish | close | reactivate | set_time
    hours: Optional[float] = None
    consumed: Optional[float] = None
    comment: Optional[str] = None


@router.get("/tasks")
def my_task_workbench(
    refresh: bool = False,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """任务工作台：当前账号名下的禅道任务（含是否关联本平台需求的标注）。

    refresh=true 时先全量刷新任务镜像（较慢）；否则读缓存（后台任务会周期刷新）。
    """
    from app.services.zentao_task_mirror_service import ZentaoTaskMirrorService

    svc = ZentaoTaskMirrorService(db)
    refreshed = None
    if refresh:
        try:
            refreshed = svc.sync_all()
        except Exception as exc:  # noqa: BLE001 — 刷新失败不影响读缓存
            refreshed = {"ok": False, "error": str(exc)}
    return {"tasks": svc.list_mine_with_links(current_user), "refreshed": refreshed}


@router.post("/tasks/{task_id}/operate")
def operate_my_task(
    task_id: int,
    payload: TaskOperatePayload,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """对未关联需求的禅道任务执行操作（开始/完成/关闭/重新激活/设置工时）。"""
    from app.services.zentao_task_mirror_service import ZentaoTaskMirrorService

    return ZentaoTaskMirrorService(db).operate_task(
        task_id=task_id,
        action=payload.action,
        current_user=current_user,
        hours=payload.hours,
        consumed=payload.consumed,
        comment=payload.comment,
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
