from __future__ import annotations

import re
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session, joinedload

from app.api.deps import get_current_user, get_db
from app.models import BugSourceType, BugTracking, Requirement, User, Version, VersionType
from app.integrations.wecom import send_markdown
from app.services.permission_service import ensure_admin
from app.services.requirement_service import RequirementService

router = APIRouter()

U_PATTERN = re.compile(r"^u#\d+$")


class RequirementCreatePayload(BaseModel):
    zentao_req_id: str
    title: str
    major_version_id: int = Field(gt=0)


class ReqBatchItemPayload(BaseModel):
    zentao_req_id: str
    title: str


class RequirementBatchCreatePayload(BaseModel):
    major_version_id: int
    items: list[ReqBatchItemPayload]


class AddCasePayload(BaseModel):
    zentao_case_id: str


class AssignItemPayload(BaseModel):
    requirement_id: int
    owner_id: Optional[int] = None


class AssignPublishPayload(BaseModel):
    major_version_id: int
    assignments: list[AssignItemPayload]


class ReqStatusUpdatePayload(BaseModel):
    case_completed: Optional[bool] = None
    test_completed: Optional[bool] = None


class CaseUpdatePayload(BaseModel):
    case_ids: list[str] = Field(default_factory=list)
    case_completed: bool


def _parse_multiple_ids(raw_ids: list[str], pattern: re.Pattern[str], label: str) -> list[str]:
    clean = []
    for item in raw_ids:
        i = item.strip()
        if not i:
            continue
        if not pattern.match(i):
            raise HTTPException(status_code=400, detail=f"Invalid {label} format: {i}")
        clean.append(i)
    return list(dict.fromkeys(clean))


@router.post("/requirements", status_code=201)
def create_requirement(payload: RequirementCreatePayload, _: object = Depends(get_current_user), db: Session = Depends(get_db)):
    service = RequirementService(db)
    return service.create_requirement(payload.zentao_req_id, payload.title, payload.major_version_id)


@router.post("/requirements/batch", status_code=201)
def batch_create_requirements(payload: RequirementBatchCreatePayload, current_user=Depends(get_current_user), db: Session = Depends(get_db)):
    ensure_admin(current_user)
    service = RequirementService(db)
    items = [{"zentao_req_id": item.zentao_req_id, "title": item.title} for item in payload.items]
    return service.batch_create_requirements(payload.major_version_id, items)


@router.get("/requirements")
def list_requirements(major_version_id: int = Query(...), db: Session = Depends(get_db), _: object = Depends(get_current_user)):
    service = RequirementService(db)
    return service.list_requirements(major_version_id)


@router.get("/requirements/my-workbench")
def my_workbench(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    major_version_id: Optional[int] = None,
    mode: str = "version",
    software_id: Optional[int] = None,
):
    query = db.query(Requirement).options(
        joinedload(Requirement.major_version),
        joinedload(Requirement.test_cases),
    ).filter(Requirement.owner_id == current_user.id)

    if mode == "version" and major_version_id:
        query = query.filter(Requirement.major_version_id == major_version_id)
    elif mode == "all_pending":
        query = query.filter(Requirement.test_completed.is_(False))
    if software_id:
        query = query.join(Version, Requirement.major_version_id == Version.id).filter(Version.software_id == software_id)

    reqs = query.order_by(Requirement.id.desc()).all()

    req_ids = [r.id for r in reqs]
    case_ids = [c.id for r in reqs for c in r.test_cases]
    minors = {v.id: v.version_no for v in db.query(Version).filter(Version.version_type == VersionType.MINOR).all()}

    case_bug_rows = (
        db.query(BugTracking)
        .options(joinedload(BugTracking.dispatched_to))
        .filter(
            BugTracking.requirement_id.in_(req_ids),
            BugTracking.source_type == BugSourceType.CASE,
            BugTracking.source_ref.in_([str(x) for x in case_ids] if case_ids else ["-1"]),
        )
        .all()
        if req_ids
        else []
    )

    free_bug_rows = (
        db.query(BugTracking)
        .options(joinedload(BugTracking.dispatched_to))
        .filter(BugTracking.requirement_id.in_(req_ids), BugTracking.source_type == BugSourceType.MANUAL)
        .all()
        if req_ids
        else []
    )

    case_bug_map: dict[str, list[dict]] = {}
    for b in case_bug_rows:
        case_bug_map.setdefault(b.source_ref or "", []).append(
            {
                "id": b.id,
                "bug_id": b.bug_id,
                "found_minor_version_no": minors.get(b.found_minor_version_id, "未知") if b.found_minor_version_id else "未知",
                "fixed_minor_version_no": minors.get(b.fixed_minor_version_id, "未知") if b.fixed_minor_version_id else None,
                "dispatched_to_name": b.dispatched_to.shown_name if b.dispatched_to else None,
            }
        )

    free_bug_map: dict[int, list[dict]] = {}
    for b in free_bug_rows:
        free_bug_map.setdefault(b.requirement_id or -1, []).append(
            {
                "id": b.id,
                "bug_id": b.bug_id,
                "found_minor_version_no": minors.get(b.found_minor_version_id, "未知") if b.found_minor_version_id else "未知",
                "fixed_minor_version_no": minors.get(b.fixed_minor_version_id, "未知") if b.fixed_minor_version_id else None,
                "dispatched_to_name": b.dispatched_to.shown_name if b.dispatched_to else None,
            }
        )

    return [
        {
            "id": r.id,
            "zentao_req_id": r.zentao_req_id,
            "title": r.title,
            "case_completed": r.case_completed,
            "test_completed": r.test_completed,
            "major_version_id": r.major_version_id,
            "major_version_name": r.major_version.version_no if r.major_version else "",
            "test_cases": [{"id": c.id, "zentao_case_id": c.zentao_case_id, "bugs": case_bug_map.get(str(c.id), [])} for c in r.test_cases],
            "free_bugs": free_bug_map.get(r.id, []),
        }
        for r in reqs
    ]


@router.post("/requirements/{req_id}/cases")
def add_case_to_requirement(req_id: int, payload: AddCasePayload, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not U_PATTERN.match(payload.zentao_case_id):
        raise HTTPException(status_code=400, detail="zentao_case_id must be like u#xxxx")
    service = RequirementService(db)
    return service.add_case_to_requirement(req_id, payload.zentao_case_id, actor_id=current_user.id)


@router.put("/requirements/{requirement_id}")
def update_requirement(requirement_id: int, payload: RequirementCreatePayload, current_user=Depends(get_current_user), db: Session = Depends(get_db)):
    ensure_admin(current_user)
    service = RequirementService(db)
    return service.update_requirement(requirement_id, payload.zentao_req_id, payload.title, payload.major_version_id, actor_id=current_user.id)


@router.delete("/requirements/{requirement_id}")
def delete_requirement(requirement_id: int, current_user=Depends(get_current_user), db: Session = Depends(get_db)):
    ensure_admin(current_user)
    service = RequirementService(db)
    return service.delete_requirement(requirement_id, actor_id=current_user.id)


@router.put("/test-cases/{case_id}")
def update_case(case_id: int, payload: AddCasePayload, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not U_PATTERN.match(payload.zentao_case_id):
        raise HTTPException(status_code=400, detail="zentao_case_id must be like u#xxxx")
    service = RequirementService(db)
    return service.update_case_identifier(case_id, payload.zentao_case_id, actor_id=current_user.id)


@router.delete("/test-cases/{case_id}")
def delete_case(case_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    service = RequirementService(db)
    return service.delete_case(case_id, actor_id=current_user.id)


@router.post("/requirements/assign-and-publish")
async def assign_and_publish(payload: AssignPublishPayload, current_user=Depends(get_current_user), db: Session = Depends(get_db)):
    ensure_admin(current_user)
    service = RequirementService(db)
    users_map = {u.id: u.shown_name for u in db.query(User).all()}
    result = service.assign_and_publish(
        payload.major_version_id,
        [{"requirement_id": item.requirement_id, "owner_id": item.owner_id} for item in payload.assignments],
        users_map,
        actor_id=current_user.id,
    )
    if result.get("change_msgs"):
        md = "### 🔄 需求负责人变更通知\n" + "\n".join(result["change_msgs"]) + "\n\n*提示：移交的需求已自动重置【完成状态】，请新负责人重新校验并打勾。*"
        await send_markdown(md)
    else:
        await send_markdown("✅ 需求分配状态已整体更新发布")
    return {"message": result.get("message", "Assignments updated")}


@router.patch("/requirements/{requirement_id}/status")
def patch_requirement_status(
    requirement_id: int,
    payload: ReqStatusUpdatePayload,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    service = RequirementService(db)
    return service.patch_requirement_status(
        requirement_id,
        current_user,
        case_completed=payload.case_completed,
        test_completed=payload.test_completed,
    )


@router.put("/requirements/{requirement_id}/cases")
def update_requirement_cases(requirement_id: int, payload: CaseUpdatePayload, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    service = RequirementService(db)
    case_ids = _parse_multiple_ids(payload.case_ids, U_PATTERN, "test case")
    return service.update_requirement_cases(requirement_id, case_ids, payload.case_completed, actor_id=current_user.id)
