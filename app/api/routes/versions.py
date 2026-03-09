from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel

from app.api.deps import get_current_user, get_db
from app.models import Version, VersionType
from app.services.permission_service import ensure_admin
from app.services.version_service import VersionService

router = APIRouter()


class VersionCreatePayload(BaseModel):
    version_no: str
    version_type: VersionType
    parent_id: Optional[int] = None
    software_id: Optional[int] = None


@router.get("/versions")
def list_versions(software_id: Optional[int] = None, _: object = Depends(get_current_user), db: Session = Depends(get_db)):
    return VersionService.list_versions(db, software_id=software_id)


@router.post("/versions", status_code=201)
def create_version(payload: VersionCreatePayload, _: object = Depends(get_current_user), db: Session = Depends(get_db)):
    if payload.version_type == VersionType.MINOR and not payload.parent_id:
        raise HTTPException(status_code=400, detail="Minor version must specify parent major version")
    if payload.version_type == VersionType.MAJOR and payload.parent_id is not None:
        raise HTTPException(status_code=400, detail="Major version cannot have a parent")

    if db.query(Version).filter(Version.version_no == payload.version_no, Version.version_type == payload.version_type).first():
        raise HTTPException(status_code=400, detail="Version already exists")

    resolved_software_id = payload.software_id
    if payload.version_type == VersionType.MINOR and payload.parent_id:
        parent = db.query(Version).filter(Version.id == payload.parent_id, Version.version_type == VersionType.MAJOR).first()
        if not parent:
            raise HTTPException(status_code=400, detail="父大版本不存在")
        resolved_software_id = parent.software_id
    if payload.version_type == VersionType.MAJOR and not resolved_software_id:
        raise HTTPException(status_code=400, detail="创建大版本必须指定 software_id")

    version = Version(
        version_no=payload.version_no,
        version_type=payload.version_type,
        parent_id=payload.parent_id,
        software_id=resolved_software_id,
    )
    db.add(version)
    db.commit()
    db.refresh(version)
    return {"id": version.id, "version_no": version.version_no, "version_type": version.version_type, "parent_id": version.parent_id}


@router.put("/versions/{version_id}")
def update_version(version_id: int, payload: VersionCreatePayload, current_user=Depends(get_current_user), db: Session = Depends(get_db)):
    ensure_admin(current_user)
    v = db.query(Version).filter(Version.id == version_id).first()
    if not v:
        raise HTTPException(status_code=404, detail="Version not found")
    v.version_no = payload.version_no
    v.version_type = payload.version_type
    v.parent_id = payload.parent_id
    if payload.version_type == VersionType.MAJOR:
        if not payload.software_id:
            raise HTTPException(status_code=400, detail="大版本必须指定 software_id")
        v.software_id = payload.software_id
    else:
        parent = db.query(Version).filter(Version.id == payload.parent_id, Version.version_type == VersionType.MAJOR).first()
        if not parent:
            raise HTTPException(status_code=400, detail="父大版本不存在")
        v.software_id = parent.software_id
    db.commit()
    return {"message": "Version updated"}


@router.delete("/versions/{version_id}")
def delete_version(version_id: int, current_user=Depends(get_current_user), db: Session = Depends(get_db)):
    ensure_admin(current_user)
    v = db.query(Version).filter(Version.id == version_id).first()
    if not v:
        raise HTTPException(status_code=404, detail="Version not found")
    db.delete(v)
    db.commit()
    return {"message": "Version deleted"}
