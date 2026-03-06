from __future__ import annotations

import re
import tempfile
import uuid
from csv import DictWriter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated, Optional

import httpx
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import Depends, FastAPI, HTTPException, Query, status
from fastapi.responses import FileResponse
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from fastapi.staticfiles import StaticFiles
from jose import JWTError, jwt
from openpyxl import Workbook
from pydantic import BaseModel, Field
from sqlalchemy import and_, func
from sqlalchemy.orm import Session, joinedload

from app.database import SessionLocal, get_db
from app.init_db import init_db
from app.core.config import settings
from app.models import (
    BugStage5Record,
    BugSourceType,
    BugTracking,
    Requirement,
    RequirementStatus,
    TestCase,
    TestExecution,
    User,
    UserRole,
    Version,
    VersionType,
)
from app.services.auth_service import AuthService
from app.services.bug_service import BugService
from app.services.export_service import ExportService
from app.services.push_service import PushService
from app.services.report_service import ReportService
from app.services.requirement_service import RequirementService
from app.services.retest_service import RetestService
from app.services.stage5_service import Stage5Service
from app.services.user_service import UserService
from app.integrations.wecom import send_markdown

SECRET_KEY = settings.secret_key
ALGORITHM = settings.algorithm
ACCESS_TOKEN_EXPIRE_MINUTES = settings.access_token_expire_minutes
WECHAT_WEBHOOK_URL = settings.wecom_webhook_url

R_PATTERN = re.compile(r"^r#\d+$")
U_PATTERN = re.compile(r"^u#\d+$")
B_PATTERN = re.compile(r"^b#\d+$")

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/token")
app = FastAPI(title="APPAuto", version="0.3.0")
scheduler = BackgroundScheduler(timezone="Asia/Shanghai")

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")


@app.get("/", include_in_schema=False)
def root_page():
    return FileResponse(str(FRONTEND_DIR / "index.html"))


@app.get("/login", include_in_schema=False)
def login_page():
    return FileResponse(str(FRONTEND_DIR / "login.html"))


@app.get("/dashboard", include_in_schema=False)
def dashboard_page():
    return FileResponse(str(FRONTEND_DIR / "index.html"))


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserOut(BaseModel):
    id: int
    username: str
    role: str
    is_team_member: bool = True


class UserCreate(BaseModel):
    username: str = Field(min_length=3, max_length=50)
    password: str = Field(min_length=3, max_length=128)
    role: UserRole = UserRole.USER


class UserRoleUpdate(BaseModel):
    role: UserRole


class PasswordChangeSelf(BaseModel):
    old_password: str
    new_password: str = Field(min_length=3, max_length=128)


class PasswordResetAdmin(BaseModel):
    new_password: str = Field(min_length=3, max_length=128)


class TeamStatusUpdate(BaseModel):
    is_team_member: bool


class VersionCreate(BaseModel):
    version_no: str
    version_type: VersionType
    parent_id: Optional[int] = None


class RequirementCreate(BaseModel):
    zentao_req_id: str
    title: str
    major_version_id: int = Field(gt=0)


class ReqBatchItem(BaseModel):
    zentao_req_id: str
    title: str


class RequirementBatchCreate(BaseModel):
    major_version_id: int
    items: list[ReqBatchItem]


class AssignItem(BaseModel):
    requirement_id: int
    owner_id: Optional[int] = None


class AssignPublishPayload(BaseModel):
    major_version_id: int
    assignments: list[AssignItem]


class CaseUpdatePayload(BaseModel):
    case_ids: list[str] = Field(default_factory=list)
    case_completed: bool


class TestExecutionPayload(BaseModel):
    minor_version_id: int
    bug_id: Optional[str] = None
    source_case_id: Optional[str] = None
    result_status: str = "passed"
    test_completed: bool
    notes: Optional[str] = None


class ProgressPushPayload(BaseModel):
    major_version_id: int


class RetestPayload(BaseModel):
    retest_completed: bool
    retest_passed: Optional[bool] = None
    retest_minor_version_id: Optional[int] = None


class ReqStatusUpdate(BaseModel):
    case_completed: Optional[bool] = None
    test_completed: Optional[bool] = None


class Stage5ResultPayload(BaseModel):
    minor_version_id: int
    test_done: bool
    newly_found_bug_id: Optional[str] = None
    resolution: str = "fixed"


class Stage5IssueCreatePayload(BaseModel):
    major_version_id: int
    requirement_id: Optional[int] = None
    source_type: BugSourceType = BugSourceType.MANUAL
    source_ref: Optional[str] = None
    bug_id: str
    minor_version_id: Optional[int] = None


class AddCasePayload(BaseModel):
    zentao_case_id: str


class ExecutionBugPayload(BaseModel):
    bug_id: str
    minor_version_id: int
    requirement_id: int
    source_type: BugSourceType
    source_ref: Optional[str] = None


class DispatchPayload(BaseModel):
    user_id: int


class BugRetestFailPayload(BaseModel):
    is_retest_failed: bool


def _create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (expires_delta or timedelta(minutes=15))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


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


def _ensure_major_version_exists(db: Session, major_version_id: int) -> None:
    version = db.query(Version).filter(Version.id == major_version_id).first()
    if not version:
        raise HTTPException(status_code=400, detail="major_version_id does not exist")
    if version.version_type != VersionType.MAJOR:
        raise HTTPException(status_code=400, detail="major_version_id must reference a major version")


async def _send_wechat_markdown(markdown: str) -> None:
    await send_markdown(markdown)


def _authenticate_user(db: Session, username: str, password: str) -> Optional[User]:
    user = db.query(User).filter(User.username == username).first()
    if not user or not user.verify_password(password):
        return None
    return user


def get_current_user(
    token: Annotated[str, Depends(oauth2_scheme)],
    db: Annotated[Session, Depends(get_db)],
) -> User:
    credential_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str | None = payload.get("sub")
        session_token = payload.get("session")
        if username is None:
            raise credential_exception
    except JWTError as exc:
        raise credential_exception from exc

    user = db.query(User).filter(User.username == username).first()
    # 核心拦截逻辑：如果用户不存在，或者 token 里的 session 已经被新的登录冲刷掉了，则拒绝访问
    if not user or session_token != user.session_token:
        raise credential_exception
    return user


def require_admin(current_user: Annotated[User, Depends(get_current_user)]) -> User:
    if current_user.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="Admin only")
    return current_user


def _build_export_rows(db: Session, major_version_id: Optional[int], minor_version_id: Optional[int]) -> list[dict[str, str]]:
    query = db.query(Requirement).options(
        joinedload(Requirement.major_version),
        joinedload(Requirement.owner),
        joinedload(Requirement.test_cases),
        joinedload(Requirement.test_executions).joinedload(TestExecution.minor_version),
    )
    if major_version_id is not None:
        query = query.filter(Requirement.major_version_id == major_version_id)

    rows: list[dict[str, str]] = []
    for req in query.order_by(Requirement.id.asc()).all():
        executions = req.test_executions
        if minor_version_id is not None:
            executions = [e for e in executions if e.minor_version_id == minor_version_id]
        if not executions:
            executions = [None]

        for exe in executions:
            rows.append(
                {
                    "requirement_id": str(req.id),
                    "major_version": req.major_version.version_no,
                    "zentao_req_id": req.zentao_req_id,
                    "title": req.title,
                    "owner": req.owner.username if req.owner else "",
                    "case_ids": ", ".join(c.zentao_case_id for c in req.test_cases),
                    "case_completed": str(req.case_completed),
                    "test_completed": str(req.test_completed),
                    "status": req.status.value,
                    "minor_version": exe.minor_version.version_no if exe else "",
                    "result_status": exe.result_status if exe else "",
                    "bug_id": exe.bug_id if exe else "",
                    "source_case_id": exe.source_case_id if exe else "",
                    "executed_at": exe.executed_at.isoformat() if exe else "",
                }
            )
    return rows


def _push_daily_report() -> None:
    db = SessionLocal()
    try:
        import asyncio
        service = PushService(db)
        asyncio.run(service.send_markdown(service.build_daily_report_message()))
    finally:
        db.close()


@app.on_event("startup")
def on_startup() -> None:
    init_db()
    if not scheduler.running:
        scheduler.add_job(_push_daily_report, CronTrigger(hour=18, minute=0), id="daily_report", replace_existing=True)
        scheduler.start()


@app.on_event("shutdown")
def on_shutdown() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)


@app.post("/auth/token", response_model=Token)
def login(
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
    db: Annotated[Session, Depends(get_db)],
):
    result = AuthService.login_with_form(db, form_data.username, form_data.password)
    return Token(**result)


@app.get("/auth/me", response_model=UserOut)
def me(current_user: Annotated[User, Depends(get_current_user)]):
    return UserOut(id=current_user.id, username=current_user.username, role=current_user.role.value, is_team_member=current_user.is_team_member)


@app.get("/users", response_model=list[UserOut])
def list_users(
    _: Annotated[User, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
):
    users = UserService.list_users(db)
    return [UserOut(id=u["id"], username=u["username"], role=u["role"], is_team_member=u["is_team_member"]) for u in users]


@app.post("/users", response_model=UserOut, status_code=201)
def create_user(
    payload: UserCreate,
    _: Annotated[User, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
):
    user = UserService.create_user(db, payload.username, payload.password, payload.role)
    return UserOut(id=user["id"], username=user["username"], role=user["role"], is_team_member=user["is_team_member"])


@app.put("/users/{user_id}/role")
def update_user_role(
    user_id: int,
    payload: UserRoleUpdate,
    current_user: Annotated[User, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
):
    return UserService.update_role(db, user_id, payload.role, actor_id=current_user.id)


@app.delete("/users/{user_id}")
def delete_user(
    user_id: int,
    current_user: Annotated[User, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
):
    if user_id == current_user.id:
        raise HTTPException(status_code=400, detail="不能删除当前登录用户")
    return UserService.delete_user(db, user_id, actor_id=current_user.id)


@app.put("/users/{user_id}/team-status")
def update_user_team_status(
    user_id: int,
    payload: TeamStatusUpdate,
    current_user: Annotated[User, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
):
    return UserService.update_team_status(db, user_id, payload.is_team_member, actor_id=current_user.id)


@app.put("/auth/password")
def change_my_password(payload: PasswordChangeSelf, current_user: Annotated[User, Depends(get_current_user)], db: Session = Depends(get_db)):
    return UserService.change_my_password(db, current_user, payload.old_password, payload.new_password)


@app.put("/users/{user_id}/password")
def reset_user_password(user_id: int, payload: PasswordResetAdmin, current_user: Annotated[User, Depends(require_admin)], db: Session = Depends(get_db)):
    return UserService.reset_user_password(db, user_id, payload.new_password, actor_id=current_user.id)


@app.post("/versions", status_code=201)
def create_version(payload: VersionCreate, _: Annotated[User, Depends(get_current_user)], db: Annotated[Session, Depends(get_db)]):
    if payload.version_type == VersionType.MINOR and not payload.parent_id:
        raise HTTPException(status_code=400, detail="Minor version must specify parent major version")
    if payload.version_type == VersionType.MAJOR and payload.parent_id is not None:
        raise HTTPException(status_code=400, detail="Major version cannot have a parent")

    if db.query(Version).filter(Version.version_no == payload.version_no, Version.version_type == payload.version_type).first():
        raise HTTPException(status_code=400, detail="Version already exists")

    version = Version(version_no=payload.version_no, version_type=payload.version_type, parent_id=payload.parent_id)
    db.add(version)
    db.commit()
    db.refresh(version)
    return {"id": version.id, "version_no": version.version_no, "version_type": version.version_type, "parent_id": version.parent_id}


@app.get("/versions")
def list_versions(_: Annotated[User, Depends(get_current_user)], db: Annotated[Session, Depends(get_db)]):
    rows = db.query(Version).order_by(Version.created_at.desc()).all()
    return [{"id": v.id, "version_no": v.version_no, "version_type": v.version_type, "parent_id": v.parent_id} for v in rows]


@app.put("/versions/{version_id}")
def update_version(version_id: int, payload: VersionCreate, _: Annotated[User, Depends(require_admin)], db: Session = Depends(get_db)):
    v = db.query(Version).filter(Version.id == version_id).first()
    if not v:
        raise HTTPException(status_code=404, detail="Version not found")
    v.version_no = payload.version_no
    v.version_type = payload.version_type
    v.parent_id = payload.parent_id
    db.commit()
    return {"message": "Version updated"}


@app.delete("/versions/{version_id}")
def delete_version(version_id: int, _: Annotated[User, Depends(require_admin)], db: Session = Depends(get_db)):
    v = db.query(Version).filter(Version.id == version_id).first()
    if not v:
        raise HTTPException(status_code=404, detail="Version not found")
    db.delete(v)
    db.commit()
    return {"message": "Version deleted"}


@app.post("/requirements", status_code=201)
def create_requirement(payload: RequirementCreate, _: Annotated[User, Depends(get_current_user)], db: Annotated[Session, Depends(get_db)]):
    service = RequirementService(db)
    return service.create_requirement(payload.zentao_req_id, payload.title, payload.major_version_id)


@app.post("/requirements/batch", status_code=201)
def batch_create_requirements(payload: RequirementBatchCreate, _: Annotated[User, Depends(require_admin)], db: Annotated[Session, Depends(get_db)]):
    service = RequirementService(db)
    items = [{"zentao_req_id": item.zentao_req_id, "title": item.title} for item in payload.items]
    return service.batch_create_requirements(payload.major_version_id, items)


@app.get("/requirements")
def list_requirements(major_version_id: int = Query(...), db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    service = RequirementService(db)
    return service.list_requirements(major_version_id)


@app.get("/requirements/my-workbench")
def my_workbench(current_user: Annotated[User, Depends(get_current_user)], db: Session = Depends(get_db), major_version_id: Optional[int] = None, mode: str = "version"):
    query = db.query(Requirement).options(
        joinedload(Requirement.major_version),
        joinedload(Requirement.test_cases)
    ).filter(Requirement.owner_id == current_user.id)

    # 支持按单个版本查看，或查看所有未完成测试的跨版本需求
    if mode == "version" and major_version_id:
        query = query.filter(Requirement.major_version_id == major_version_id)
    elif mode == "all_pending":
        query = query.filter(Requirement.test_completed == False)

    reqs = query.order_by(Requirement.id.desc()).all()

    req_ids = [r.id for r in reqs]
    case_ids = [c.id for r in reqs for c in r.test_cases]
    # 获取小版本映射字典
    minors = {v.id: v.version_no for v in db.query(Version).filter(Version.version_type == VersionType.MINOR).all()}

    # 获取 bugs
    case_bug_rows = db.query(BugTracking).options(joinedload(BugTracking.dispatched_to)).filter(
        BugTracking.requirement_id.in_(req_ids), BugTracking.source_type == BugSourceType.CASE, BugTracking.source_ref.in_([str(x) for x in case_ids] if case_ids else ["-1"])
    ).all() if req_ids else []

    free_bug_rows = db.query(BugTracking).options(joinedload(BugTracking.dispatched_to)).filter(
        BugTracking.requirement_id.in_(req_ids), BugTracking.source_type == BugSourceType.MANUAL
    ).all() if req_ids else []

    case_bug_map: dict[str, list[dict]] = {}
    for b in case_bug_rows:
        case_bug_map.setdefault(b.source_ref or "", []).append({
            "id": b.id, "bug_id": b.bug_id,
            "found_minor_version_no": minors.get(b.found_minor_version_id, "未知") if b.found_minor_version_id else "未知",
            "fixed_minor_version_no": minors.get(b.fixed_minor_version_id, "未知") if b.fixed_minor_version_id else None,
            "dispatched_to_name": b.dispatched_to.username if b.dispatched_to else None
        })

    free_bug_map: dict[int, list[dict]] = {}
    for b in free_bug_rows:
        free_bug_map.setdefault(b.requirement_id or -1, []).append({
            "id": b.id, "bug_id": b.bug_id,
            "found_minor_version_no": minors.get(b.found_minor_version_id, "未知") if b.found_minor_version_id else "未知",
            "fixed_minor_version_no": minors.get(b.fixed_minor_version_id, "未知") if b.fixed_minor_version_id else None,
            "dispatched_to_name": b.dispatched_to.username if b.dispatched_to else None
        })

    return [
        {
            "id": r.id, "zentao_req_id": r.zentao_req_id, "title": r.title,
            "case_completed": r.case_completed, "test_completed": r.test_completed,
            "major_version_name": r.major_version.version_no if r.major_version else "",
            "test_cases": [{"id": c.id, "zentao_case_id": c.zentao_case_id, "bugs": case_bug_map.get(str(c.id), [])} for c in r.test_cases],
            "free_bugs": free_bug_map.get(r.id, []),
        }
        for r in reqs
    ]


@app.post("/requirements/{req_id}/cases")
def add_case_to_requirement(req_id: int, payload: AddCasePayload, current_user: Annotated[User, Depends(get_current_user)], db: Session = Depends(get_db)):
    if not U_PATTERN.match(payload.zentao_case_id):
        raise HTTPException(status_code=400, detail="zentao_case_id must be like u#xxxx")
    service = RequirementService(db)
    return service.add_case_to_requirement(req_id, payload.zentao_case_id, actor_id=current_user.id)


@app.put("/requirements/{requirement_id}")
def update_requirement(requirement_id: int, payload: RequirementCreate, current_user: Annotated[User, Depends(require_admin)], db: Session = Depends(get_db)):
    service = RequirementService(db)
    return service.update_requirement(requirement_id, payload.zentao_req_id, payload.title, payload.major_version_id, actor_id=current_user.id)


@app.delete("/requirements/{requirement_id}")
def delete_requirement(requirement_id: int, current_user: Annotated[User, Depends(require_admin)], db: Session = Depends(get_db)):
    service = RequirementService(db)
    return service.delete_requirement(requirement_id, actor_id=current_user.id)


@app.put("/test-cases/{case_id}")
def update_case(case_id: int, payload: AddCasePayload, current_user: Annotated[User, Depends(get_current_user)], db: Session = Depends(get_db)):
    if not U_PATTERN.match(payload.zentao_case_id):
        raise HTTPException(status_code=400, detail="zentao_case_id must be like u#xxxx")
    service = RequirementService(db)
    return service.update_case_identifier(case_id, payload.zentao_case_id, actor_id=current_user.id)


@app.delete("/test-cases/{case_id}")
def delete_case(case_id: int, current_user: Annotated[User, Depends(get_current_user)], db: Session = Depends(get_db)):
    service = RequirementService(db)
    return service.delete_case(case_id, actor_id=current_user.id)


@app.post("/bugs/execution")
def create_execution_bug(payload: ExecutionBugPayload, current_user: Annotated[User, Depends(get_current_user)], db: Session = Depends(get_db)):
    if not B_PATTERN.match(payload.bug_id):
        raise HTTPException(status_code=400, detail="bug_id must be like b#xxxx")
    if payload.source_type not in [BugSourceType.CASE, BugSourceType.MANUAL, BugSourceType.RETEST]:
        raise HTTPException(status_code=400, detail="source_type only supports case/manual/retest")
    service = BugService(db)
    return service.create_execution_bug(
        bug_id=payload.bug_id,
        minor_version_id=payload.minor_version_id,
        requirement_id=payload.requirement_id,
        source_type=payload.source_type,
        source_ref=payload.source_ref,
        actor=current_user,
    )


@app.post("/requirements/assign-and-publish")
async def assign_and_publish(payload: AssignPublishPayload, current_user: Annotated[User, Depends(require_admin)], db: Annotated[Session, Depends(get_db)]):
    service = RequirementService(db)
    users_map = {u.id: u.username for u in db.query(User).all()}
    result = service.assign_and_publish(
        payload.major_version_id,
        [{"requirement_id": item.requirement_id, "owner_id": item.owner_id} for item in payload.assignments],
        users_map,
        actor_id=current_user.id,
    )

    if result["change_msgs"]:
        md = "### 需求负责人变更通知\n" + "\n".join(result["change_msgs"]) + "\n\n*提示：移交的需求已自动重置完成状态，请新负责人重新校验并勾选。*"
        await _send_wechat_markdown(md)
    else:
        await _send_wechat_markdown("需求分配状态已整体更新发布")

    return {"message": result["message"]}

@app.patch("/requirements/{requirement_id}/status")
def patch_requirement_status(
    requirement_id: int,
    payload: ReqStatusUpdate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    service = RequirementService(db)
    return service.patch_requirement_status(
        requirement_id,
        current_user,
        case_completed=payload.case_completed,
        test_completed=payload.test_completed,
    )


@app.put("/requirements/{requirement_id}/cases")
def update_requirement_cases(requirement_id: int, payload: CaseUpdatePayload, current_user: Annotated[User, Depends(get_current_user)], db: Annotated[Session, Depends(get_db)]):
    service = RequirementService(db)
    case_ids = _parse_multiple_ids(payload.case_ids, U_PATTERN, "test case")
    return service.update_requirement_cases(requirement_id, case_ids, payload.case_completed, actor_id=current_user.id)


@app.put("/requirements/{requirement_id}/test-execution")
def upsert_test_execution(requirement_id: int, payload: TestExecutionPayload, current_user: Annotated[User, Depends(get_current_user)], db: Annotated[Session, Depends(get_db)]):
    service = RequirementService(db)
    return service.upsert_test_execution(
        requirement_id=requirement_id,
        minor_version_id=payload.minor_version_id,
        bug_id=payload.bug_id,
        source_case_id=payload.source_case_id,
        result_status=payload.result_status,
        notes=payload.notes,
        test_completed=payload.test_completed,
        actor_id=current_user.id,
    )


@app.post("/push/case-progress")
async def push_case_progress(payload: ProgressPushPayload, current_user: Annotated[User, Depends(get_current_user)], db: Annotated[Session, Depends(get_db)]):
    service = PushService(db)
    return await service.push_case_progress(payload.major_version_id, current_user)


@app.post("/push/test-progress")
async def push_test_progress(minor_version_id: int, major_version_id: int, current_user: Annotated[User, Depends(get_current_user)], db: Annotated[Session, Depends(get_db)]):
    service = PushService(db)
    return await service.push_test_progress(major_version_id, minor_version_id, current_user)


@app.get("/retest/workbench")
def get_retest_workbench(major_version_id: int, current_user: Annotated[User, Depends(get_current_user)], db: Annotated[Session, Depends(get_db)]):
    service = RetestService(db)
    return service.get_workbench(major_version_id, current_user)


@app.put("/requirements/{requirement_id}/retest")
def submit_retest(requirement_id: int, payload: RetestPayload, current_user: Annotated[User, Depends(get_current_user)], db: Annotated[Session, Depends(get_db)]):
    service = RetestService(db)
    return service.submit_retest(
        requirement_id,
        retest_completed=payload.retest_completed,
        retest_passed=payload.retest_passed,
        retest_minor_version_id=payload.retest_minor_version_id,
        current_user=current_user,
    )


@app.post("/push/retest-result")
async def push_retest_result(major_version_id: int, current_user: Annotated[User, Depends(get_current_user)], db: Annotated[Session, Depends(get_db)]):
    service = PushService(db)
    return await service.push_retest_result(major_version_id, current_user)


@app.get("/stage5/overview")
def stage5_overview(major_version_id: int, current_user: Annotated[User, Depends(get_current_user)], db: Annotated[Session, Depends(get_db)]):
    service = Stage5Service(db)
    return service.overview(major_version_id, current_user)


@app.get("/stage5/search-options")
def get_stage5_search_options(major_version_id: int, _: Annotated[User, Depends(get_current_user)], db: Session = Depends(get_db)):
    service = Stage5Service(db)
    return service.search_options(major_version_id)


@app.put("/stage5/bugs/{bug_track_id}/result")
async def submit_stage5_result(bug_track_id: int, payload: Stage5ResultPayload, current_user: Annotated[User, Depends(get_current_user)], db: Annotated[Session, Depends(get_db)]):
    service = Stage5Service(db)
    result = service.submit_result(
        bug_track_id,
        minor_version_id=payload.minor_version_id,
        test_done=payload.test_done,
        newly_found_bug_id=payload.newly_found_bug_id,
        resolution=payload.resolution,
        current_user=current_user,
    )
    if result["notice"]:
        await _send_wechat_markdown(result["notice"])
    return {"message": result["message"]}


@app.post("/stage5/issues")
async def add_stage5_issue(payload: Stage5IssueCreatePayload, current_user: Annotated[User, Depends(get_current_user)], db: Annotated[Session, Depends(get_db)]):
    if not B_PATTERN.match(payload.bug_id):
        raise HTTPException(status_code=400, detail="bug_id must be like b#xxxx")
    service = Stage5Service(db)
    return service.add_issue(
        major_version_id=payload.major_version_id,
        requirement_id=payload.requirement_id,
        source_type=payload.source_type,
        source_ref=payload.source_ref,
        bug_id=payload.bug_id,
        minor_version_id=payload.minor_version_id,
        current_user=current_user,
    )


@app.post("/stage5/push-status")
async def push_stage5_status(major_version_id: int, minor_version_id: int, _: Annotated[User, Depends(get_current_user)], db: Annotated[Session, Depends(get_db)]):
    service = PushService(db)
    return await service.push_stage5_status(major_version_id, minor_version_id)


@app.get("/reports/summary")
def reports_summary(
    start_date: date,
    end_date: date,
    user_id: Optional[int] = None,
    current_user: Annotated[User, Depends(get_current_user)] = None,
    db: Session = Depends(get_db),
):
    service = ReportService(db)
    return service.summary(start_date, end_date, current_user, user_id)


@app.get("/reports/advanced")
def reports_advanced(start_date: date, end_date: date, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    service = ReportService(db)
    return service.advanced(start_date, end_date)


@app.get("/reports/version-bugs")
def reports_version_bugs(_: Annotated[User, Depends(get_current_user)], db: Session = Depends(get_db)):
    service = ReportService(db)
    return service.version_bugs()


@app.get("/admin/data-overview")
def admin_data_overview(_: Annotated[User, Depends(require_admin)], db: Session = Depends(get_db)):
    # 预加载 test_cases 以避免 N+1 查询
    reqs = db.query(Requirement).options(joinedload(Requirement.test_cases)).order_by(Requirement.id.desc()).all()

    return {
        "users": [{"id": u.id, "username": u.username, "role": u.role.value, "is_team_member": u.is_team_member, "created_at": u.created_at.isoformat()} for u in db.query(User).order_by(User.id.asc()).all()],
        "versions": [{"id": v.id, "version_no": v.version_no, "version_type": v.version_type.value, "parent_id": v.parent_id} for v in db.query(Version).order_by(Version.created_at.desc()).all()],
        "requirements": [{"id": r.id, "zentao_req_id": r.zentao_req_id, "title": r.title, "major_version_id": r.major_version_id, "case_ids": [c.zentao_case_id for c in r.test_cases]} for r in reqs],
        "bugs": [{"id": b.id, "bug_id": b.bug_id, "major_version_id": b.major_version_id, "requirement_id": b.requirement_id, "source_type": b.source_type.value, "source_ref": b.source_ref, "found_minor_version_id": b.found_minor_version_id} for b in db.query(BugTracking).order_by(BugTracking.id.desc()).all()],
    }


@app.put("/bugs/{bug_id}")
def update_bug(bug_id: int, new_bug_id: str, current_user: Annotated[User, Depends(get_current_user)], db: Session = Depends(get_db)):
    if not B_PATTERN.match(new_bug_id):
        raise HTTPException(status_code=400, detail="Invalid bug format")
    service = BugService(db)
    return service.update_bug(bug_id, new_bug_id, actor_id=current_user.id)


@app.delete("/bugs/{bug_id}")
def delete_bug(bug_id: int, current_user: Annotated[User, Depends(get_current_user)], db: Session = Depends(get_db)):
    service = BugService(db)
    return service.delete_bug(bug_id, actor_id=current_user.id)


@app.patch("/bugs/{bug_id}/retest-fail")
def toggle_bug_retest_fail(bug_id: int, payload: BugRetestFailPayload, current_user: Annotated[User, Depends(get_current_user)], db: Session = Depends(get_db)):
    service = BugService(db)
    return service.toggle_retest_fail(bug_id, payload.is_retest_failed, actor_id=current_user.id)


@app.get("/bugs/search")
def search_bug(bug_id: str, _: Annotated[User, Depends(require_admin)], db: Session = Depends(get_db)):
    service = BugService(db)
    return service.search_bug(bug_id)


@app.post("/bugs/{bug_id}/dispatch")
async def dispatch_bug(bug_id: int, payload: DispatchPayload, current_user: Annotated[User, Depends(require_admin)], db: Annotated[Session, Depends(get_db)]):
    service = BugService(db)
    push_service = PushService(db)
    result, user, bug = service.dispatch_bug(bug_id, payload.user_id, actor_id=current_user.id)
    if user:
        await push_service.push_bug_dispatch_notice(bug.bug_id, user.username)
    return result


@app.get("/bugs/dispatched-to-me")
def dispatched_to_me(major_version_id: int, current_user: Annotated[User, Depends(get_current_user)], db: Session = Depends(get_db)):
    service = BugService(db)
    return service.dispatched_to_me(major_version_id, current_user)


@app.get("/bugs/dispatched-all")
def get_all_dispatched_bugs(_: Annotated[User, Depends(require_admin)], db: Session = Depends(get_db)):
    service = BugService(db)
    return service.dispatched_all()


@app.get("/export")
def export_data(format: str = Query("csv", pattern="^(csv|xlsx)$"), major_version_id: Optional[int] = Query(default=None), minor_version_id: Optional[int] = Query(default=None), _: User = Depends(get_current_user), db: Session = Depends(get_db)):
    service = ExportService(db)
    out_path, media_type = service.export(format, major_version_id, minor_version_id)
    return FileResponse(path=str(out_path), filename=out_path.name, media_type=media_type)
