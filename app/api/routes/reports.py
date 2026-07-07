from __future__ import annotations

from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.models import User
from app.services.report_service import ReportService

router = APIRouter()


@router.get("/reports/summary")
def reports_summary(
    start_date: date,
    end_date: date,
    user_id: Optional[int] = None,
    major_version_id: Optional[int] = None,
    software_id: Optional[int] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    service = ReportService(db)
    return service.summary(start_date, end_date, current_user, user_id, major_version_id, software_id)


@router.get("/reports/weekly-task-export")
def reports_weekly_task_export(
    week_offset: int = 0,
    _: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """本周工作内容（禅道任务，按大版本/人员分组、父子合并）。返回 JSON，
    前端用 text 字段生成 txt 下载（避免响应头中文文件名编码问题）。"""
    return ReportService(db).weekly_task_report(week_offset=week_offset)


@router.get("/reports/advanced")
def reports_advanced(start_date: date, end_date: date, major_version_id: Optional[int] = None, software_id: Optional[int] = None, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    service = ReportService(db)
    return service.advanced(start_date, end_date, major_version_id, software_id)


@router.get("/reports/version-bugs")
def reports_version_bugs(major_version_id: Optional[int] = None, _: User = Depends(get_current_user), db: Session = Depends(get_db)):
    service = ReportService(db)
    return service.version_bugs(major_version_id=major_version_id)


@router.get("/reports/governance")
def reports_governance(
    start_date: date,
    end_date: date,
    major_version_id: Optional[int] = None,
    software_id: Optional[int] = None,
    req_overdue_days: int = 14,
    feedback_overdue_days: int = 7,
    bug_overdue_days: int = 7,
    stale_bug_days: int = 14,
    _: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    service = ReportService(db)
    return service.governance(
        start_date=start_date,
        end_date=end_date,
        major_version_id=major_version_id,
        software_id=software_id,
        req_overdue_days=req_overdue_days,
        feedback_overdue_days=feedback_overdue_days,
        bug_overdue_days=bug_overdue_days,
        stale_bug_days=stale_bug_days,
    )


@router.get("/reports/zentao-sync-stats")
def reports_zentao_sync_stats(
    major_version_id: Optional[int] = None,
    software_id: Optional[int] = None,
    stale_minutes: int = 60,
    _: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return Zentao sync health statistics for governance view."""
    service = ReportService(db)
    return service.zentao_sync_stats(
        major_version_id=major_version_id,
        software_id=software_id,
        stale_minutes=stale_minutes,
    )
