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
    # 勾「用例完成」触发：用例走轻量增量同步（只拉最近编辑页，绕过 5 分钟 TTL，
    # 新建用例秒级可见；服务端另有 60s 最小间隔保护）
    testcases_recent: bool = False


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
    action: str  # start | pause | finish | close | cancel | reactivate | set_time | assign
    hours: Optional[float] = None
    consumed: Optional[float] = None
    comment: Optional[str] = None
    assigned_to: Optional[str] = None  # assign 动作的目标禅道账号


@router.get("/tasks")
def my_task_workbench(
    refresh: bool = False,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """任务工作台：当前账号名下的禅道任务（含是否关联本平台需求的标注）。

    refresh=true 时仅刷新当前用户名下任务所在的执行（快）；否则读缓存（后台任务周期全量刷新）。
    """
    from app.services.zentao_task_mirror_service import ZentaoTaskMirrorService

    svc = ZentaoTaskMirrorService(db)
    refreshed = None
    if refresh:
        try:
            refreshed = svc.sync_mine(current_user)
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
    """对禅道任务执行操作（开始/暂停/完成/关闭/取消/重新激活/设置工时/指派）。"""
    from app.services.zentao_task_mirror_service import ZentaoTaskMirrorService

    return ZentaoTaskMirrorService(db).operate_task(
        task_id=task_id,
        action=payload.action,
        current_user=current_user,
        hours=payload.hours,
        consumed=payload.consumed,
        comment=payload.comment,
        assigned_to=payload.assigned_to,
    )


@router.get("/tasks/{task_id}/assignable")
def task_assignable_users(
    task_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """任务所在执行的可指派人列表（指派弹窗数据源，面向所有用户开放）。"""
    from app.services.zentao_task_mirror_service import ZentaoTaskMirrorService

    return ZentaoTaskMirrorService(db).assignable_users(task_id)


@router.get("/review")
def review_workbench(
    major_version_id: Optional[int] = None,
    software_id: Optional[int] = None,
    _: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """审查工作台：所选大版本全部需求 + 用例（含按人独立的审查标签）。"""
    return WorkbenchService(db).get_review_workbench(
        major_version_id=major_version_id,
        software_id=software_id,
    )


class CaseReviewPayload(BaseModel):
    zentao_case_id: str
    requirement_id: Optional[int] = None
    status: str  # passed / failed
    opinion: Optional[str] = None


class CaseReviewFixPayload(BaseModel):
    zentao_case_id: str
    requirement_id: Optional[int] = None
    content: str


@router.post("/case-reviews")
async def submit_case_review(
    payload: CaseReviewPayload,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """提交用例审查（通过/不通过）。不通过时企微播报提醒需求负责人。"""
    from app.services.case_review_service import CaseReviewService
    from app.services.push_service import PushService

    svc = CaseReviewService(db)
    result = svc.submit_review(
        zentao_case_id=payload.zentao_case_id,
        requirement_id=payload.requirement_id,
        status=payload.status,
        opinion=payload.opinion,
        current_user=current_user,
    )
    if result.get("status") == "failed":
        ctx = svc.wecom_context(
            zentao_case_id=payload.zentao_case_id,
            requirement_id=payload.requirement_id,
            current_user=current_user,
        )
        ctx["opinion"] = (payload.opinion or "").strip()
        await PushService(db).push_case_review_failed(ctx)
    return result


@router.post("/case-reviews/fix")
def submit_case_review_fix(
    payload: CaseReviewFixPayload,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """修改完成：归档该用例全部 active 审查（回到未审查态），保留历史可查。"""
    from app.services.case_review_service import CaseReviewService

    return CaseReviewService(db).submit_fix(
        zentao_case_id=payload.zentao_case_id,
        requirement_id=payload.requirement_id,
        content=payload.content,
        current_user=current_user,
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
        testcases_recent=payload.testcases_recent,
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
