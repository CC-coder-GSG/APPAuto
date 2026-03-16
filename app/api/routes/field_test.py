from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.models import FieldTestPurposeType, FieldTestResultStatus, User
from app.services.activity_service import ActivityService
from app.services.field_test_service import FieldTestService

router = APIRouter()


class FieldTestUpsertPayload(BaseModel):
    major_version_id: int
    minor_version_id: int
    purpose_type: FieldTestPurposeType
    requirement_id: Optional[int] = None
    test_content: Optional[str] = None
    start_time: datetime
    end_time: datetime
    result_status: FieldTestResultStatus
    bug_ids: list[str] = Field(default_factory=list)
    notes: Optional[str] = None


class FieldTestAddBugPayload(BaseModel):
    bug_id: str


class FieldTestLinkBugPayload(BaseModel):
    bug_tracking_id: int


@router.get("/field-tests")
def list_field_tests(
    major_version_id: Optional[int] = None,
    minor_version_id: Optional[int] = None,
    purpose_type: Optional[FieldTestPurposeType] = None,
    tester_id: Optional[int] = None,
    software_id: Optional[int] = None,
    keyword: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return FieldTestService(db).list_records(
        current_user=current_user,
        major_version_id=major_version_id,
        minor_version_id=minor_version_id,
        purpose_type=purpose_type,
        tester_id=tester_id,
        software_id=software_id,
        keyword=keyword,
    )


@router.get("/field-tests/options")
def field_test_options(major_version_id: int, _: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return FieldTestService(db).options(major_version_id)


@router.get("/field-tests/paged")
def list_field_tests_paged(
    major_version_id: Optional[int] = None,
    minor_version_id: Optional[int] = None,
    purpose_type: Optional[FieldTestPurposeType] = None,
    tester_id: Optional[int] = None,
    software_id: Optional[int] = None,
    keyword: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    sort_by: str = Query(default="start_time"),
    sort_order: str = Query(default="desc"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return FieldTestService(db).list_records_paged(
        current_user=current_user,
        major_version_id=major_version_id,
        minor_version_id=minor_version_id,
        purpose_type=purpose_type,
        tester_id=tester_id,
        software_id=software_id,
        keyword=keyword,
        page=page,
        page_size=page_size,
        sort_by=sort_by,
        sort_order=sort_order,
    )


@router.get("/field-tests/{record_id}")
def get_field_test_detail(record_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return FieldTestService(db).get_detail(record_id, current_user)


@router.get("/field-tests/{record_id}/timeline")
def get_field_test_timeline(record_id: int, _: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return ActivityService(db).field_test_timeline(record_id)


@router.post("/field-tests")
def create_field_test(payload: FieldTestUpsertPayload, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return FieldTestService(db).create_record(
        major_version_id=payload.major_version_id,
        minor_version_id=payload.minor_version_id,
        purpose_type=payload.purpose_type,
        requirement_id=payload.requirement_id,
        test_content=payload.test_content,
        start_time=payload.start_time,
        end_time=payload.end_time,
        result_status=payload.result_status,
        bug_ids=payload.bug_ids,
        notes=payload.notes,
        actor=current_user,
    )


@router.put("/field-tests/{record_id}")
def update_field_test(record_id: int, payload: FieldTestUpsertPayload, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return FieldTestService(db).update_record(
        record_id,
        major_version_id=payload.major_version_id,
        minor_version_id=payload.minor_version_id,
        purpose_type=payload.purpose_type,
        requirement_id=payload.requirement_id,
        test_content=payload.test_content,
        start_time=payload.start_time,
        end_time=payload.end_time,
        result_status=payload.result_status,
        bug_ids=payload.bug_ids,
        notes=payload.notes,
        actor=current_user,
    )


@router.delete("/field-tests/{record_id}/bugs/{bug_tracking_id}")
def unlink_field_test_bug(record_id: int, bug_tracking_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return FieldTestService(db).unlink_bug(record_id, bug_tracking_id, current_user)


@router.post("/field-tests/{record_id}/bugs/add")
def add_field_test_bug(record_id: int, payload: FieldTestAddBugPayload, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return FieldTestService(db).add_bug_by_no(record_id, payload.bug_id, current_user)


@router.post("/field-tests/{record_id}/bugs/link")
def link_field_test_existing_bug(record_id: int, payload: FieldTestLinkBugPayload, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return FieldTestService(db).link_existing_bug(record_id, payload.bug_tracking_id, current_user)


@router.get("/field-tests/{record_id}/bug-options")
def field_test_bug_options(
    record_id: int,
    keyword: Optional[str] = None,
    limit: int = Query(default=20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return FieldTestService(db).search_existing_bugs(record_id, keyword=keyword, limit=limit, current_user=current_user)


@router.get("/reports/field-test")
def report_field_test(
    start_date: date,
    end_date: date,
    user_id: Optional[int] = Query(default=None),
    major_version_id: Optional[int] = Query(default=None),
    software_id: Optional[int] = Query(default=None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return FieldTestService(db).report_stats(
        start_date=start_date,
        end_date=end_date,
        current_user=current_user,
        user_id=user_id,
        major_version_id=major_version_id,
        software_id=software_id,
    )
