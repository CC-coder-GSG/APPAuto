from __future__ import annotations

import os
import re
import tempfile
from csv import DictWriter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated, Optional

import httpx
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import Depends, FastAPI, HTTPException, Query, status
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from jose import JWTError, jwt
from openpyxl import Workbook
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session, joinedload

from app.database import SessionLocal, get_db
from app.init_db import init_db
from app.models import (
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
app = FastAPI(title="APPAuto", version="0.2.0")
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


class UserCreate(BaseModel):
    username: str = Field(min_length=3, max_length=50)
    password: str = Field(min_length=3, max_length=128)


class VersionCreate(BaseModel):
    version_no: str
    version_type: VersionType
    parent_id: Optional[int] = None


class RequirementCreate(BaseModel):
    zentao_req_id: str
    title: str
    major_version_id: int


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


class Stage5ResultPayload(BaseModel):
    minor_version_id: int
    test_done: bool
    newly_found_bug_id: Optional[str] = None


class Stage5IssueCreatePayload(BaseModel):
    major_version_id: int
    requirement_id: Optional[int] = None
    source_type: BugSourceType = BugSourceType.MANUAL
    source_ref: Optional[str] = None
    bug_id: str
    minor_version_id: Optional[int] = None


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


async def _send_wechat_markdown(markdown: str) -> None:
    if not WECHAT_WEBHOOK_URL:
        return
    payload = {"msgtype": "markdown", "markdown": {"content": markdown}}
    async with httpx.AsyncClient(timeout=10) as client:
        await client.post(WECHAT_WEBHOOK_URL, json=payload)


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
            db.query(TestExecution)
            .filter(TestExecution.bug_id.isnot(None))
            .filter(TestExecution.executed_at >= datetime.combine(today, datetime.min.time()))
            .count()
        )
        content = "\n".join(
            [
                "## 每日18:00测试进度播报",
                f"- 总需求数: {total}",
                f"- 已测数: {tested}",
                f"- 未测数: {untested}",
                f"- 当日新增 b# Bug 数: {new_bugs}",
            ]
        )
        import asyncio

        asyncio.run(_send_wechat_markdown(content))
    finally:
        db.close()


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
        if username is None:
            raise credential_exception
    except JWTError as exc:
        raise credential_exception from exc

    user = db.query(User).filter(User.username == username).first()
    if not user:
        raise credential_exception
    return user


def require_admin(current_user: Annotated[User, Depends(get_current_user)]) -> User:
    if current_user.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="Admin only")
    return current_user


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

    access_token = _create_access_token(
        data={"sub": user.username, "role": user.role.value},
        expires_delta=timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES),
    )
    return Token(access_token=access_token)


@app.get("/auth/me", response_model=UserOut)
def me(current_user: Annotated[User, Depends(get_current_user)]):
    return UserOut(id=current_user.id, username=current_user.username, role=current_user.role.value)


@app.get("/users", response_model=list[UserOut])
def list_users(
    _: Annotated[User, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
):
    users = db.query(User).order_by(User.id.asc()).all()
    return [UserOut(id=u.id, username=u.username, role=u.role.value) for u in users]


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
        role=UserRole.USER,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return UserOut(id=user.id, username=user.username, role=user.role.value)


@app.post("/versions", status_code=201)
def create_version(
    payload: VersionCreate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    if payload.version_type == VersionType.MINOR and not payload.parent_id:
        raise HTTPException(status_code=400, detail="Minor version must specify parent major version")
    if payload.version_type == VersionType.MAJOR and payload.parent_id is not None:
        raise HTTPException(status_code=400, detail="Major version cannot have a parent")

    if db.query(Version).filter(
        Version.version_no == payload.version_no,
        Version.version_type == payload.version_type,
    ).first():
        raise HTTPException(status_code=400, detail="Version already exists")

    if payload.parent_id:
        parent = db.query(Version).filter(Version.id == payload.parent_id).first()
        if not parent or parent.version_type != VersionType.MAJOR:
            raise HTTPException(status_code=400, detail="Invalid parent major version")

    version = Version(
        version_no=payload.version_no,
        version_type=payload.version_type,
        parent_id=payload.parent_id,
    )
    db.add(version)
    db.commit()
    db.refresh(version)
    return {
        "id": version.id,
        "version_no": version.version_no,
        "version_type": version.version_type,
        "parent_id": version.parent_id,
        "created_by": current_user.username,
    }


@app.get("/versions")
def list_versions(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    _ = current_user
    rows = db.query(Version).order_by(Version.created_at.desc()).all()
    return [
        {
            "id": v.id,
            "version_no": v.version_no,
            "version_type": v.version_type,
            "parent_id": v.parent_id,
        }
        for v in rows
    ]


@app.post("/requirements", status_code=201)
def create_requirement(
    payload: RequirementCreate,
    _: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    if not R_PATTERN.match(payload.zentao_req_id):
        raise HTTPException(status_code=400, detail="zentao_req_id must be like r#xxxx")
    major = db.query(Version).filter(Version.id == payload.major_version_id, Version.version_type == VersionType.MAJOR).first()
    if not major:
        raise HTTPException(status_code=400, detail="major version not found")

    existing = db.query(Requirement).filter(Requirement.zentao_req_id == payload.zentao_req_id).first()
    if existing:
        raise HTTPException(status_code=400, detail="zentao_req_id exists")

    requirement = Requirement(
        zentao_req_id=payload.zentao_req_id,
        title=payload.title,
        major_version_id=payload.major_version_id,
    )
    db.add(requirement)
    db.commit()
    db.refresh(requirement)
    return {
        "id": requirement.id,
        "zentao_req_id": requirement.zentao_req_id,
        "title": requirement.title,
        "status": requirement.status,
    }


@app.get("/requirements")
def list_requirements(
    major_version_id: int = Query(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _ = current_user
    rows = (
        db.query(Requirement)
        .options(joinedload(Requirement.owner), joinedload(Requirement.retester), joinedload(Requirement.test_cases))
        .filter(Requirement.major_version_id == major_version_id)
        .order_by(Requirement.id.asc())
        .all()
    )
    return [
        {
            "id": r.id,
            "zentao_req_id": r.zentao_req_id,
            "title": r.title,
            "owner": r.owner.username if r.owner else None,
            "owner_id": r.owner_id,
            "case_completed": r.case_completed,
            "test_completed": r.test_completed,
            "retest_completed": r.retest_completed,
            "retested_by": r.retester.username if r.retester else None,
            "status": r.status,
            "case_ids": [c.zentao_case_id for c in r.test_cases],
        }
        for r in rows
    ]




@app.get("/requirements/my-workbench")
def my_workbench(
    major_version_id: int = Query(...),
    current_user: Annotated[User, Depends(get_current_user)] = None,
    db: Session = Depends(get_db),
):
    rows = (
        db.query(Requirement)
        .options(joinedload(Requirement.test_cases))
        .filter(Requirement.major_version_id == major_version_id, Requirement.owner_id == current_user.id)
        .order_by(Requirement.id.asc())
        .all()
    )
    return [
        {
            "id": r.id,
            "zentao_req_id": r.zentao_req_id,
            "title": r.title,
            "case_completed": r.case_completed,
            "test_completed": r.test_completed,
            "case_ids": [c.zentao_case_id for c in r.test_cases],
        }
        for r in rows
    ]


@app.put("/requirements/{requirement_id}")
def update_requirement(
    requirement_id: int,
    payload: RequirementCreate,
    _: Annotated[User, Depends(require_admin)],
    db: Session = Depends(get_db),
):
    req = db.query(Requirement).filter(Requirement.id == requirement_id).first()
    if not req:
        raise HTTPException(status_code=404, detail="Requirement not found")
    if not R_PATTERN.match(payload.zentao_req_id):
        raise HTTPException(status_code=400, detail="zentao_req_id must be like r#xxxx")
    dup = db.query(Requirement).filter(Requirement.zentao_req_id == payload.zentao_req_id, Requirement.id != requirement_id).first()
    if dup:
        raise HTTPException(status_code=400, detail="zentao_req_id exists")
    req.zentao_req_id = payload.zentao_req_id
    req.title = payload.title
    req.major_version_id = payload.major_version_id
    db.commit()
    return {"message": "Requirement updated"}


@app.delete("/requirements/{requirement_id}")
def delete_requirement(
    requirement_id: int,
    _: Annotated[User, Depends(require_admin)],
    db: Session = Depends(get_db),
):
    req = db.query(Requirement).filter(Requirement.id == requirement_id).first()
    if not req:
        raise HTTPException(status_code=404, detail="Requirement not found")
    db.delete(req)
    db.commit()
    return {"message": "Requirement deleted"}


@app.put("/test-cases/{case_id}")
def update_case(
    case_id: int,
    zentao_case_id: str,
    _: Annotated[User, Depends(require_admin)],
    db: Session = Depends(get_db),
):
    if not U_PATTERN.match(zentao_case_id):
        raise HTTPException(status_code=400, detail="Invalid case format")
    c = db.query(TestCase).filter(TestCase.id == case_id).first()
    if not c:
        raise HTTPException(status_code=404, detail="Case not found")
    c.zentao_case_id = zentao_case_id
    db.commit()
    return {"message": "Case updated"}


@app.delete("/test-cases/{case_id}")
def delete_case(
    case_id: int,
    _: Annotated[User, Depends(require_admin)],
    db: Session = Depends(get_db),
):
    c = db.query(TestCase).filter(TestCase.id == case_id).first()
    if not c:
        raise HTTPException(status_code=404, detail="Case not found")
    db.delete(c)
    db.commit()
    return {"message": "Case deleted"}


@app.put("/versions/{version_id}")
def update_version(
    version_id: int,
    payload: VersionCreate,
    _: Annotated[User, Depends(require_admin)],
    db: Session = Depends(get_db),
):
    v = db.query(Version).filter(Version.id == version_id).first()
    if not v:
        raise HTTPException(status_code=404, detail="Version not found")
    v.version_no = payload.version_no
    v.version_type = payload.version_type
    v.parent_id = payload.parent_id
    db.commit()
    return {"message": "Version updated"}


@app.delete("/versions/{version_id}")
def delete_version(
    version_id: int,
    _: Annotated[User, Depends(require_admin)],
    db: Session = Depends(get_db),
):
    v = db.query(Version).filter(Version.id == version_id).first()
    if not v:
        raise HTTPException(status_code=404, detail="Version not found")
    db.delete(v)
    db.commit()
    return {"message": "Version deleted with cascaded children"}


@app.get("/admin/data-overview")
def admin_data_overview(
    _: Annotated[User, Depends(require_admin)],
    db: Session = Depends(get_db),
):
    return {
        "versions": [{"id": v.id, "version_no": v.version_no, "version_type": v.version_type.value, "parent_id": v.parent_id} for v in db.query(Version).order_by(Version.created_at.desc()).all()],
        "requirements": [{"id": r.id, "zentao_req_id": r.zentao_req_id, "title": r.title, "major_version_id": r.major_version_id} for r in db.query(Requirement).order_by(Requirement.id.desc()).all()],
        "bugs": [{"id": b.id, "bug_id": b.bug_id, "major_version_id": b.major_version_id} for b in db.query(BugTracking).order_by(BugTracking.id.desc()).all()],
    }


@app.put("/bugs/{bug_id}")
def update_bug(
    bug_id: int,
    new_bug_id: str,
    _: Annotated[User, Depends(require_admin)],
    db: Session = Depends(get_db),
):
    if not B_PATTERN.match(new_bug_id):
        raise HTTPException(status_code=400, detail="Invalid bug format")
    bug = db.query(BugTracking).filter(BugTracking.id == bug_id).first()
    if not bug:
        raise HTTPException(status_code=404, detail="Bug not found")
    bug.bug_id = new_bug_id
    db.commit()
    return {"message": "Bug updated"}


@app.delete("/bugs/{bug_id}")
def delete_bug(
    bug_id: int,
    _: Annotated[User, Depends(require_admin)],
    db: Session = Depends(get_db),
):
    bug = db.query(BugTracking).filter(BugTracking.id == bug_id).first()
    if not bug:
        raise HTTPException(status_code=404, detail="Bug not found")
    db.delete(bug)
    db.commit()
    return {"message": "Bug deleted"}

@app.post("/requirements/assign-and-publish")
async def assign_and_publish(
    payload: AssignPublishPayload,
    _: Annotated[User, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
):
    req_map = {
        r.id: r
        for r in db.query(Requirement).filter(Requirement.major_version_id == payload.major_version_id).all()
    }
    if not req_map:
        raise HTTPException(status_code=404, detail="No requirements found")

    for item in payload.assignments:
        req = req_map.get(item.requirement_id)
        if not req:
            continue
        req.owner_id = item.owner_id
        req.status = RequirementStatus.ASSIGNED if item.owner_id else RequirementStatus.PENDING

    db.commit()

    rows = (
        db.query(Requirement)
        .options(joinedload(Requirement.owner))
        .filter(Requirement.major_version_id == payload.major_version_id)
        .order_by(Requirement.id.asc())
        .all()
    )

    assigned_lines: list[str] = []
    pending_lines: list[str] = []
    for r in rows:
        if r.owner:
            assigned_lines.append(f"- {r.zentao_req_id} {r.title} -> @{r.owner.username}")
        else:
            pending_lines.append(f"- {r.zentao_req_id} {r.title}")

    markdown = "\n".join([
        "## 阶段一：需求分配更新",
        "### 已分配",
        *(assigned_lines or ["- 无"]),
        "### 待分配区",
        *(pending_lines or ["- 无"]),
    ])
    await _send_wechat_markdown(markdown)
    return {"message": "Assignments updated and notification sent"}


@app.put("/requirements/{requirement_id}/cases")
def update_requirement_cases(
    requirement_id: int,
    payload: CaseUpdatePayload,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    requirement = db.query(Requirement).filter(Requirement.id == requirement_id).first()
    if not requirement:
        raise HTTPException(status_code=404, detail="Requirement not found")

    if requirement.owner_id and requirement.owner_id != current_user.id and current_user.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="Only owner can update cases")

    case_ids = _parse_multiple_ids(payload.case_ids, U_PATTERN, "test case")
    db.query(TestCase).filter(TestCase.requirement_id == requirement_id).delete()
    for cid in case_ids:
        db.add(TestCase(requirement_id=requirement.id, zentao_case_id=cid, creator_id=current_user.id))

    if requirement.case_completed and not payload.case_completed:
        requirement.status = RequirementStatus.ASSIGNED
    elif payload.case_completed:
        requirement.status = RequirementStatus.CASE_DONE
    requirement.case_completed = payload.case_completed

    db.commit()
    return {"message": "Cases updated", "case_completed": requirement.case_completed, "case_ids": case_ids}


@app.post("/push/case-progress")
async def push_case_progress(
    payload: ProgressPushPayload,
    _: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    reqs = (
        db.query(Requirement)
        .options(joinedload(Requirement.owner), joinedload(Requirement.retester), joinedload(Requirement.test_cases))
        .filter(Requirement.major_version_id == payload.major_version_id)
        .order_by(Requirement.id.asc())
        .all()
    )
    by_user: dict[str, list[Requirement]] = {}
    for r in reqs:
        key = r.owner.username if r.owner else "未分配"
        by_user.setdefault(key, []).append(r)

    lines = ["## 阶段二：用例编写进度"]
    for owner, items in by_user.items():
        lines.append(f"### {owner}")
        for req in items:
            u_ids = ", ".join([c.zentao_case_id for c in req.test_cases]) or "无"
            done = "✅" if req.case_completed else "⬜"
            lines.append(f"- {done} {req.zentao_req_id} {req.title} | 用例: {u_ids}")
        if owner != "未分配" and items and all(i.case_completed for i in items):
            lines.append("- 该成员用例已全编写完毕")

    await _send_wechat_markdown("\n".join(lines))
    return {"message": "Case progress pushed"}


@app.post("/requirements/{requirement_id}/case-rollback")
async def rollback_case_status(
    requirement_id: int,
    _: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    req = db.query(Requirement).filter(Requirement.id == requirement_id).first()
    if not req:
        raise HTTPException(status_code=404, detail="Requirement not found")
    req.case_completed = False
    req.status = RequirementStatus.ASSIGNED if req.owner_id else RequirementStatus.PENDING
    db.commit()
    await _send_wechat_markdown("⚠️ 用例状态回滚，退回编写阶段")
    return {"message": "Rollback notification sent"}


@app.get("/testing/requirements")
def list_testable_requirements(
    major_version_id: int,
    _: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    rows = (
        db.query(Requirement)
        .options(joinedload(Requirement.test_cases))
        .filter(Requirement.major_version_id == major_version_id, Requirement.case_completed.is_(True))
        .order_by(Requirement.id.asc())
        .all()
    )
    return [
        {
            "id": r.id,
            "zentao_req_id": r.zentao_req_id,
            "title": r.title,
            "case_ids": [c.zentao_case_id for c in r.test_cases],
            "test_completed": r.test_completed,
        }
        for r in rows
    ]


@app.put("/requirements/{requirement_id}/test-execution")
def upsert_test_execution(
    requirement_id: int,
    payload: TestExecutionPayload,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    requirement = db.query(Requirement).options(joinedload(Requirement.test_cases)).filter(Requirement.id == requirement_id).first()
    if not requirement:
        raise HTTPException(status_code=404, detail="Requirement not found")
    if not requirement.case_completed:
        raise HTTPException(status_code=400, detail="Requirement not passed case gate")

    minor = db.query(Version).filter(Version.id == payload.minor_version_id, Version.version_type == VersionType.MINOR).first()
    if not minor:
        raise HTTPException(status_code=400, detail="Invalid minor version")

    if payload.bug_id and not B_PATTERN.match(payload.bug_id):
        raise HTTPException(status_code=400, detail="Invalid bug id format")
    if payload.source_case_id and payload.source_case_id != "无关联发现":
        allowed = {c.zentao_case_id for c in requirement.test_cases}
        if payload.source_case_id not in allowed:
            raise HTTPException(status_code=400, detail="source_case_id must belong to requirement case ids")

    execution = (
        db.query(TestExecution)
        .filter(
            TestExecution.requirement_id == requirement_id,
            TestExecution.minor_version_id == payload.minor_version_id,
        )
        .first()
    )
    if not execution:
        execution = TestExecution(requirement_id=requirement_id, minor_version_id=payload.minor_version_id)
        db.add(execution)

    execution.bug_id = payload.bug_id
    execution.source_case_id = payload.source_case_id
    execution.result_status = payload.result_status
    execution.notes = payload.notes
    execution.executed_by_id = current_user.id
    execution.executed_at = datetime.utcnow()

    if requirement.test_completed and not payload.test_completed:
        requirement.status = RequirementStatus.CASE_DONE
    elif payload.test_completed:
        requirement.status = RequirementStatus.TEST_DONE
    else:
        requirement.status = RequirementStatus.TESTING
    requirement.test_completed = payload.test_completed

    db.commit()
    return {"message": "Test execution updated"}


@app.post("/push/test-progress")
async def push_test_progress(
    minor_version_id: int,
    major_version_id: int,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    minor = db.query(Version).filter(Version.id == minor_version_id, Version.version_type == VersionType.MINOR).first()
    if not minor:
        raise HTTPException(status_code=404, detail="minor version not found")

    reqs = (
        db.query(Requirement)
        .options(joinedload(Requirement.test_executions))
        .filter(Requirement.major_version_id == major_version_id, Requirement.case_completed.is_(True))
        .order_by(Requirement.id.asc())
        .all()
    )
    lines = [f"## 阶段三：测试进度播报（子版本 {minor.version_no}）"]
    for r in reqs:
        exec_row = next((e for e in r.test_executions if e.minor_version_id == minor.id), None)
        state = "✅测试完成" if r.test_completed else "⬜待测试"
        if exec_row:
            bug = exec_row.bug_id or "无"
            src = exec_row.source_case_id or "无"
            lines.append(f"- {r.zentao_req_id} | {state} | 结果:{exec_row.result_status} | Bug:{bug} | 来源用例:{src}")
        else:
            lines.append(f"- {r.zentao_req_id} | {state} | 结果:untested | Bug:无 | 来源用例:无")

    other_members = db.query(User).filter(User.id != current_user.id).order_by(User.username.asc()).all()
    if other_members:
        mentions = " ".join([f"@{u.username}" for u in other_members])
        lines.append(f"\n以上需求已测试完毕，请其他人前往系统进行交叉复测！{mentions}")

    await _send_wechat_markdown("\n".join(lines))
    return {"message": "Test progress pushed"}


@app.post("/requirements/{requirement_id}/test-rollback")
async def rollback_test_status(
    requirement_id: int,
    _: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    req = db.query(Requirement).filter(Requirement.id == requirement_id).first()
    if not req:
        raise HTTPException(status_code=404, detail="Requirement not found")
    req.test_completed = False
    req.status = RequirementStatus.CASE_DONE
    db.commit()
    await _send_wechat_markdown("⚠️ 测试状态回滚，需重新测试")
    return {"message": "Rollback notification sent"}

@app.get("/export")
def export_data(
    format: str = Query("csv", pattern="^(csv|xlsx)$"),
    major_version_id: Optional[int] = Query(default=None),
    minor_version_id: Optional[int] = Query(default=None),
    _: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    rows = _build_export_rows(db, major_version_id=major_version_id, minor_version_id=minor_version_id)
    if not rows:
        raise HTTPException(status_code=404, detail="No data for export")

    fields = [
        "requirement_id",
        "major_version",
        "zentao_req_id",
        "title",
        "owner",
        "case_ids",
        "case_completed",
        "test_completed",
        "status",
        "minor_version",
        "result_status",
        "bug_id",
        "source_case_id",
        "executed_at",
    ]

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

    return FileResponse(
        path=str(out_path),
        filename=out_path.name,
        media_type=media_type,
    )


def _seed_stage5_bug_pool(db: Session, major_version_id: int, actor_id: int | None = None) -> None:
    exists = db.query(BugTracking).filter(BugTracking.major_version_id == major_version_id).first()
    if exists:
        return

    reqs = (
        db.query(Requirement)
        .options(joinedload(Requirement.test_cases), joinedload(Requirement.test_executions))
        .filter(Requirement.major_version_id == major_version_id)
        .all()
    )
    for req in reqs:
        for exe in req.test_executions:
            if exe.bug_id:
                db.add(
                    BugTracking(
                        major_version_id=major_version_id,
                        requirement_id=req.id,
                        source_type=BugSourceType.REQUIREMENT,
                        source_ref=exe.source_case_id,
                        bug_id=exe.bug_id,
                        latest_minor_version_id=exe.minor_version_id,
                        created_by_id=actor_id,
                    )
                )


@app.get("/retest/workbench")
def get_retest_workbench(
    major_version_id: int,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    rows = (
        db.query(Requirement)
        .options(joinedload(Requirement.owner), joinedload(Requirement.test_cases), joinedload(Requirement.retester))
        .filter(
            Requirement.major_version_id == major_version_id,
            Requirement.test_completed.is_(True),
            Requirement.owner_id.isnot(None),
            Requirement.owner_id != current_user.id,
        )
        .order_by(Requirement.id.asc())
        .all()
    )
    return [
        {
            "id": r.id,
            "zentao_req_id": r.zentao_req_id,
            "title": r.title,
            "owner": r.owner.username if r.owner else None,
            "case_ids": [c.zentao_case_id for c in r.test_cases],
            "retest_completed": r.retest_completed,
            "retested_by": r.retester.username if r.retester else None,
        }
        for r in rows
    ]


@app.put("/requirements/{requirement_id}/retest")
def submit_retest(
    requirement_id: int,
    payload: RetestPayload,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    req = db.query(Requirement).filter(Requirement.id == requirement_id).first()
    if not req:
        raise HTTPException(status_code=404, detail="Requirement not found")
    if not req.test_completed:
        raise HTTPException(status_code=400, detail="Requirement has not completed test yet")
    if req.owner_id == current_user.id:
        raise HTTPException(status_code=403, detail="Self-tested requirement cannot be cross-retested by self")

    req.retest_completed = payload.retest_completed
    req.retested_by_id = current_user.id if payload.retest_completed else None
    req.retested_at = datetime.utcnow() if payload.retest_completed else None
    db.commit()
    return {"message": "Retest status updated"}


@app.post("/push/retest-result")
async def push_retest_result(
    major_version_id: int,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    rows = (
        db.query(Requirement)
        .filter(
            Requirement.major_version_id == major_version_id,
            Requirement.retest_completed.is_(True),
            Requirement.retested_by_id == current_user.id,
        )
        .order_by(Requirement.id.asc())
        .all()
    )
    if not rows:
        raise HTTPException(status_code=400, detail="No retested requirements by current user")

    lines = ["## 阶段四：交叉复测播报"]
    for req in rows:
        lines.append(f"✅ [{req.zentao_req_id}] 需求已由 [{current_user.username}] 复测通过，顺利闭环！")
    await _send_wechat_markdown("\n".join(lines))

    return {"message": "Retest results pushed", "count": len(rows)}


@app.get("/stage5/overview")
def stage5_overview(
    major_version_id: int,
    _: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    total = db.query(Requirement).filter(Requirement.major_version_id == major_version_id).count()
    retested = (
        db.query(Requirement)
        .filter(Requirement.major_version_id == major_version_id, Requirement.retest_completed.is_(True))
        .count()
    )
    if total > 0 and total != retested:
        raise HTTPException(status_code=400, detail="Not all requirements finished cross-retest")

    _seed_stage5_bug_pool(db, major_version_id)
    db.commit()

    reqs = (
        db.query(Requirement)
        .options(joinedload(Requirement.test_cases), joinedload(Requirement.test_executions))
        .filter(Requirement.major_version_id == major_version_id)
        .order_by(Requirement.id.asc())
        .all()
    )
    bugs = (
        db.query(BugTracking)
        .filter(BugTracking.major_version_id == major_version_id)
        .order_by(BugTracking.id.asc())
        .all()
    )

    return {
        "major_version_id": major_version_id,
        "requirements": [
            {
                "id": r.id,
                "zentao_req_id": r.zentao_req_id,
                "title": r.title,
                "case_ids": [c.zentao_case_id for c in r.test_cases],
                "history_bug_ids": [e.bug_id for e in r.test_executions if e.bug_id],
            }
            for r in reqs
        ],
        "bug_pool": [
            {
                "id": b.id,
                "bug_id": b.bug_id,
                "source_type": b.source_type.value,
                "source_ref": b.source_ref,
                "requirement_id": b.requirement_id,
                "latest_minor_version_id": b.latest_minor_version_id,
                "test_done": b.test_done,
                "newly_found_bug_id": b.newly_found_bug_id,
                "closed": b.closed,
            }
            for b in bugs
        ],
    }


@app.put("/stage5/bugs/{bug_track_id}/result")
async def submit_stage5_result(
    bug_track_id: int,
    payload: Stage5ResultPayload,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    bug = db.query(BugTracking).filter(BugTracking.id == bug_track_id).first()
    if not bug:
        raise HTTPException(status_code=404, detail="Bug tracking item not found")

    if payload.newly_found_bug_id and not B_PATTERN.match(payload.newly_found_bug_id):
        raise HTTPException(status_code=400, detail="Invalid newly_found_bug_id format")

    bug.latest_minor_version_id = payload.minor_version_id
    bug.test_done = payload.test_done
    bug.newly_found_bug_id = payload.newly_found_bug_id
    bug.closed = payload.test_done and (not payload.newly_found_bug_id)
    bug.closed_by_id = current_user.id if bug.closed else None

    if payload.newly_found_bug_id:
        db.add(
            BugTracking(
                major_version_id=bug.major_version_id,
                requirement_id=bug.requirement_id,
                source_type=BugSourceType.LEGACY_BUG,
                source_ref=bug.bug_id,
                bug_id=payload.newly_found_bug_id,
                latest_minor_version_id=payload.minor_version_id,
                created_by_id=current_user.id,
            )
        )

    db.commit()
    await _send_wechat_markdown(
        f"阶段五整体测试更新：Bug[{bug.bug_id}] 在包[{payload.minor_version_id}] 已提交结果，状态={'通过' if bug.closed else '未闭环'}"
    )
    return {"message": "Stage5 result updated"}


@app.post("/stage5/issues")
async def add_stage5_issue(
    payload: Stage5IssueCreatePayload,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    if not B_PATTERN.match(payload.bug_id):
        raise HTTPException(status_code=400, detail="bug_id must be like b#xxxx")

    item = BugTracking(
        major_version_id=payload.major_version_id,
        requirement_id=payload.requirement_id,
        source_type=payload.source_type,
        source_ref=payload.source_ref,
        bug_id=payload.bug_id,
        latest_minor_version_id=payload.minor_version_id,
        created_by_id=current_user.id,
    )
    db.add(item)
    db.commit()
    db.refresh(item)

    await _send_wechat_markdown(
        f"➕ 新增问题：[{payload.bug_id}] 已录入问题池，来源={payload.source_type.value} {payload.source_ref or ''}"
    )
    return {"id": item.id, "message": "Issue added"}


@app.post("/stage5/push-status")
async def push_stage5_status(
    major_version_id: int,
    minor_version_id: int,
    _: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    bugs = (
        db.query(BugTracking)
        .filter(BugTracking.major_version_id == major_version_id)
        .order_by(BugTracking.id.asc())
        .all()
    )
    lines = [f"## 阶段五：整体测试进度（包 {minor_version_id}）"]
    remaining = 0
    for b in bugs:
        closed = b.closed and b.latest_minor_version_id == minor_version_id and not b.newly_found_bug_id
        if not closed:
            remaining += 1
        lines.append(
            f"- {b.bug_id} | {'✅已验证通过' if closed else '❗待回归/失败'} | 新Bug:{b.newly_found_bug_id or '无'}"
        )

    if bugs and remaining == 0:
        major = db.query(Version).filter(Version.id == major_version_id).first()
        lines.append(f"\n🎉 【大版本 {major.version_no if major else major_version_id}】整体测试完美通关，符合发布标准！")

    await _send_wechat_markdown("\n".join(lines))
    return {"message": "Stage5 status pushed", "remaining": remaining}
