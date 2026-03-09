from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.models import User
from app.services.retest_service import RetestService

router = APIRouter()


class RetestPayload(BaseModel):
    retest_completed: bool
    retest_passed: bool | None = None
    retest_minor_version_id: int | None = None


@router.get("/retest/workbench")
def get_retest_workbench(major_version_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    service = RetestService(db)
    return service.get_workbench(major_version_id, current_user)


@router.put("/requirements/{requirement_id}/retest")
def submit_retest(requirement_id: int, payload: RetestPayload, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    service = RetestService(db)
    return service.submit_retest(
        requirement_id,
        retest_completed=payload.retest_completed,
        retest_passed=payload.retest_passed,
        retest_minor_version_id=payload.retest_minor_version_id,
        current_user=current_user,
    )
