from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.models import User
from app.services.final_test_service import FinalTestService
from app.services.permission_service import ensure_admin

router = APIRouter(prefix="/final-test", tags=["final-test"])


class FinalTestTogglePayload(BaseModel):
    major_version_id: int
    enabled: bool


class FinalTestStatusUpdatePayload(BaseModel):
    case_completed: Optional[bool] = None
    test_completed: Optional[bool] = None


@router.post("/toggle")
def toggle_final_test(
    payload: FinalTestTogglePayload,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    ensure_admin(current_user)
    return FinalTestService(db).toggle(payload.major_version_id, payload.enabled, current_user)


@router.get("/status")
def final_test_status(
    major_version_id: int,
    _: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return FinalTestService(db).status(major_version_id)


@router.get("/progress")
def final_test_progress(
    major_version_id: int,
    _: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return FinalTestService(db).progress_for_major(major_version_id)


@router.patch("/requirements/{requirement_id}/status")
def patch_final_test_status(
    requirement_id: int,
    payload: FinalTestStatusUpdatePayload,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return FinalTestService(db).upsert_record(
        requirement_id,
        current_user,
        case_completed=payload.case_completed,
        test_completed=payload.test_completed,
    )
