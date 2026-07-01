from __future__ import annotations

import logging
import re
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from app.api.deps import get_current_user, get_db
from app.models import BugSourceType, BugTracking, Requirement, User, Version, VersionType
from app.integrations.wecom import send_markdown
from app.services.activity_service import ActivityService
from app.services.permission_service import ensure_admin, ensure_tab_access
from app.services.requirement_service import RequirementService
from app.services.zentao_task_sync_service import ZentaoTaskSyncService

router = APIRouter()
logger = logging.getLogger(__name__)

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
    # 禅道任务联动（2026-06-29）：父任务起止日期；提供则在禅道建测试任务。
    task_start_date: Optional[str] = None
    task_deadline: Optional[str] = None
    create_zentao_tasks: bool = True

class LinkMajorPayload(BaseModel):
    target_major_version_id: int
    source_major_version_id: int
    source_requirement_ids: list[int] = Field(default_factory=list)
    copy_status: bool = True


class ReqStatusUpdatePayload(BaseModel):
    case_completed: Optional[bool] = None
    test_completed: Optional[bool] = None


class CaseUpdatePayload(BaseModel):
    case_ids: list[str] = Field(default_factory=list)
    case_completed: bool


class ReqTestNotesPayload(BaseModel):
    test_notes: Optional[str] = None


class EstimatedHoursPayload(BaseModel):
    estimated_test_hours: float = Field(gt=0, le=999)


class StartTaskPayload(BaseModel):
    hours: Optional[float] = None


class RequirementStoryBindingPayload(BaseModel):
    zentao_story_id: Optional[int] = None


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

@router.get("/requirements/admin/list")
def admin_list_requirements(
    major_version_id: Optional[int] = None,
    software_id: Optional[int] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    ensure_tab_access(current_user, "assign")
    q = (
        db.query(Requirement)
        .options(joinedload(Requirement.owner), joinedload(Requirement.retester), joinedload(Requirement.test_cases), joinedload(Requirement.major_version))
        .order_by(Requirement.major_version_id.asc(), Requirement.id.asc())
    )
    if major_version_id:
        q = q.filter(Requirement.major_version_id == major_version_id)
    elif software_id:
        q = q.join(Version, Requirement.major_version_id == Version.id).filter(Version.software_id == software_id)

    rows = q.all()
    return [
        {
            "id": r.id,
            "zentao_req_id": r.zentao_req_id,
            "title": r.title,
            "owner": r.owner.shown_name if r.owner else None,
            "owner_id": r.owner_id,
            "case_completed": r.case_completed,
            "test_completed": r.test_completed,
            "retest_completed": r.retest_completed,
            "retested_by": r.retester.shown_name if r.retester else None,
            "status": r.status,
            "case_ids": [c.zentao_case_id for c in r.test_cases],
            "major_version_id": r.major_version_id,
            "major_version_name": r.major_version.version_no if r.major_version else "未知",
        }
        for r in rows
    ]


@router.get("/requirements/admin/progress")
def admin_requirements_progress(
    major_version_id: Optional[int] = None,
    software_id: Optional[int] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    ensure_tab_access(current_user, "assign")

    q = (
        db.query(Requirement)
        .options(joinedload(Requirement.owner), joinedload(Requirement.major_version), joinedload(Requirement.test_cases))
        .filter(Requirement.owner_id.isnot(None))
    )
    if major_version_id:
        q = q.filter(Requirement.major_version_id == major_version_id)
    elif software_id:
        q = q.join(Version, Requirement.major_version_id == Version.id).filter(Version.software_id == software_id)
    reqs = q.order_by(Requirement.major_version_id.asc(), Requirement.id.asc()).all()

    req_ids = [r.id for r in reqs]
    bug_count_map: dict[int, int] = {}
    if req_ids:
        bug_rows = (
            db.query(BugTracking.requirement_id, func.count(BugTracking.id))
            .filter(BugTracking.requirement_id.in_(req_ids))
            .group_by(BugTracking.requirement_id)
            .all()
        )
        bug_count_map = {rid: cnt for rid, cnt in bug_rows}

    owners_map: dict[int, dict] = {}
    for r in reqs:
        oid = r.owner_id or -1
        owner_name = r.owner.shown_name if r.owner else "未分配"
        major_name = r.major_version.version_no if r.major_version else "未知"
        owner_bucket = owners_map.setdefault(
            oid,
            {
                "owner_id": oid,
                "owner_name": owner_name,
                "requirements": [],
                "major_summary_map": {},
            },
        )
        owner_bucket["requirements"].append(
            {
                "id": r.id,
                "zentao_req_id": r.zentao_req_id,
                "title": r.title,
                "major_version_id": r.major_version_id,
                "major_version_name": major_name,
                "case_completed": bool(r.case_completed),
                "test_completed": bool(r.test_completed),
                "retest_completed": bool(r.retest_completed),
                "case_count": len(r.test_cases or []),
                "bug_count": int(bug_count_map.get(r.id, 0)),
            }
        )
        s = owner_bucket["major_summary_map"].setdefault(
            r.major_version_id,
            {
                "major_version_id": r.major_version_id,
                "major_version_name": major_name,
                "total_requirements": 0,
                "case_done": 0,
                "test_done": 0,
            },
        )
        s["total_requirements"] += 1
        s["case_done"] += 1 if r.case_completed else 0
        s["test_done"] += 1 if r.test_completed else 0

    owners = []
    for owner in owners_map.values():
        major_summaries = list(owner["major_summary_map"].values())
        major_summaries.sort(key=lambda x: x["major_version_name"])
        for m in major_summaries:
            m["case_pending"] = max(0, m["total_requirements"] - m["case_done"])
            m["test_pending"] = max(0, m["total_requirements"] - m["test_done"])
        req_list = owner["requirements"]
        req_list.sort(key=lambda x: (x["major_version_name"], x["zentao_req_id"]))
        owners.append(
            {
                "owner_id": owner["owner_id"],
                "owner_name": owner["owner_name"],
                "major_summaries": major_summaries,
                "requirements": req_list,
            }
        )
    owners.sort(key=lambda x: x["owner_name"])

    retest_q = db.query(Requirement).options(joinedload(Requirement.major_version)).filter(
        Requirement.test_completed.is_(True),
        Requirement.retest_completed.is_(False),
        Requirement.owner_id.isnot(None),
    )
    if major_version_id:
        retest_q = retest_q.filter(Requirement.major_version_id == major_version_id)
    elif software_id:
        retest_q = retest_q.join(Version, Requirement.major_version_id == Version.id).filter(Version.software_id == software_id)
    retest_rows = retest_q.all()
    retest_map: dict[int, dict] = {}
    for r in retest_rows:
        k = r.major_version_id
        bucket = retest_map.setdefault(
            k,
            {
                "major_version_id": k,
                "major_version_name": r.major_version.version_no if r.major_version else "未知",
                "pending_retest_count": 0,
            },
        )
        bucket["pending_retest_count"] += 1
    retest_pending_by_major = sorted(retest_map.values(), key=lambda x: x["major_version_name"])

    total_requirements = len(reqs)
    case_done_total = sum(1 for r in reqs if r.case_completed)
    test_done_total = sum(1 for r in reqs if r.test_completed)
    return {
        "summary": {
            "owners": len(owners),
            "requirements": total_requirements,
            "case_done": case_done_total,
            "case_pending": max(0, total_requirements - case_done_total),
            "test_done": test_done_total,
            "test_pending": max(0, total_requirements - test_done_total),
            "retest_pending_total": sum(x["pending_retest_count"] for x in retest_pending_by_major),
        },
        "owners": owners,
        "retest_pending_by_major": retest_pending_by_major,
    }


@router.post("/requirements/admin/sync-zentao")
def admin_sync_requirements_from_zentao(
    major_version_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    ensure_tab_access(current_user, "assign")
    service = RequirementService(db)
    return service.sync_zentao_major_requirements(major_version_id, current_user)

@router.get("/requirements/admin/link-options")
def admin_link_options(
    source_major_version_id: int,
    target_major_version_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    ensure_tab_access(current_user, "assign")
    service = RequirementService(db)
    return service.list_requirements_for_link(source_major_version_id, target_major_version_id)

@router.post("/requirements/admin/link-major")
def admin_link_major(
    payload: LinkMajorPayload,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    ensure_tab_access(current_user, "assign")
    service = RequirementService(db)
    return service.link_requirements_from_major(
        target_major_version_id=payload.target_major_version_id,
        source_major_version_id=payload.source_major_version_id,
        source_requirement_ids=payload.source_requirement_ids,
        copy_status=payload.copy_status,
        actor_id=current_user.id,
    )


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
        joinedload(Requirement.test_notes_updated_by),
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
        .filter(BugTracking.requirement_id.in_(req_ids), BugTracking.source_type.in_([BugSourceType.MANUAL, BugSourceType.REQUIREMENT]))
        .all()
        if req_ids
        else []
    )

    # 「指派给我的」判定：优先用禅道账号(已绑定)，否则回退真实姓名匹配。
    my_account = (current_user.zentao_account or "").strip().lower()
    my_name = (current_user.shown_name or "").strip().lower()

    def _assigned_to_me(b: BugTracking) -> bool:
        acc = (b.zentao_assigned_to_account or "").strip().lower()
        if my_account and acc:
            return acc == my_account
        nm = (b.zentao_assigned_to_name or "").strip().lower()
        if my_name and nm:
            return nm == my_name
        return False

    def _bug_dict(b: BugTracking) -> dict:
        return {
            "id": b.id,
            "bug_id": b.bug_id,
            "zentao_bug_url": b.zentao_bug_url,
            "zentao_bug_title": b.zentao_bug_title,
            # 缓存的禅道状态/指派信息：前端可即时显示（先显示，hydrator 再慢慢更新）
            "zentao_live_status": b.zentao_live_status,
            "zentao_assigned_to_name": b.zentao_assigned_to_name,
            "zentao_assigned_to_account": b.zentao_assigned_to_account,
            "assigned_to_me": _assigned_to_me(b),
            "found_minor_version_no": minors.get(b.found_minor_version_id, "未知") if b.found_minor_version_id else "未知",
            "fixed_minor_version_no": minors.get(b.fixed_minor_version_id, "未知") if b.fixed_minor_version_id else None,
            "dispatched_to_name": b.dispatched_to.shown_name if b.dispatched_to else None,
        }

    case_bug_map: dict[str, list[dict]] = {}
    for b in case_bug_rows:
        case_bug_map.setdefault(b.source_ref or "", []).append(_bug_dict(b))

    free_bug_map: dict[int, list[dict]] = {}
    for b in free_bug_rows:
        free_bug_map.setdefault(b.requirement_id or -1, []).append(_bug_dict(b))

    return [
        {
            "id": r.id,
            "zentao_req_id": r.zentao_req_id,
            "title": r.title,
            "case_completed": r.case_completed,
            "test_completed": r.test_completed,
            "major_version_id": r.major_version_id,
            "major_version_name": r.major_version.version_no if r.major_version else "",
            # 禅道任务联动
            "estimated_test_hours": r.estimated_test_hours,
            "zentao_task_id": r.zentao_task_id,
            "zentao_task_status": r.zentao_task_status_cache,
            "task_started_at": r.task_started_at.isoformat() if r.task_started_at else None,
            "task_finished_at": r.task_finished_at.isoformat() if r.task_finished_at else None,
            "test_notes": r.test_notes,
            "test_notes_updated_at": r.test_notes_updated_at.isoformat() if r.test_notes_updated_at else None,
            "test_notes_updated_by_name": r.test_notes_updated_by.shown_name if r.test_notes_updated_by else None,
            "test_cases": [{"id": c.id, "zentao_case_id": c.zentao_case_id, "zentao_case_url": c.zentao_case_url, "bugs": case_bug_map.get(str(c.id), [])} for c in r.test_cases],
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


@router.get("/requirements/{requirement_id}/story-binding-preview")
def preview_requirement_story_binding(
    requirement_id: int,
    story_id: Optional[int] = None,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    ensure_admin(current_user)
    return RequirementService(db).preview_story_binding(requirement_id, story_id=story_id)


@router.put("/requirements/{requirement_id}/story-binding")
def update_requirement_story_binding(
    requirement_id: int,
    payload: RequirementStoryBindingPayload,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    ensure_admin(current_user)
    return RequirementService(db).update_story_binding(
        requirement_id,
        story_id=payload.zentao_story_id,
        actor_id=current_user.id,
    )


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
    ensure_tab_access(current_user, "assign")
    service = RequirementService(db)
    users_map = {u.id: u.shown_name for u in db.query(User).all()}
    result = service.assign_and_publish(
        payload.major_version_id,
        [{"requirement_id": item.requirement_id, "owner_id": item.owner_id} for item in payload.assignments],
        users_map,
        actor_id=current_user.id,
    )
    major = db.query(Version).filter(Version.id == payload.major_version_id).first()
    major_text = major.version_no if major else f"ID:{payload.major_version_id}"

    # 禅道任务联动：在禅道建/改派测试任务（失败不影响本地分配，partial 返回）
    zentao_result: dict | None = None
    if payload.create_zentao_tasks:
        try:
            task_service = ZentaoTaskSyncService(db)
            zentao_result = task_service.create_tasks_for_assignment(
                payload.major_version_id,
                [{"requirement_id": item.requirement_id, "owner_id": item.owner_id} for item in payload.assignments],
                est_started=payload.task_start_date,
                deadline=payload.task_deadline,
                actor=current_user,
            )
        except Exception as exc:  # 兜底，绝不让禅道异常打断分配发布
            logger.warning("assign_and_publish zentao task sync failed: %s", exc)
            zentao_result = {"ok": False, "errors": [str(exc)]}

    if result.get("change_msgs"):
        md = (
            "### 📢 需求负责人变更通知\n"
            f"> 大版本：**{major_text}**\n\n"
            + "\n".join(result["change_msgs"])
            + "\n\n*提示：移交需求已自动重置完成状态，请新负责人重新校验并勾选。*"
        )
        await send_markdown(md)
    else:
        await send_markdown(f"✅ 需求分配状态已更新发布\n> 大版本：**{major_text}**")
    return {"message": result.get("message", "Assignments updated"), "zentao": zentao_result}


@router.post("/requirements/create-zentao-tasks")
def create_zentao_tasks(payload: AssignPublishPayload, current_user=Depends(get_current_user), db: Session = Depends(get_db)):
    """仅在禅道创建/改派测试任务，不改动本地负责人、不发送企微通知。

    用于「分配并发布（企微）」之后单独补建任务：创建有时会部分失败，若靠重新点击
    发布来重试会重复推送企微通知，故独立成一个按钮。幂等：已建过的按 story 认领，
    禅道侧已取消/关闭的会重建。
    """
    ensure_tab_access(current_user, "assign")
    task_service = ZentaoTaskSyncService(db)
    try:
        zentao_result = task_service.create_tasks_for_assignment(
            payload.major_version_id,
            [{"requirement_id": item.requirement_id, "owner_id": item.owner_id} for item in payload.assignments],
            est_started=payload.task_start_date,
            deadline=payload.task_deadline,
            actor=current_user,
        )
    except Exception as exc:  # 兜底，禅道异常不抛 500
        logger.warning("create_zentao_tasks failed: %s", exc)
        zentao_result = {"ok": False, "errors": [str(exc)]}
    return {"message": "禅道任务创建已执行", "zentao": zentao_result}


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


@router.put("/requirements/{requirement_id}/estimated-hours")
def update_requirement_estimated_hours(
    requirement_id: int,
    payload: EstimatedHoursPayload,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    service = RequirementService(db)
    return service.update_estimated_test_hours(requirement_id, payload.estimated_test_hours, current_user)


@router.post("/requirements/{requirement_id}/task/start")
def start_requirement_task(
    requirement_id: int,
    payload: StartTaskPayload,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    service = RequirementService(db)
    return service.start_requirement_task(requirement_id, current_user, hours=payload.hours)


@router.put("/requirements/{requirement_id}/test-notes")
def update_requirement_test_notes(
    requirement_id: int,
    payload: ReqTestNotesPayload,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    service = RequirementService(db)
    return service.update_test_notes(requirement_id, payload.test_notes, current_user)


@router.get("/requirements/{requirement_id}/timeline")
def requirement_timeline(
    requirement_id: int,
    _: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return ActivityService(db).requirement_timeline(requirement_id)


@router.put("/requirements/{requirement_id}/cases")
def update_requirement_cases(requirement_id: int, payload: CaseUpdatePayload, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    service = RequirementService(db)
    case_ids = _parse_multiple_ids(payload.case_ids, U_PATTERN, "test case")
    return service.update_requirement_cases(requirement_id, case_ids, payload.case_completed, actor_id=current_user.id)
