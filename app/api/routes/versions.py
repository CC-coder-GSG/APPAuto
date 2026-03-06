from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.services.version_service import VersionService

router = APIRouter()


@router.get("/versions")
def list_versions(_: object = Depends(get_current_user), db: Session = Depends(get_db)):
    return VersionService.list_versions(db)
