from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.models import User
from app.services.push_service import PushService

router = APIRouter()


class ProgressPushPayload(BaseModel):
    major_version_id: int


@router.post("/push/case-progress")
async def push_case_progress(payload: ProgressPushPayload, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    service = PushService(db)
    return await service.push_case_progress(payload.major_version_id, current_user)


@router.post("/push/test-progress")
async def push_test_progress(minor_version_id: int, major_version_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    service = PushService(db)
    return await service.push_test_progress(major_version_id, minor_version_id, current_user)


@router.post("/push/retest-result")
async def push_retest_result(major_version_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    service = PushService(db)
    return await service.push_retest_result(major_version_id, current_user)


@router.post("/stage5/push-status")
async def push_stage5_status(major_version_id: int, minor_version_id: int, _: User = Depends(get_current_user), db: Session = Depends(get_db)):
    service = PushService(db)
    return await service.push_stage5_status(major_version_id, minor_version_id)
