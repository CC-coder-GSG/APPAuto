from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.services.bugly_import import MAX_FILE_BYTES, MAX_TEXT, identifier, timestamp
from app.services.bugly_service import BuglyService

router = APIRouter(prefix="/api/bugly", tags=["bugly"])


def service(response: Response, user=Depends(get_current_user), db: Session = Depends(get_db)):
    response.headers["Cache-Control"] = "no-store"
    return BuglyService(db, user)


class Payload(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ApplicationInput(Payload):
    name: str = Field(min_length=1, max_length=120)
    source_host: str = Field(default="bugly.qq.com", min_length=1, max_length=255)
    bugly_app_id: str | None = Field(default=None, max_length=100)
    package_name: str | None = Field(default=None, max_length=255)
    platform: Literal["Android", "iOS", "Other"] = "Android"
    source_timezone: str = "Asia/Shanghai"
    software_id: int | None = None


class IssueInput(Payload):
    application_id: int = Field(gt=0)
    source_issue_id: str | None = Field(default=None, max_length=100)
    title: str = Field(min_length=1, max_length=500)
    exception_type: str = Field(default="待确认", max_length=100)

    @field_validator("source_issue_id")
    @classmethod
    def normalize_id(cls, value):
        return identifier(value)


class IssueUpdate(Payload):
    expected_version: int = Field(ge=1)
    source_issue_id: str | None = Field(default=None, max_length=100)
    title: str | None = Field(default=None, min_length=1, max_length=500)
    status: Literal["pending", "processing", "verifying", "closed", "ignored"] | None = None
    exception_type: str | None = Field(default=None, max_length=100)
    analysis: str | None = Field(default=None, max_length=50000)
    external_note: str | None = Field(default=None, max_length=50000)
    owner_id: int | None = None
    archived: bool | None = None

    @field_validator("source_issue_id")
    @classmethod
    def normalize_id(cls, value):
        return identifier(value)


class StackInput(Payload):
    text: str = Field(default="", max_length=MAX_TEXT)
    mode: Literal["manual", "parsed", "fallback"] = "manual"
    frameCount: int = Field(default=0, ge=0)


class ReportInput(Payload):
    reportId: str | None = Field(default=None, max_length=100)
    listFields: dict = Field(default_factory=dict)
    detailFields: dict = Field(default_factory=dict)
    stack: StackInput = Field(default_factory=StackInput)
    collectedAt: str | None = None
    rawDetailText: str | None = Field(default=None, max_length=MAX_TEXT)


@router.get("/applications")
def applications(svc=Depends(service)):
    return svc.applications()


@router.post("/applications", status_code=201)
def create_application(payload: ApplicationInput, svc=Depends(service)):
    return svc.create_application(payload.model_dump())


@router.get("/issues")
def issues(application_id: int | None = None, q: str = Query("", max_length=200), status: str = "",
           exception_type: str = "", app_version: str = "", device: str = "", since: str = "", until: str = "",
           include_archived: bool = False, page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100), svc=Depends(service)):
    for value in (since, until):
        if value and timestamp(value) is None:
            raise HTTPException(422, "时间格式无效")
    return svc.list_issues(dict(application_id=application_id, q=q, status=status, exception_type=exception_type,
                                app_version=app_version, device=device, since=timestamp(since), until=timestamp(until),
                                include_archived=include_archived, page=page, page_size=page_size))


@router.post("/issues", status_code=201)
def create_issue(payload: IssueInput, svc=Depends(service)):
    return svc.create_issue(payload.model_dump())


@router.get("/issues/{issue_id}")
def issue_detail(issue_id: int, svc=Depends(service)):
    return svc.detail(issue_id)


@router.patch("/issues/{issue_id}")
def update_issue(issue_id: int, payload: IssueUpdate, svc=Depends(service)):
    data = payload.model_dump(exclude_unset=True)
    version = data.pop("expected_version")
    if any(v is None for k, v in data.items() if k not in {"owner_id", "source_issue_id"}):
        raise HTTPException(422, "字段不能设置为 null")
    return svc.update_issue(issue_id, data, version)


@router.get("/issues/{issue_id}/reports")
def reports(issue_id: int, page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100), q: str = "", svc=Depends(service)):
    return svc.reports(issue_id, page, page_size, q)


@router.post("/issues/{issue_id}/reports", status_code=201)
def create_report(issue_id: int, payload: ReportInput, svc=Depends(service)):
    return svc.create_report(issue_id, payload.model_dump(exclude_none=True))


@router.get("/reports/{report_id}")
def report_detail(report_id: int, svc=Depends(service)):
    return svc.report_detail(report_id)


@router.get("/reports")
def report_list(application_id: int | None = None, q: str = Query("", max_length=200), status: str = "",
                app_version: str = "", device: str = "", since: str = "", until: str = "", include_archived: bool = False,
                page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100), svc=Depends(service)):
    for value in (since, until):
        if value and timestamp(value) is None:
            raise HTTPException(422, "时间格式无效")
    return svc.list_reports(dict(application_id=application_id, q=q, status=status, app_version=app_version, device=device,
                                 since=timestamp(since), until=timestamp(until), include_archived=include_archived, page=page, page_size=page_size))


@router.get("/reports/{report_id}/revisions")
def report_revisions(report_id: int, svc=Depends(service)):
    return svc.report_revisions(report_id)


@router.post("/imports/preview")
async def preview_import(application_id: int = Form(...), issue_id: int | None = Form(None), file: UploadFile = File(...), svc=Depends(service)):
    svc.internal()
    data = await file.read(MAX_FILE_BYTES + 1)
    return svc.preview_import(application_id, file.filename or "", data, issue_id)


@router.post("/imports/{batch_id}/confirm")
def confirm_import(batch_id: str, svc=Depends(service)):
    return svc.confirm_import(batch_id)


@router.get("/imports")
def import_history(svc=Depends(service)):
    return svc.import_history()


@router.get("/imports/{batch_id}")
def import_detail(batch_id: str, svc=Depends(service)):
    return svc.batch_dict(svc.batch(batch_id))


