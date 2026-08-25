from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.models import User
from app.services.permission_service import ensure_tab_access
from app.services.zentao_testcase_service import ZentaoTestCaseService

router = APIRouter(prefix="/zentao/testcases", tags=["zentao_testcases"])


class ZentaoTestCaseSyncPayload(BaseModel):
    software_id: int
    force: bool = False


@router.post("/sync")
def sync_zentao_testcases(
    payload: ZentaoTestCaseSyncPayload,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    ensure_tab_access(current_user, "testcase-center")
    return ZentaoTestCaseService(db).sync_software_testcases(
        software_id=payload.software_id,
        current_user=current_user,
        force=payload.force,
        sync_source="manual_sync",
    )


@router.post("/sync-recent")
def sync_zentao_testcases_recent(
    payload: ZentaoTestCaseSyncPayload,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Incremental sync: pulls only the most recently edited cases."""
    ensure_tab_access(current_user, "testcase-center")
    return ZentaoTestCaseService(db).sync_recent_software_testcases(
        software_id=payload.software_id,
        current_user=current_user,
        force=payload.force,
        sync_source="manual_incremental",
    )


@router.get("")
def list_zentao_testcases(
    software_id: Optional[int] = None,
    product_id: Optional[int] = None,
    module_id: Optional[int] = None,
    requirement_id: Optional[int] = None,
    status: Optional[str] = None,
    keyword: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    ensure_tab_access(current_user, "testcase-center")
    return ZentaoTestCaseService(db).list_cases(
        software_id=software_id,
        product_id=product_id,
        module_id=module_id,
        requirement_id=requirement_id,
        status=status,
        keyword=keyword,
        page=page,
        page_size=page_size,
    )


@router.get("/modules")
def list_zentao_testcase_modules(
    software_id: Optional[int] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    ensure_tab_access(current_user, "testcase-center")
    return ZentaoTestCaseService(db).list_modules(software_id=software_id)


@router.get("/{case_numeric_id}")
def get_zentao_testcase_detail(
    case_numeric_id: int,
    software_id: Optional[int] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    ensure_tab_access(current_user, "testcase-center")
    return ZentaoTestCaseService(db).get_case_detail(
        case_numeric_id,
        software_id=software_id,
        current_user=current_user,
    )
