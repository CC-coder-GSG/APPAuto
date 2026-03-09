from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session, joinedload

from app.api.deps import get_current_user, get_db
from app.models import BugTracking, Requirement, User, Version
from app.services.permission_service import ensure_admin

router = APIRouter()


@router.get("/admin/data-overview")
def admin_data_overview(current_user=Depends(get_current_user), db: Session = Depends(get_db)):
    ensure_admin(current_user)
    reqs = db.query(Requirement).options(joinedload(Requirement.test_cases)).order_by(Requirement.id.desc()).all()

    return {
        "users": [
            {
                "id": u.id,
                "username": u.username,
                "role": u.role.value,
                "is_team_member": u.is_team_member,
                "created_at": u.created_at.isoformat(),
            }
            for u in db.query(User).order_by(User.id.asc()).all()
        ],
        "versions": [{"id": v.id, "version_no": v.version_no, "version_type": v.version_type.value, "parent_id": v.parent_id} for v in db.query(Version).order_by(Version.created_at.desc()).all()],
        "requirements": [{"id": r.id, "zentao_req_id": r.zentao_req_id, "title": r.title, "major_version_id": r.major_version_id, "case_ids": [c.zentao_case_id for c in r.test_cases]} for r in reqs],
        "bugs": [{"id": b.id, "bug_id": b.bug_id, "major_version_id": b.major_version_id, "requirement_id": b.requirement_id, "source_type": b.source_type.value, "source_ref": b.source_ref, "found_minor_version_id": b.found_minor_version_id} for b in db.query(BugTracking).order_by(BugTracking.id.desc()).all()],
    }
