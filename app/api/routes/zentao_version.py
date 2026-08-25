"""
Zentao version sync endpoints.

GET  /zentao/projects          List Zentao projects accessible to the user
POST /zentao/sync-versions     Sync executions → MAJOR versions and
                               builds → MINOR versions into the local DB
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.services.permission_service import ensure_tab_access
from app.services.zentao_version_sync_service import ZentaoVersionSyncService

router = APIRouter(prefix="/zentao", tags=["zentao_version"])


class VersionSyncPayload(BaseModel):
    software_id: int
    zentao_project_id: int
    sync_minor: bool = True


@router.get("/projects")
def list_zentao_projects(
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Return the list of Zentao projects the current user can access.
    Requires a valid Zentao binding for this user.
    """
    ensure_tab_access(current_user, "data")
    service = ZentaoVersionSyncService(db)
    return service.get_accessible_projects(current_user.id)


@router.post("/sync-versions")
async def sync_versions(
    payload: VersionSyncPayload,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Sync Zentao executions → local MAJOR versions and
    Zentao builds → local MINOR versions.

    Returns counts of created / updated / skipped items.
    Naming rules:
      - Pure sXXXX names → converted to V4.0.3.x
      - All other names  → stored as-is
    """
    if payload.software_id <= 0:
        raise HTTPException(status_code=400, detail='software_id 不能为空')
    if payload.zentao_project_id <= 0:
        raise HTTPException(status_code=400, detail='zentao_project_id 不能为空')

    ensure_tab_access(current_user, "data")
    service = ZentaoVersionSyncService(db)
    result = await service.sync_versions(
        user_id=current_user.id,
        software_id=payload.software_id,
        zentao_project_id=payload.zentao_project_id,
        sync_minor=payload.sync_minor,
    )

    return {
        'created_major': result.created_major,
        'updated_major': result.updated_major,
        'created_minor': result.created_minor,
        'updated_minor': result.updated_minor,
        'skipped': result.skipped,
        'summary': (
            f"大版本 +{len(result.created_major)} 更新{len(result.updated_major)}，"
            f"子版本 +{len(result.created_minor)} 更新{len(result.updated_minor)}"
            + (f"，跳过 {len(result.skipped)} 个" if result.skipped else "")
        ),
    }
