from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.models import User
from app.services.export_service import ExportService

router = APIRouter()


@router.get("/export")
def export_data(
    format: str = Query("csv", pattern="^(csv|xlsx)$"),
    major_version_id: Optional[int] = Query(default=None),
    minor_version_id: Optional[int] = Query(default=None),
    _: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    service = ExportService(db)
    out_path, media_type = service.export(format, major_version_id, minor_version_id)
    return FileResponse(path=str(out_path), filename=out_path.name, media_type=media_type)
