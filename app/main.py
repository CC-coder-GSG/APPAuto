from __future__ import annotations

import os
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

SECRET_KEY = os.getenv("APP_SECRET_KEY", "change_me_in_production")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "720"))
WECHAT_WEBHOOK_URL = os.getenv("WECHAT_WEBHOOK_URL", "")

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
    if not WECHAT_WEBHOOK_URL:
        return
    payload = {"msgtype": "markdown", "markdown": {"content": markdown}}
    async with httpx.AsyncClient(timeout=10) as client:
        await client.post(WECHAT_WEBHOOK_URL, json=payload)


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
        today = datetime.utcnow().date()
        total = db.query(Requirement).count()
        tested = db.query(Requirement).filter(Requirement.test_completed.is_(True)).count()
        untested = total - tested
        new_bugs = (
            db.query(BugTracking)
            .filter(BugTracking.created_at >= datetime.combine(today, datetime.min.time()))
            .count()
        )
        import asyncio

        asyncio.run(
            _send_wechat_markdown(
                "\n".join(
                    [
                        "## 每日18:00测试进度播报",
                        f"- 总需求数: {total}",
                        f"- 已测数: {tested}",
                        f"- 未测数: {untested}",
                        f"- 当日新增 b# Bug 数: {new_bugs}",
                    ]
                )
            )
        )
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
    user = _authenticate_user(db, form_data.username, form_data.password)
    if not user:
        raise HTTPException(status_code=401, detail="Incorrect username or password")

    user.session_token = str(uuid.uuid4())
    db.commit()

    access_token = _create_access_token(
        data={"sub": user.username, "role": user.role.value, "session": user.session_token},
        expires_delta=timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES),
    )
    return Token(access_token=access_token)


@app.get("/auth/me", response_model=UserOut)
def me(current_user: Annotated[User, Depends(get_current_user)]):
    return UserOut(id=current_user.id, username=current_user.username, role=current_user.role.value, is_team_member=current_user.is_team_member)


@app.get("/users", response_model=list[UserOut])
def list_users(
    _: Annotated[User, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
):
    users = db.query(User).order_by(User.id.asc()).all()
    return [UserOut(id=u.id, username=u.username, role=u.role.value, is_team_member=u.is_team_member) for u in users]


@app.post("/users", response_model=UserOut, status_code=201)
def create_user(
    payload: UserCreate,
    _: Annotated[User, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
):
    if db.query(User).filter(User.username == payload.username).first():
        raise HTTPException(status_code=400, detail="Username already exists")
    user = User(
        username=payload.username,
        password_hash=User.hash_password(payload.password),
        role=payload.role,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return UserOut(id=user.id, username=user.username, role=user.role.value, is_team_member=user.is_team_member)


@app.put("/users/{user_id}/role")
def update_user_role(
    user_id: int,
    payload: UserRoleUpdate,
    _: Annotated[User, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    user.role = payload.role
    db.commit()
    return {"message": "User role updated"}


@app.delete("/users/{user_id}")
def delete_user(
    user_id: int,
    current_user: Annotated[User, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
):
    if user_id == current_user.id:
        raise HTTPException(status_code=400, detail="不能删除当前登录用户")
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    db.delete(user)
    db.commit()
    return {"message": "User deleted"}


@app.put("/users/{user_id}/team-status")
def update_user_team_status(
    user_id: int,
    payload: TeamStatusUpdate,
    _: Annotated[User, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    user.is_team_member = payload.is_team_member
    db.commit()
    return {"message": "Team member status updated"}


@app.put("/auth/password")
def change_my_password(payload: PasswordChangeSelf, current_user: Annotated[User, Depends(get_current_user)], db: Session = Depends(get_db)):
    if not current_user.verify_password(payload.old_password):
        raise HTTPException(status_code=400, detail="原密码输入错误")
    if payload.old_password == payload.new_password:
        raise HTTPException(status_code=400, detail="新密码不能与原密码相同")

    current_user.password_hash = User.hash_password(payload.new_password)
    current_user.session_token = None  # 清空会话，强制自己重新登录
    db.commit()
    return {"message": "密码修改成功，请重新登录"}


@app.put("/users/{user_id}/password")
def reset_user_password(user_id: int, payload: PasswordResetAdmin, _: Annotated[User, Depends(require_admin)], db: Session = Depends(get_db)):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    user.password_hash = User.hash_password(payload.new_password)
    user.session_token = None  # 清空会话，强制该用户下线
    db.commit()
    return {"message": f"用户 {user.username} 的密码已重置，且已被强制下线"}


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
    if not R_PATTERN.match(payload.zentao_req_id):
        raise HTTPException(status_code=400, detail="zentao_req_id must be like r#xxxx")
    _ensure_major_version_exists(db, payload.major_version_id)
    if db.query(Requirement).filter(Requirement.zentao_req_id == payload.zentao_req_id).first():
        raise HTTPException(status_code=400, detail="zentao_req_id exists")
    requirement = Requirement(zentao_req_id=payload.zentao_req_id, title=payload.title, major_version_id=payload.major_version_id)
    db.add(requirement)
    db.commit()
    db.refresh(requirement)
    return {"id": requirement.id, "zentao_req_id": requirement.zentao_req_id, "title": requirement.title, "status": requirement.status}


@app.post("/requirements/batch", status_code=201)
def batch_create_requirements(payload: RequirementBatchCreate, _: Annotated[User, Depends(require_admin)], db: Annotated[Session, Depends(get_db)]):
    version = db.query(Version).filter(Version.id == payload.major_version_id, Version.version_type == VersionType.MAJOR).first()
    if not version:
        raise HTTPException(status_code=400, detail="关联的大版本不存在")

    existing_reqs = {r[0] for r in db.query(Requirement.zentao_req_id).all()}

    new_reqs = []
    for item in payload.items:
        if not R_PATTERN.match(item.zentao_req_id) or item.zentao_req_id in existing_reqs:
            continue
        new_reqs.append(
            Requirement(
                zentao_req_id=item.zentao_req_id,
                title=item.title,
                major_version_id=payload.major_version_id,
            )
        )
        existing_reqs.add(item.zentao_req_id)

    if new_reqs:
        db.bulk_save_objects(new_reqs)
        db.commit()

    return {"message": "导入成功", "count": len(new_reqs)}


@app.get("/requirements")
def list_requirements(major_version_id: int = Query(...), db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    rows = db.query(Requirement).options(joinedload(Requirement.owner), joinedload(Requirement.retester), joinedload(Requirement.test_cases)).filter(Requirement.major_version_id == major_version_id).order_by(Requirement.id.asc()).all()
    return [{"id": r.id, "zentao_req_id": r.zentao_req_id, "title": r.title, "owner": r.owner.username if r.owner else None, "owner_id": r.owner_id, "case_completed": r.case_completed, "test_completed": r.test_completed, "retest_completed": r.retest_completed, "retested_by": r.retester.username if r.retester else None, "status": r.status, "case_ids": [c.zentao_case_id for c in r.test_cases]} for r in rows]


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
    req = db.query(Requirement).filter(Requirement.id == req_id).first()
    if not req:
        raise HTTPException(status_code=404, detail="Requirement not found")
    if req.case_completed:
        raise HTTPException(status_code=400, detail="用例已封板（已勾选完成），无法新增！请先取消勾选。")
    if not U_PATTERN.match(payload.zentao_case_id):
        raise HTTPException(status_code=400, detail="zentao_case_id must be like u#xxxx")
    case = TestCase(requirement_id=req_id, zentao_case_id=payload.zentao_case_id, creator_id=current_user.id)
    db.add(case)
    db.commit()
    db.refresh(case)
    return {"id": case.id, "zentao_case_id": case.zentao_case_id}


@app.put("/requirements/{requirement_id}")
def update_requirement(requirement_id: int, payload: RequirementCreate, _: Annotated[User, Depends(require_admin)], db: Session = Depends(get_db)):
    req = db.query(Requirement).filter(Requirement.id == requirement_id).first()
    if not req:
        raise HTTPException(status_code=404, detail="Requirement not found")
    _ensure_major_version_exists(db, payload.major_version_id)
    req.zentao_req_id = payload.zentao_req_id
    req.title = payload.title
    req.major_version_id = payload.major_version_id
    db.commit()
    return {"message": "Requirement updated"}


@app.delete("/requirements/{requirement_id}")
def delete_requirement(requirement_id: int, _: Annotated[User, Depends(require_admin)], db: Session = Depends(get_db)):
    req = db.query(Requirement).filter(Requirement.id == requirement_id).first()
    if not req:
        raise HTTPException(status_code=404, detail="Requirement not found")
    db.delete(req)
    db.commit()
    return {"message": "Requirement deleted"}


@app.put("/test-cases/{case_id}")
def update_case(case_id: int, payload: AddCasePayload, _: Annotated[User, Depends(get_current_user)], db: Session = Depends(get_db)):
    if not U_PATTERN.match(payload.zentao_case_id):
        raise HTTPException(status_code=400, detail="zentao_case_id must be like u#xxxx")
    c = db.query(TestCase).filter(TestCase.id == case_id).first()
    if not c:
        raise HTTPException(status_code=404, detail="Case not found")
        
    req = db.query(Requirement).filter(Requirement.id == c.requirement_id).first()
    if req and req.case_completed:
        raise HTTPException(status_code=400, detail="用例已封板（已勾选完成），无法修改！请先取消勾选。")
        
    c.zentao_case_id = payload.zentao_case_id
    db.commit()
    return {"message": "Case updated"}


@app.delete("/test-cases/{case_id}")
def delete_case(case_id: int, _: Annotated[User, Depends(get_current_user)], db: Session = Depends(get_db)):
    c = db.query(TestCase).filter(TestCase.id == case_id).first()
    if not c:
        raise HTTPException(status_code=404, detail="Case not found")
        
    req = db.query(Requirement).filter(Requirement.id == c.requirement_id).first()
    if req and req.case_completed:
        raise HTTPException(status_code=400, detail="用例已封板（已勾选完成），无法删除！请先取消勾选。")
        
    db.delete(c)
    db.commit()
    return {"message": "Case deleted"}


@app.post("/bugs/execution")
def create_execution_bug(payload: ExecutionBugPayload, current_user: Annotated[User, Depends(get_current_user)], db: Session = Depends(get_db)):
    if not B_PATTERN.match(payload.bug_id):
        raise HTTPException(status_code=400, detail="bug_id must be like b#xxxx")
    if payload.source_type not in [BugSourceType.CASE, BugSourceType.MANUAL, BugSourceType.RETEST]:
        raise HTTPException(status_code=400, detail="source_type only supports case/manual/retest")
    if db.query(BugTracking).filter(BugTracking.bug_id == payload.bug_id).first():
        raise HTTPException(status_code=400, detail=f"添加失败：Bug 编号 {payload.bug_id} 已经存在！")

    req = db.query(Requirement).filter(Requirement.id == payload.requirement_id).first()
    if not req:
        raise HTTPException(status_code=404, detail="Requirement not found")
    if req.test_completed and payload.source_type != BugSourceType.RETEST:
        raise HTTPException(status_code=400, detail="测试已封板（已勾选完成），无法继续添加 Bug！请先取消勾选。")

    bug = BugTracking(
        major_version_id=req.major_version_id,
        requirement_id=payload.requirement_id,
        source_type=payload.source_type,
        source_ref=payload.source_ref,
        bug_id=payload.bug_id,
        found_minor_version_id=payload.minor_version_id,
        created_by_id=current_user.id,
    )
    db.add(bug)
    db.commit()
    db.refresh(bug)
    return {"id": bug.id, "message": "Bug recorded"}


@app.post("/requirements/assign-and-publish")
async def assign_and_publish(payload: AssignPublishPayload, _: Annotated[User, Depends(require_admin)], db: Annotated[Session, Depends(get_db)]):
    req_map = {r.id: r for r in db.query(Requirement).filter(Requirement.major_version_id == payload.major_version_id).all()}
    # 获取所有用户映射，用于在企微消息中精准@对应人员
    users_map = {u.id: u.username for u in db.query(User).all()}

    change_msgs = []

    for item in payload.assignments:
        req = req_map.get(item.requirement_id)
        if req:
            old_owner_id = req.owner_id
            new_owner_id = item.owner_id

            # 如果负责人发生实质性改变
            if old_owner_id != new_owner_id:
                req.owner_id = new_owner_id

                # 核心逻辑：只要发生人员流转，强制打回初始未完成状态，倒逼新负责人重新确认
                req.case_completed = False
                req.test_completed = False

                if new_owner_id:
                    req.status = RequirementStatus.ASSIGNED
                    old_name = users_map.get(old_owner_id, "未分配")
                    new_name = users_map.get(new_owner_id, "未知")

                    # 只有“之前有人处理过”且“现在分配给了另一个人”时，才发送精准移交通知
                    if old_owner_id:
                        change_msgs.append(f"> **{req.zentao_req_id}** ({req.title}) 已从 @{old_name} 移交给了 @{new_name}")
                else:
                    req.status = RequirementStatus.PENDING

    db.commit()

    # 根据是否有人员换防，发送不同的企微播报
    if change_msgs:
        md = "### 🔄 需求负责人变更通知\n" + "\n".join(change_msgs) + "\n\n*提示：移交的需求已自动重置【完成状态】，请新负责人重新校验并打勾。*"
        await _send_wechat_markdown(md)
    else:
        await _send_wechat_markdown("✅ 需求分配状态已整体更新发布")

    return {"message": "Assignments updated"}


@app.patch("/requirements/{requirement_id}/status")
def patch_requirement_status(
    requirement_id: int,
    payload: ReqStatusUpdate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    requirement = db.query(Requirement).filter(Requirement.id == requirement_id).first()
    if not requirement:
        raise HTTPException(status_code=404, detail="Requirement not found")
    if requirement.owner_id and requirement.owner_id != current_user.id and current_user.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="Only owner can update status")

    if payload.case_completed is not None:
        requirement.case_completed = payload.case_completed
    if payload.test_completed is not None:
        requirement.test_completed = payload.test_completed

    if requirement.test_completed:
        requirement.status = RequirementStatus.TEST_DONE
    elif requirement.case_completed:
        requirement.status = RequirementStatus.CASE_DONE
    elif requirement.owner_id:
        requirement.status = RequirementStatus.ASSIGNED
    else:
        requirement.status = RequirementStatus.PENDING

    db.commit()
    return {
        "message": "Requirement status updated",
        "case_completed": requirement.case_completed,
        "test_completed": requirement.test_completed,
        "status": requirement.status,
    }


@app.put("/requirements/{requirement_id}/cases")
def update_requirement_cases(requirement_id: int, payload: CaseUpdatePayload, current_user: Annotated[User, Depends(get_current_user)], db: Annotated[Session, Depends(get_db)]):
    requirement = db.query(Requirement).filter(Requirement.id == requirement_id).first()
    if not requirement:
        raise HTTPException(status_code=404, detail="Requirement not found")
    case_ids = _parse_multiple_ids(payload.case_ids, U_PATTERN, "test case")
    db.query(TestCase).filter(TestCase.requirement_id == requirement_id).delete()
    for cid in case_ids:
        db.add(TestCase(requirement_id=requirement.id, zentao_case_id=cid, creator_id=current_user.id))
    requirement.case_completed = payload.case_completed
    db.commit()
    return {"message": "Cases updated", "case_ids": case_ids}


@app.put("/requirements/{requirement_id}/test-execution")
def upsert_test_execution(requirement_id: int, payload: TestExecutionPayload, current_user: Annotated[User, Depends(get_current_user)], db: Annotated[Session, Depends(get_db)]):
    requirement = db.query(Requirement).filter(Requirement.id == requirement_id).first()
    if not requirement:
        raise HTTPException(status_code=404, detail="Requirement not found")
    execution = db.query(TestExecution).filter(TestExecution.requirement_id == requirement_id, TestExecution.minor_version_id == payload.minor_version_id).first()
    if not execution:
        execution = TestExecution(requirement_id=requirement_id, minor_version_id=payload.minor_version_id)
        db.add(execution)
    execution.bug_id = payload.bug_id
    execution.source_case_id = payload.source_case_id
    execution.result_status = payload.result_status
    execution.notes = payload.notes
    execution.executed_by_id = current_user.id
    execution.executed_at = datetime.utcnow()
    requirement.test_completed = payload.test_completed
    db.commit()
    return {"message": "Test execution updated"}


@app.post("/push/case-progress")
async def push_case_progress(payload: ProgressPushPayload, _: Annotated[User, Depends(get_current_user)], db: Annotated[Session, Depends(get_db)]):
    await _send_wechat_markdown(f"阶段二进度推送：大版本{payload.major_version_id}")
    return {"message": "Case progress pushed"}


@app.post("/push/test-progress")
async def push_test_progress(minor_version_id: int, major_version_id: int, current_user: Annotated[User, Depends(get_current_user)], db: Annotated[Session, Depends(get_db)]):
    other_members = db.query(User).filter(User.id != current_user.id).all()
    mentions = " ".join([f"@{u.username}" for u in other_members])
    await _send_wechat_markdown(f"阶段三进度推送：包{minor_version_id}。以上需求已测试完毕，请其他人前往系统进行交叉复测！{mentions}")
    return {"message": "Test progress pushed"}


@app.get("/retest/workbench")
def get_retest_workbench(major_version_id: int, current_user: Annotated[User, Depends(get_current_user)], db: Annotated[Session, Depends(get_db)]):
    reqs = db.query(Requirement).options(
        joinedload(Requirement.owner),
        joinedload(Requirement.test_cases),
        joinedload(Requirement.retester)
    ).filter(
        Requirement.major_version_id == major_version_id,
        Requirement.test_completed.is_(True),
        Requirement.owner_id.isnot(None),
        Requirement.owner_id != current_user.id
    ).order_by(Requirement.id.asc()).all()

    minors = {v.id: v.version_no for v in db.query(Version).filter(Version.version_type == VersionType.MINOR).all()}
    req_ids = [r.id for r in reqs]
    case_ids = [c.id for r in reqs for c in r.test_cases]

    case_bug_rows = db.query(BugTracking).filter(
        BugTracking.requirement_id.in_(req_ids),
        BugTracking.source_type == BugSourceType.CASE,
        BugTracking.source_ref.in_([str(x) for x in case_ids] if case_ids else ["-1"]),
    ).all() if req_ids else []
    free_bug_rows = db.query(BugTracking).filter(
        BugTracking.requirement_id.in_(req_ids),
        BugTracking.source_type == BugSourceType.MANUAL,
    ).all() if req_ids else []
    retest_bug_rows = db.query(BugTracking).filter(
        BugTracking.requirement_id.in_(req_ids),
        BugTracking.source_type == BugSourceType.RETEST,
    ).all() if req_ids else []

    case_bug_map: dict[str, list[dict]] = {}
    for b in case_bug_rows:
        case_bug_map.setdefault(b.source_ref or "", []).append({
            "id": b.id,
            "bug_id": b.bug_id,
            "found_minor_version_no": minors.get(b.found_minor_version_id, "未知"),
            "is_retest_failed": b.is_retest_failed,
        })

    free_bug_map: dict[int, list[dict]] = {}
    for b in free_bug_rows:
        free_bug_map.setdefault(b.requirement_id or -1, []).append({
            "id": b.id,
            "bug_id": b.bug_id,
            "found_minor_version_no": minors.get(b.found_minor_version_id, "未知"),
            "is_retest_failed": b.is_retest_failed,
        })

    retest_bug_map: dict[int, list[dict]] = {}
    for b in retest_bug_rows:
        retest_bug_map.setdefault(b.requirement_id or -1, []).append({
            "id": b.id,
            "bug_id": b.bug_id,
            "found_minor_version_no": minors.get(b.found_minor_version_id, "未知"),
            "is_retest_failed": b.is_retest_failed,
        })

    return [
        {
            "id": r.id,
            "zentao_req_id": r.zentao_req_id,
            "title": r.title,
            "owner": r.owner.username if r.owner else None,
            "retest_completed": r.retest_completed,
            "retest_passed": r.retest_passed,
            "retest_minor_version_id": r.retest_minor_version_id,
            "retested_by": r.retester.username if r.retester else None,
            "test_cases": [
                {"id": c.id, "zentao_case_id": c.zentao_case_id, "bugs": case_bug_map.get(str(c.id), [])}
                for c in r.test_cases
            ],
            "free_bugs": free_bug_map.get(r.id, []),
            "retest_bugs": retest_bug_map.get(r.id, []),
        }
        for r in reqs
    ]

@app.put("/requirements/{requirement_id}/retest")
def submit_retest(requirement_id: int, payload: RetestPayload, current_user: Annotated[User, Depends(get_current_user)], db: Annotated[Session, Depends(get_db)]):
    req = db.query(Requirement).filter(Requirement.id == requirement_id).first()
    if not req:
        raise HTTPException(status_code=404, detail="Requirement not found")
    if req.owner_id == current_user.id:
        raise HTTPException(status_code=403, detail="Self-tested requirement cannot be cross-retested by self")
        
    # 🚨 核心拦截逻辑：打回操作必须要有“呈堂证供”
    if payload.retest_completed and payload.retest_passed is False:
        from app.models import BugTracking, BugSourceType
        # 证据 1：有没有标记未修好的旧 Bug
        has_failed_old = db.query(BugTracking).filter(BugTracking.requirement_id == requirement_id, BugTracking.is_retest_failed.is_(True)).first()
        # 证据 2：有没有新增的漏测 Bug
        has_new_retest = db.query(BugTracking).filter(BugTracking.requirement_id == requirement_id, BugTracking.source_type == BugSourceType.RETEST).first()
        
        if not has_failed_old and not has_new_retest:
            raise HTTPException(status_code=400, detail="打回无效：请至少勾选一个未修好的旧 Bug，或新增一个漏测 Bug 作为证据！")

    req.retest_completed = payload.retest_completed
    req.retest_passed = payload.retest_passed if payload.retest_completed else None
    req.retest_minor_version_id = payload.retest_minor_version_id if payload.retest_completed else None
    req.retested_by_id = current_user.id if payload.retest_completed else None
    req.retested_at = datetime.utcnow() if payload.retest_completed else None
    db.commit()
    return {"message": "Retest status updated"}

@app.post("/push/retest-result")
async def push_retest_result(major_version_id: int, current_user: Annotated[User, Depends(get_current_user)], db: Annotated[Session, Depends(get_db)]):
    rows = db.query(Requirement).options(joinedload(Requirement.owner), joinedload(Requirement.retest_minor_version)).filter(Requirement.major_version_id == major_version_id, Requirement.retest_completed.is_(True), Requirement.retested_by_id == current_user.id).all()
    if not rows:
        raise HTTPException(status_code=400, detail="No retested requirements by current user")

    msg_lines = [f"### 📢 复测结果专项通报 (复测人: @{current_user.username})"]
    for r in rows:
        owner_name = r.owner.username if r.owner else "未知"
        minor_ver = r.retest_minor_version.version_no if r.retest_minor_version else "未知"
        if r.retest_passed:
            msg_lines.append(f"> ✅ **[通过]** {r.zentao_req_id} (原测试: @{owner_name} | 验证发包: {minor_ver})")
        else:
            msg_lines.append(f"> ❌ **[打回]** <font color=\"warning\">{r.zentao_req_id}</font> (原测试: @{owner_name} | 验证发包: {minor_ver}) - *存在漏测或未修复问题！*")

    await _send_wechat_markdown("\n".join(msg_lines))
    return {"message": "Retest results pushed", "count": len(rows)}

@app.get("/stage5/overview")
def stage5_overview(major_version_id: int, current_user: Annotated[User, Depends(get_current_user)], db: Annotated[Session, Depends(get_db)]):
    reqs = db.query(Requirement).options(joinedload(Requirement.test_cases), joinedload(Requirement.test_executions)).filter(Requirement.major_version_id == major_version_id).all()
    bugs = db.query(BugTracking).options(
        joinedload(BugTracking.stage5_records).joinedload(BugStage5Record.user),
        joinedload(BugTracking.stage5_records).joinedload(BugStage5Record.minor_version),
        joinedload(BugTracking.dispatched_to),
    ).filter(BugTracking.major_version_id == major_version_id).all()

    bug_pool = []
    for b in bugs:
        other_records = []
        my_record = None
        for r in b.stage5_records:
            if r.user_id != current_user.id:
                other_records.append({
                    "username": r.user.username,
                    "minor_version_no": r.minor_version.version_no if r.minor_version else "未知",
                    "test_done": r.test_done,
                    "resolution": r.resolution
                })
            else:
                my_record = r

        bug_pool.append({
            "id": b.id, "bug_id": b.bug_id, "source_type": b.source_type.value, "source_ref": b.source_ref,
            "requirement_id": b.requirement_id, "found_minor_version_id": b.found_minor_version_id, "fixed_minor_version_id": b.fixed_minor_version_id,
            "closed": b.closed,  # 保留全局闭环状态用于全景进度条统计
            "my_test_done": my_record.test_done if my_record else False,
            "my_resolution": my_record.resolution if my_record else "fixed",
            "other_records": other_records,
            "dispatched_to_name": b.dispatched_to.username if b.dispatched_to else None,
            "is_retest_failed": getattr(b, 'is_retest_failed', False)
        })
    return {
        "major_version_id": major_version_id,
        "requirements": [{
            "id": r.id,
            "zentao_req_id": r.zentao_req_id,
            "title": r.title,
            "case_ids": [c.zentao_case_id for c in r.test_cases],
            "history_bug_ids": [e.bug_id for e in r.test_executions if e.bug_id],
        } for r in reqs],
        "bug_pool": bug_pool,
    }


@app.get("/stage5/search-options")
def get_stage5_search_options(major_version_id: int, _: Annotated[User, Depends(get_current_user)], db: Session = Depends(get_db)):
    # 抓取当前版本下的可选项用于前端模糊搜索
    reqs = db.query(Requirement).filter(Requirement.major_version_id == major_version_id).all()
    cases = db.query(TestCase).join(Requirement).filter(Requirement.major_version_id == major_version_id).all()
    legacy_bugs = db.query(BugTracking).filter(BugTracking.major_version_id == major_version_id).all()

    return {
        "reqs": [{"id": r.id, "label": f"{r.zentao_req_id} {r.title}"} for r in reqs],
        "cases": [{"id": c.id, "req_id": c.requirement_id, "label": c.zentao_case_id} for c in cases],
        "bugs": [{"id": b.id, "req_id": b.requirement_id, "label": b.bug_id} for b in legacy_bugs],
    }


@app.put("/stage5/bugs/{bug_track_id}/result")
async def submit_stage5_result(bug_track_id: int, payload: Stage5ResultPayload, current_user: Annotated[User, Depends(get_current_user)], db: Annotated[Session, Depends(get_db)]):
    bug = db.query(BugTracking).filter(BugTracking.id == bug_track_id).first()
    if not bug:
        raise HTTPException(status_code=404, detail="Bug tracking item not found")

    old_resolution = bug.resolution

    # 1. 保存个人独立闭环业绩 (UPSERT)
    from app.models import BugStage5Record
    record = db.query(BugStage5Record).filter(BugStage5Record.bug_tracking_id == bug_track_id, BugStage5Record.user_id == current_user.id).first()
    if not record:
        record = BugStage5Record(bug_tracking_id=bug_track_id, user_id=current_user.id)
        db.add(record)
    record.minor_version_id = payload.minor_version_id
    record.test_done = payload.test_done
    record.newly_found_bug_id = payload.newly_found_bug_id
    record.resolution = payload.resolution
    record.updated_at = datetime.utcnow()
    db.commit()  # 先提交个人记录

    # 2. 重新计算全局状态：只要有一个人确认了闭环，全局即视为闭环（用于统计进度条）
    all_records = db.query(BugStage5Record).filter(BugStage5Record.bug_tracking_id == bug_track_id).all()
    any_closed = any(r.test_done for r in all_records)

    bug.resolution = payload.resolution
    bug.fixed_minor_version_id = payload.minor_version_id if any_closed else None
    bug.closed = any_closed
    bug.closed_by_id = current_user.id if any_closed else None

    # 企微消息广播逻辑
    if payload.test_done and old_resolution != payload.resolution:
        res_zh_map = {"fixed": "✅修复通过", "false_alarm": "⚠️误报", "rejected": "⛔拒绝修复"}
        if payload.resolution in ["false_alarm", "rejected"] or old_resolution in ["false_alarm", "rejected"]:
            await _send_wechat_markdown(f"📢 **Bug 状态流转通知**\n> 缺陷 **{bug.bug_id}** 的处理状态被 @{current_user.username} 更新为：**{res_zh_map.get(payload.resolution, payload.resolution)}** (位于发包: 🏷️{payload.minor_version_id})")

    # 3. 兼容逗号分割的多 Bug 字符串自动连带创建 (特派区逻辑)
    if payload.newly_found_bug_id:
        from app.models import BugSourceType
        new_bugs = [b.strip() for b in payload.newly_found_bug_id.split(",") if b.strip()]
        for nb in new_bugs:
            if not db.query(BugTracking).filter(BugTracking.bug_id == nb).first():
                db.add(BugTracking(
                    major_version_id=bug.major_version_id, requirement_id=bug.requirement_id,
                    source_type=BugSourceType.LEGACY_BUG, source_ref=bug.bug_id, bug_id=nb,
                    found_minor_version_id=payload.minor_version_id, created_by_id=current_user.id, dispatched_to_id=current_user.id
                ))

    db.commit()
    return {"message": "Stage5 result updated"}

@app.post("/stage5/issues")
async def add_stage5_issue(payload: Stage5IssueCreatePayload, current_user: Annotated[User, Depends(get_current_user)], db: Annotated[Session, Depends(get_db)]):
    if not B_PATTERN.match(payload.bug_id):
        raise HTTPException(status_code=400, detail="bug_id must be like b#xxxx")
    if db.query(BugTracking).filter(BugTracking.bug_id == payload.bug_id).first():
        raise HTTPException(status_code=400, detail=f"添加失败：Bug 编号 {payload.bug_id} 已经存在！")
    item = BugTracking(
        major_version_id=payload.major_version_id,
        requirement_id=payload.requirement_id,
        source_type=payload.source_type,
        source_ref=payload.source_ref,
        bug_id=payload.bug_id,
        found_minor_version_id=payload.minor_version_id,
        created_by_id=current_user.id,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return {"id": item.id, "message": "Issue added"}


@app.post("/stage5/push-status")
async def push_stage5_status(major_version_id: int, minor_version_id: int, _: Annotated[User, Depends(get_current_user)], db: Annotated[Session, Depends(get_db)]):
    bugs = db.query(BugTracking).filter(BugTracking.major_version_id == major_version_id).all()
    remaining = len([b for b in bugs if not (b.closed and b.fixed_minor_version_id == minor_version_id)])
    await _send_wechat_markdown(f"阶段五进度推送：剩余未闭环 {remaining}")
    return {"message": "Stage5 status pushed", "remaining": remaining}


@app.get("/reports/summary")
def reports_summary(
    start_date: date,
    end_date: date,
    user_id: Optional[int] = None,
    current_user: Annotated[User, Depends(get_current_user)] = None,
    db: Session = Depends(get_db),
):
    all_users_mode = user_id in (None, 0)
    if all_users_mode and current_user.role != UserRole.ADMIN:
        target_user_id = current_user.id
        all_users_mode = False
    else:
        target_user_id = current_user.id if user_id is None else user_id

    if (not all_users_mode) and target_user_id != current_user.id and current_user.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="无权限查看其他人的报表")

    sdt = datetime.combine(start_date, datetime.min.time())
    edt = datetime.combine(end_date, datetime.max.time())

    team_ids = []
    if all_users_mode:
        team_ids = [u.id for u in db.query(User).filter(User.is_team_member.is_(True)).all()]

    def metrics_for_user(uid: int) -> dict:
        executed_req_count = (
            db.query(func.count(func.distinct(TestExecution.requirement_id)))
            .filter(TestExecution.executed_by_id == uid, TestExecution.executed_at >= sdt, TestExecution.executed_at <= edt)
            .scalar()
            or 0
        )
        case_count = (
            db.query(func.count(TestCase.id))
            .filter(TestCase.creator_id == uid, TestCase.created_at >= sdt, TestCase.created_at <= edt)
            .scalar()
            or 0
        )
        bug_count = (
            db.query(func.count(BugTracking.id))
            .filter(BugTracking.created_by_id == uid, BugTracking.created_at >= sdt, BugTracking.created_at <= edt)
            .scalar()
            or 0
        )
        retested_reqs = (
            db.query(func.count(Requirement.id))
            .filter(Requirement.retested_by_id == uid, Requirement.retested_at >= sdt, Requirement.retested_at <= edt)
            .scalar()
            or 0
        )
        closed_bugs = (
            db.query(func.count(func.distinct(BugStage5Record.bug_tracking_id)))
            .filter(
                BugStage5Record.user_id == uid,
                BugStage5Record.updated_at >= sdt,
                BugStage5Record.updated_at <= edt,
                BugStage5Record.test_done.is_(True),
                BugStage5Record.newly_found_bug_id.is_(None),
            )
            .scalar()
            or 0
        )
        return {
            "executed_requirements": executed_req_count,
            "created_cases": case_count,
            "created_bugs": bug_count,
            "retested_reqs": retested_reqs,
            "closed_bugs": closed_bugs,
        }

    if all_users_mode:
        overview = {
            "executed_requirements": (
                db.query(func.count(func.distinct(TestExecution.requirement_id)))
                .filter(TestExecution.executed_at >= sdt, TestExecution.executed_at <= edt, TestExecution.executed_by_id.in_(team_ids))
                .scalar()
                or 0
            ),
            "created_cases": (
                db.query(func.count(TestCase.id))
                .filter(TestCase.created_at >= sdt, TestCase.created_at <= edt, TestCase.creator_id.in_(team_ids))
                .scalar()
                or 0
            ),
            "created_bugs": (
                db.query(func.count(BugTracking.id))
                .filter(BugTracking.created_at >= sdt, BugTracking.created_at <= edt, BugTracking.created_by_id.in_(team_ids))
                .scalar()
                or 0
            ),
            "retested_reqs": (
                db.query(func.count(Requirement.id))
                .filter(Requirement.retested_at >= sdt, Requirement.retested_at <= edt, Requirement.retested_by_id.in_(team_ids))
                .scalar()
                or 0
            ),
            "closed_bugs": (
                db.query(func.count(func.distinct(BugStage5Record.bug_tracking_id)))
                .filter(
                    BugStage5Record.updated_at >= sdt,
                    BugStage5Record.updated_at <= edt,
                    BugStage5Record.user_id.in_(team_ids),
                    BugStage5Record.test_done.is_(True),
                    BugStage5Record.newly_found_bug_id.is_(None),
                )
                .scalar()
                or 0
            ),
        }
    else:
        overview = metrics_for_user(target_user_id)

    trend = []
    cur = start_date
    while cur <= end_date:
        day_s = datetime.combine(cur, datetime.min.time())
        day_e = datetime.combine(cur, datetime.max.time())

        if all_users_mode:
            day_exec = (
                db.query(func.count(func.distinct(TestExecution.requirement_id)))
                .filter(TestExecution.executed_at >= day_s, TestExecution.executed_at <= day_e, TestExecution.executed_by_id.in_(team_ids))
                .scalar()
                or 0
            )
            day_case = (
                db.query(func.count(TestCase.id))
                .filter(TestCase.created_at >= day_s, TestCase.created_at <= day_e, TestCase.creator_id.in_(team_ids))
                .scalar()
                or 0
            )
            day_bug = (
                db.query(func.count(BugTracking.id))
                .filter(BugTracking.created_at >= day_s, BugTracking.created_at <= day_e, BugTracking.created_by_id.in_(team_ids))
                .scalar()
                or 0
            )
            day_retested = (
                db.query(func.count(Requirement.id))
                .filter(Requirement.retested_at >= day_s, Requirement.retested_at <= day_e, Requirement.retested_by_id.in_(team_ids))
                .scalar()
                or 0
            )
            day_closed = (
                db.query(func.count(func.distinct(BugStage5Record.bug_tracking_id)))
                .filter(
                    BugStage5Record.updated_at >= day_s,
                    BugStage5Record.updated_at <= day_e,
                    BugStage5Record.user_id.in_(team_ids),
                    BugStage5Record.test_done.is_(True),
                    BugStage5Record.newly_found_bug_id.is_(None),
                )
                .scalar()
                or 0
            )
        else:
            day_exec = (
                db.query(func.count(func.distinct(TestExecution.requirement_id)))
                .filter(TestExecution.executed_by_id == target_user_id, TestExecution.executed_at >= day_s, TestExecution.executed_at <= day_e)
                .scalar()
                or 0
            )
            day_case = (
                db.query(func.count(TestCase.id))
                .filter(TestCase.creator_id == target_user_id, TestCase.created_at >= day_s, TestCase.created_at <= day_e)
                .scalar()
                or 0
            )
            day_bug = (
                db.query(func.count(BugTracking.id))
                .filter(BugTracking.created_by_id == target_user_id, BugTracking.created_at >= day_s, BugTracking.created_at <= day_e)
                .scalar()
                or 0
            )
            day_retested = (
                db.query(func.count(Requirement.id))
                .filter(Requirement.retested_by_id == target_user_id, Requirement.retested_at >= day_s, Requirement.retested_at <= day_e)
                .scalar()
                or 0
            )
            day_closed = (
                db.query(func.count(func.distinct(BugStage5Record.bug_tracking_id)))
                .filter(
                    BugStage5Record.user_id == target_user_id,
                    BugStage5Record.updated_at >= day_s,
                    BugStage5Record.updated_at <= day_e,
                    BugStage5Record.test_done.is_(True),
                    BugStage5Record.newly_found_bug_id.is_(None),
                )
                .scalar()
                or 0
            )

        trend.append({
            "date": cur.isoformat(),
            "executed_requirements": day_exec,
            "created_cases": day_case,
            "created_bugs": day_bug,
            "retested_reqs": day_retested,
            "closed_bugs": day_closed,
        })
        cur += timedelta(days=1)

    bug_dist_query = db.query(BugTracking.source_type, func.count(BugTracking.id)).filter(BugTracking.created_at >= sdt, BugTracking.created_at <= edt)
    if all_users_mode:
        bug_dist_query = bug_dist_query.filter(BugTracking.created_by_id.in_(team_ids))
    else:
        bug_dist_query = bug_dist_query.filter(BugTracking.created_by_id == target_user_id)
    bug_source_dist = [{"source_type": (k.value if hasattr(k, "value") else str(k)), "count": v} for k, v in bug_dist_query.group_by(BugTracking.source_type).all()]

    result = {
        "overview": overview,
        "trend": trend,
        "bug_source_dist": bug_source_dist,
        "target_user_id": None if all_users_mode else target_user_id,
    }

    if all_users_mode:
        team = []
        all_users = db.query(User).filter(User.is_team_member.is_(True)).order_by(User.id.asc()).all()
        for u in all_users:
            m = metrics_for_user(u.id)
            team.append({"user_id": u.id, "username": u.username, **m})
        result["team_comparison"] = team

    return result


@app.get("/reports/version-bugs")
def reports_version_bugs(_: Annotated[User, Depends(get_current_user)], db: Session = Depends(get_db)):
    # 获取所有小版本
    minor_versions = db.query(Version).filter(Version.version_type == VersionType.MINOR).all()
    
    result = []
    for mv in minor_versions:
        # 统计挂在这个小版本上的 Bug 数量
        bug_count = db.query(BugTracking).filter(BugTracking.found_minor_version_id == mv.id).count()
        if bug_count > 0:
            parent = db.query(Version).filter(Version.id == mv.parent_id).first()
            parent_name = parent.version_no if parent else "未知大版本"
            # 拼接展示名称，如：V4030(换行)V4030.1
            display_name = f"{parent_name}\n{mv.version_no}"
            result.append({"version_name": display_name, "bug_count": bug_count})
            
    return result


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
def update_bug(bug_id: int, new_bug_id: str, _: Annotated[User, Depends(get_current_user)], db: Session = Depends(get_db)):
    if not B_PATTERN.match(new_bug_id):
        raise HTTPException(status_code=400, detail="Invalid bug format")
        
    # 查重：新编号是否已被其他Bug占用
    existing = db.query(BugTracking).filter(BugTracking.bug_id == new_bug_id).first()
    if existing and existing.id != bug_id:
        raise HTTPException(status_code=400, detail=f"修改失败：Bug 编号 {new_bug_id} 已存在！")
        
    bug = db.query(BugTracking).filter(BugTracking.id == bug_id).first()
    if not bug:
        raise HTTPException(status_code=404, detail="Bug not found")
    bug.bug_id = new_bug_id
    db.commit()
    return {"message": "Bug updated"}


@app.delete("/bugs/{bug_id}")
def delete_bug(bug_id: int, _: Annotated[User, Depends(get_current_user)], db: Session = Depends(get_db)):
    bug = db.query(BugTracking).filter(BugTracking.id == bug_id).first()
    if not bug:
        raise HTTPException(status_code=404, detail="Bug not found")
    db.delete(bug)
    db.commit()
    return {"message": "Bug deleted"}


@app.patch("/bugs/{bug_id}/retest-fail")
def toggle_bug_retest_fail(bug_id: int, payload: BugRetestFailPayload, _: Annotated[User, Depends(get_current_user)], db: Session = Depends(get_db)):
    bug = db.query(BugTracking).filter(BugTracking.id == bug_id).first()
    if not bug:
        raise HTTPException(status_code=404, detail="Bug not found")
    bug.is_retest_failed = payload.is_retest_failed
    db.commit()
    return {"message": "Bug retest status updated"}


@app.get("/bugs/search")
def search_bug(bug_id: str, _: Annotated[User, Depends(require_admin)], db: Session = Depends(get_db)):
    b = db.query(BugTracking).options(joinedload(BugTracking.requirement)).filter(BugTracking.bug_id == bug_id).first()
    if not b:
        raise HTTPException(status_code=404, detail="未找到该 Bug 编号")
    return {
        "id": b.id,
        "bug_id": b.bug_id,
        "req_title": b.requirement.title if b.requirement else "无关联需求 / 自由Bug",
        "dispatched_to_id": b.dispatched_to_id,
    }


@app.post("/bugs/{bug_id}/dispatch")
async def dispatch_bug(bug_id: int, payload: DispatchPayload, _: Annotated[User, Depends(require_admin)], db: Annotated[Session, Depends(get_db)]):
    b = db.query(BugTracking).filter(BugTracking.id == bug_id).first()
    if not b:
        raise HTTPException(status_code=404, detail="Bug not found")
    b.dispatched_to_id = payload.user_id
    db.commit()
    u = db.query(User).filter(User.id == payload.user_id).first()
    if u:
        await _send_wechat_markdown(f"📢 **Bug 特派专项通知**\n> 缺陷 **{b.bug_id}** 已被管理员特派给 @{u.username} 进行专项验证！请前往【我的工作台】顶部处理。")
    return {"message": "特派成功"}


@app.get("/bugs/dispatched-to-me")
def dispatched_to_me(major_version_id: int, current_user: Annotated[User, Depends(get_current_user)], db: Session = Depends(get_db)):
    bugs = db.query(BugTracking).options(joinedload(BugTracking.requirement)).filter(BugTracking.major_version_id == major_version_id, BugTracking.dispatched_to_id == current_user.id).all()
    records = db.query(BugStage5Record).filter(BugStage5Record.user_id == current_user.id).all()
    rmap = {r.bug_tracking_id: r for r in records}
    res = []
    for b in bugs:
        r = rmap.get(b.id)
        res.append({
            "id": b.id,
            "bug_id": b.bug_id,
            "source_type": b.source_type.value,
            "req_title": b.requirement.title if b.requirement else "无关联需求 / 自由Bug",
            "test_done": r.test_done if r else False,
            "resolution": r.resolution if r else "fixed",
            "newly_found_bug_id": r.newly_found_bug_id if r else "",
        })
    return res


@app.get("/bugs/dispatched-all")
def get_all_dispatched_bugs(_: Annotated[User, Depends(require_admin)], db: Session = Depends(get_db)):
    bugs = db.query(BugTracking).options(joinedload(BugTracking.dispatched_to)).filter(BugTracking.dispatched_to_id.isnot(None)).order_by(BugTracking.id.desc()).all()
    return [{
        "id": b.id,
        "bug_id": b.bug_id,
        "dispatched_to_name": b.dispatched_to.username if b.dispatched_to else "未知",
        "closed": b.closed,
        "resolution": b.resolution
    } for b in bugs]


@app.get("/export")
def export_data(format: str = Query("csv", pattern="^(csv|xlsx)$"), major_version_id: Optional[int] = Query(default=None), minor_version_id: Optional[int] = Query(default=None), _: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = _build_export_rows(db, major_version_id=major_version_id, minor_version_id=minor_version_id)
    if not rows:
        raise HTTPException(status_code=404, detail="No data for export")

    fields = ["requirement_id", "major_version", "zentao_req_id", "title", "owner", "case_ids", "case_completed", "test_completed", "status", "minor_version", "result_status", "bug_id", "source_case_id", "executed_at"]

    tmp_dir = Path(tempfile.gettempdir())
    stamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    if format == "csv":
        out_path = tmp_dir / f"appauto_export_{stamp}.csv"
        with out_path.open("w", newline="", encoding="utf-8-sig") as f:
            writer = DictWriter(f, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
        media_type = "text/csv"
    else:
        out_path = tmp_dir / f"appauto_export_{stamp}.xlsx"
        wb = Workbook()
        ws = wb.active
        ws.title = "export"
        ws.append(fields)
        for r in rows:
            ws.append([r.get(k, "") for k in fields])
        wb.save(out_path)
        media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

    return FileResponse(path=str(out_path), filename=out_path.name, media_type=media_type)
