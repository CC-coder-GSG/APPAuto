from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.models import TestResultStatus, User
from app.services.requirement_service import RequirementService

router = APIRouter()


class TestExecutionPayload(BaseModel):
    minor_version_id: int
    bug_id: Optional[str] = None
    source_case_id: Optional[str] = None
    result_status: TestResultStatus = TestResultStatus.PASSED
    test_completed: bool
    notes: Optional[str] = None


@router.put("/requirements/{requirement_id}/test-execution")
def upsert_test_execution(
    requirement_id: int,
    payload: TestExecutionPayload,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    service = RequirementService(db)
    return service.upsert_test_execution(
        requirement_id=requirement_id,
        minor_version_id=payload.minor_version_id,
        bug_id=payload.bug_id,
        source_case_id=payload.source_case_id,
        result_status=payload.result_status.value,
        notes=payload.notes,
        test_completed=payload.test_completed,
        actor_id=current_user.id,
    )
