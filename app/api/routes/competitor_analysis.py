from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.models import User
from app.services.competitor_analysis_service import CompetitorAnalysisService


router = APIRouter(prefix="/competitor-analysis", tags=["competitor-analysis"])


class ReportSavePayload(BaseModel):
    data: dict[str, Any]
    baseVersion: int = Field(ge=1)


@router.get("/report")
def get_report(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return CompetitorAnalysisService(db).get_report(current_user)


@router.put("/report")
def save_report(
    payload: ReportSavePayload,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return CompetitorAnalysisService(db).save_report(
        payload.data, payload.baseVersion, current_user
    )
