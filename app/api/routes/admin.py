from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session, joinedload

from app.api.deps import get_current_user, get_db
from app.models import AuditLog, BugTracking, Requirement, User, Version
from app.schemas.admin import JenkinsBuildReportPayload
from app.services.activity_service import ActivityService
from app.services.build_record_service import BuildRecordService
from app.services.permission_service import ensure_admin, ensure_tab_access, get_allowed_tabs
from app.services.push_service import PushService

router = APIRouter()
logger = logging.getLogger(__name__)


class PushActivitySummaryPayload(BaseModel):
    hours: int = Field(default=24, ge=1, le=168)
    target_types: list[str] = Field(default_factory=list)
    only_important: bool = True
    software_id: Optional[int] = None


def _summarize_change_log(change_log: str | None, limit: int = 200) -> tuple[str, int]:
    text = (change_log or "").strip()
    if not text:
        return "", 0
    if len(text) <= limit:
        return text, len(text)
    return f"{text[:limit]}...", len(text)


def _resolve_build_report_token() -> str:
    return (
        os.getenv("BUILD_REPORT_TOKEN")
        or os.getenv("JENKINS_REPORT_TOKEN")
        or "abc123456"
    )


def _build_requirement_link_logs(db: Session, limit: int = 300) -> list[dict]:
    req_map = {r.id: r for r in db.query(Requirement).options(joinedload(Requirement.major_version)).all()}
    version_map = {v.id: v for v in db.query(Version).all()}
    user_map = {u.id: u for u in db.query(User).all()}

    link_logs: list[dict] = []
    rows = (
        db.query(AuditLog)
        .filter(AuditLog.action == "requirement.link_major")
        .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
        .limit(limit)
        .all()
    )
    for log in rows:
        detail = log.detail or ""
        from_major_id = None
        from_req_id = None
        for p in detail.split(","):
            p = p.strip()
            if p.startswith("from_major="):
                try:
                    from_major_id = int(p.split("=", 1)[1])
                except Exception:
                    from_major_id = None
            elif p.startswith("from_req="):
                try:
                    from_req_id = int(p.split("=", 1)[1])
                except Exception:
                    from_req_id = None

        target_req = None
        try:
            target_req = req_map.get(int(log.target_id or 0))
        except Exception:
            target_req = None
        source_req = req_map.get(from_req_id or -1)
        from_major = version_map.get(from_major_id or -1)
        to_major = target_req.major_version if target_req else None
        actor = user_map.get(log.actor_id or -1)

        link_logs.append(
            {
                "id": log.id,
                "created_at": log.created_at.isoformat() if log.created_at else None,
                "actor_name": actor.shown_name if actor else "未知",
                "source_major_id": from_major_id,
                "source_major_name": from_major.version_no if from_major else "未知",
                "target_major_id": target_req.major_version_id if target_req else None,
                "target_major_name": to_major.version_no if to_major else "未知",
                "source_requirement_id": from_req_id,
                "source_zentao_req_id": source_req.zentao_req_id if source_req else None,
                "source_title": source_req.title if source_req else None,
                "target_requirement_id": target_req.id if target_req else None,
                "target_zentao_req_id": target_req.zentao_req_id if target_req else None,
                "target_title": target_req.title if target_req else None,
            }
        )

    return link_logs


@router.get("/admin/data-overview")
def admin_data_overview(current_user=Depends(get_current_user), db: Session = Depends(get_db)):
    ensure_tab_access(current_user, "data")
    reqs = (
        db.query(Requirement)
        .options(joinedload(Requirement.test_cases), joinedload(Requirement.test_notes_updated_by))
        .order_by(Requirement.id.desc())
        .all()
    )

    return {
        "users": [
            {
                "id": u.id,
                "username": u.username,
                "display_name": u.shown_name,
                "role": u.role.value,
                "is_team_member": u.is_team_member,
                "allowed_tabs": get_allowed_tabs(u),
                "created_at": u.created_at.isoformat(),
            }
            for u in db.query(User).order_by(User.id.asc()).all()
        ],
        "versions": [
            {
                "id": v.id,
                "version_no": v.version_no,
                "version_type": v.version_type.value,
                "parent_id": v.parent_id,
                "software_id": v.software_id,
            }
            for v in db.query(Version).order_by(Version.created_at.desc()).all()
        ],
        "requirements": [
            {
                "id": r.id,
                "zentao_req_id": r.zentao_req_id,
                "title": r.title,
                "major_version_id": r.major_version_id,
                "case_ids": [c.zentao_case_id for c in r.test_cases],
                "test_cases": [{"id": c.id, "zentao_case_id": c.zentao_case_id, "zentao_case_url": c.zentao_case_url} for c in r.test_cases],
                "test_notes": r.test_notes,
                "test_notes_updated_at": r.test_notes_updated_at.isoformat() if r.test_notes_updated_at else None,
                "test_notes_updated_by_name": r.test_notes_updated_by.shown_name if r.test_notes_updated_by else None,
            }
            for r in reqs
        ],
        "bugs": [
            {
                "id": b.id,
                "bug_id": b.bug_id,
                "major_version_id": b.major_version_id,
                "requirement_id": b.requirement_id,
                "source_type": b.source_type.value,
                "source_ref": b.source_ref,
                "found_minor_version_id": b.found_minor_version_id,
                "zentao_bug_url": b.zentao_bug_url,
                "zentao_bug_title": b.zentao_bug_title,
                "zentao_bug_id": b.zentao_bug_id,
                "zentao_live_status": b.zentao_live_status or "",
                "zentao_deleted": bool(b.zentao_deleted),
                "zentao_closed_by_name": b.zentao_closed_by_name or "",
                "zentao_assigned_to_name": b.zentao_assigned_to_name or "",
                "last_zentao_synced_at": b.last_zentao_synced_at.strftime("%Y-%m-%d %H:%M") if b.last_zentao_synced_at else "",
                "zentao_sync_status": b.zentao_sync_status or "",
            }
            for b in db.query(BugTracking).order_by(BugTracking.id.desc()).all()
        ],
    }


@router.get("/admin/requirement-link-logs")
def admin_requirement_link_logs(current_user=Depends(get_current_user), db: Session = Depends(get_db)):
    ensure_tab_access(current_user, "data")
    return _build_requirement_link_logs(db, limit=300)


@router.get("/admin/activity-feed")
def admin_activity_feed(
    target_type: Optional[str] = None,
    action: Optional[str] = None,
    actor_id: Optional[int] = None,
    keyword: Optional[str] = None,
    software_id: Optional[int] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    only_important: bool = False,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    ensure_tab_access(current_user, "activity")
    return ActivityService(db).list_feed(
        target_type=target_type,
        action=action,
        actor_id=actor_id,
        keyword=keyword,
        software_id=software_id,
        date_from=date_from,
        date_to=date_to,
        only_important=only_important,
        limit=limit,
        offset=offset,
    )


@router.get("/admin/activity-summary")
def admin_activity_summary(
    days: int = Query(default=1, ge=1, le=30),
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    software_id: Optional[int] = None,
    only_important: bool = False,
    target_types: list[str] = Query(default=[]),
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    ensure_tab_access(current_user, "activity")
    end_at = date_to or datetime.utcnow()
    start_at = date_from or (end_at - timedelta(days=days))
    return ActivityService(db).get_summary(
        date_from=start_at,
        date_to=end_at,
        software_id=software_id,
        target_types=target_types or None,
        only_important=only_important,
    )


@router.post("/admin/push-activity-summary")
async def admin_push_activity_summary(
    payload: PushActivitySummaryPayload,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    ensure_admin(current_user)
    markdown = ActivityService(db).build_push_markdown(
        hours=payload.hours,
        software_id=payload.software_id,
        target_types=payload.target_types or None,
        only_important=payload.only_important,
    )
    await PushService(db).send_markdown(markdown)
    return {"message": "最近动态摘要已推送", "hours": payload.hours}


# Jenkins build-report receiver.
# Current version validates payload, performs token check, upserts build records and returns a debug-friendly response.
@router.post("/admin/build-report")
@router.post("/api/admin/build-report")
def admin_build_report(
    payload: JenkinsBuildReportPayload,
    x_build_token: str | None = Header(default=None, alias="X-Build-Token"),
    db: Session = Depends(get_db),
):
    expected_token = _resolve_build_report_token()
    if not x_build_token or x_build_token.strip() != expected_token:
        return JSONResponse(
            status_code=401,
            content={
                "success": False,
                "message": "invalid build token",
            },
        )

    change_log_preview, change_log_length = _summarize_change_log(payload.change_log)

    try:
        record, action = BuildRecordService(db).upsert_report(
            job_name=payload.job_name,
            build_number=payload.build_number,
            build_status=payload.build_status,
            version_name=payload.version_name,
            branch=payload.branch,
            build_url=payload.build_url,
            change_log=payload.change_log,
        )
        logger.info(
            "Received Jenkins build report | job=%s build=%s status=%s version=%s action=%s branch=%s url=%s change_log_len=%s change_log_preview=%s",
            payload.job_name,
            payload.build_number,
            payload.build_status,
            payload.version_name or "-",
            action,
            payload.branch or "-",
            payload.build_url or "-",
            change_log_length,
            change_log_preview or "-",
        )
        return {
            "success": True,
            "message": "build report received",
            "data": {
                "record_id": record.id,
                "action": action,
                "job_name": payload.job_name,
                "build_number": str(payload.build_number),
                "build_status": payload.build_status,
                "version_name": payload.version_name,
                "branch": payload.branch,
                "build_url": payload.build_url,
            },
        }
    except Exception:
        logger.exception(
            "Failed to process Jenkins build report | job=%s build=%s status=%s",
            payload.job_name,
            payload.build_number,
            payload.build_status,
        )
        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "message": "build report process failed",
            },
        )


@router.get("/admin/build-records")
@router.get("/api/admin/build-records")
def admin_build_records(
    limit: int = Query(default=20, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    job_name: Optional[str] = None,
    build_status: Optional[str] = None,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    ensure_tab_access(current_user, "build-records")
    return BuildRecordService(db).list_records(
        limit=limit,
        offset=offset,
        job_name=job_name,
        build_status=build_status,
    )


@router.get("/admin/build-records/major-log")
@router.get("/api/admin/build-records/major-log")
def admin_build_records_major_log(
    job_name: str = Query(...),
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    ensure_tab_access(current_user, "build-records")
    job_name_text = (job_name or "").strip()
    if not job_name_text:
        raise HTTPException(status_code=400, detail="job_name 不能为空")

    data = BuildRecordService(db).get_major_log(job_name_text)
    logger.info(
        "Generated build major log | job=%s record_count=%s",
        data["job_name"],
        data["record_count"],
    )
    return {
        "success": True,
        "message": "major log generated" if data["record_count"] > 0 else "no build records found",
        "data": data,
    }


@router.get("/admin/build-records/{record_id}")
@router.get("/api/admin/build-records/{record_id}")
def admin_build_record_detail(
    record_id: int,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    ensure_tab_access(current_user, "build-records")
    row = BuildRecordService(db).get_record(record_id)
    if not row:
        raise HTTPException(status_code=404, detail="构建记录不存在")
    return row
