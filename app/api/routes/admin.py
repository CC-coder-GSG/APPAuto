from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session, joinedload

from app.api.deps import get_current_user, get_db
from app.models import AuditLog, BugTracking, Requirement, User, Version
from app.services.permission_service import ensure_admin

router = APIRouter()


@router.get("/admin/data-overview")
def admin_data_overview(current_user=Depends(get_current_user), db: Session = Depends(get_db)):
    ensure_admin(current_user)
    reqs = db.query(Requirement).options(joinedload(Requirement.test_cases)).order_by(Requirement.id.desc()).all()
    req_map = {r.id: r for r in db.query(Requirement).options(joinedload(Requirement.major_version)).all()}
    version_map = {v.id: v for v in db.query(Version).all()}
    user_map = {u.id: u for u in db.query(User).all()}

    link_logs = []
    rows = (
        db.query(AuditLog)
        .filter(AuditLog.action == "requirement.link_major")
        .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
        .limit(300)
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

    return {
        "users": [
            {
                "id": u.id,
                "username": u.username,
                "display_name": u.shown_name,
                "role": u.role.value,
                "is_team_member": u.is_team_member,
                "created_at": u.created_at.isoformat(),
            }
            for u in db.query(User).order_by(User.id.asc()).all()
        ],
        "versions": [{"id": v.id, "version_no": v.version_no, "version_type": v.version_type.value, "parent_id": v.parent_id, "software_id": v.software_id} for v in db.query(Version).order_by(Version.created_at.desc()).all()],
        "requirements": [{"id": r.id, "zentao_req_id": r.zentao_req_id, "title": r.title, "major_version_id": r.major_version_id, "case_ids": [c.zentao_case_id for c in r.test_cases]} for r in reqs],
        "bugs": [{"id": b.id, "bug_id": b.bug_id, "major_version_id": b.major_version_id, "requirement_id": b.requirement_id, "source_type": b.source_type.value, "source_ref": b.source_ref, "found_minor_version_id": b.found_minor_version_id} for b in db.query(BugTracking).order_by(BugTracking.id.desc()).all()],
        "requirement_link_logs": link_logs,
    }
