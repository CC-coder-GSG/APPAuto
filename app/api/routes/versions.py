from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

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


@router.get('/versions')
def list_versions(software_id: Optional[int] = None, _: object = Depends(get_current_user), db: Session = Depends(get_db)):
    return VersionService.list_versions(db, software_id=software_id)


@router.post('/versions', status_code=201)
def create_version(payload: VersionCreatePayload, _: object = Depends(get_current_user), db: Session = Depends(get_db)):
    version_no = (payload.version_no or '').strip()
    if not version_no:
        raise HTTPException(status_code=400, detail='版本号不能为空')

    if payload.version_type == VersionType.MINOR and not payload.parent_id:
        raise HTTPException(status_code=400, detail='子版本必须指定父大版本')
    if payload.version_type == VersionType.MAJOR and payload.parent_id is not None:
        raise HTTPException(status_code=400, detail='大版本不能指定父版本')

    resolved_software_id = payload.software_id
    if payload.version_type == VersionType.MINOR and payload.parent_id:
        parent = db.query(Version).filter(Version.id == payload.parent_id, Version.version_type == VersionType.MAJOR).first()
        if not parent:
            raise HTTPException(status_code=400, detail='父大版本不存在')
        resolved_software_id = parent.software_id

    if payload.version_type == VersionType.MAJOR and not resolved_software_id:
        raise HTTPException(status_code=400, detail='创建大版本必须指定 software_id')

    if payload.version_type == VersionType.MAJOR:
        exists = db.query(Version).filter(
            Version.version_type == VersionType.MAJOR,
            Version.version_no == version_no,
            Version.software_id == resolved_software_id,
        ).first()
        if exists:
            raise HTTPException(status_code=400, detail=f'大版本重复：{version_no} 已存在')
    else:
        # 同一个软件内版本号唯一；不同软件允许复用常见版本号。
        exists = db.query(Version).filter(
            Version.version_type == VersionType.MINOR,
            Version.version_no == version_no,
            Version.software_id == resolved_software_id,
        ).first()
        if exists:
            parent_no = exists.parent.version_no if exists.parent else None
            detail = (
                f'子版本重复：{version_no} 已存在（归属于大版本 {parent_no}）'
                if parent_no
                else f'子版本重复：{version_no} 已存在'
            )
            raise HTTPException(status_code=400, detail=detail)

    version = Version(
        version_no=version_no,
        version_type=payload.version_type,
        parent_id=payload.parent_id,
        software_id=resolved_software_id,
    )
    db.add(version)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=400, detail=f'版本号冲突：{version_no} 已存在')
    db.refresh(version)
    return {
        'id': version.id,
        'version_no': version.version_no,
        'version_type': version.version_type,
        'parent_id': version.parent_id,
        'software_id': version.software_id,
    }


@router.put('/versions/{version_id}')
def update_version(version_id: int, payload: VersionCreatePayload, current_user=Depends(get_current_user), db: Session = Depends(get_db)):
    ensure_admin(current_user)

    version_no = (payload.version_no or '').strip()
    if not version_no:
        raise HTTPException(status_code=400, detail='版本号不能为空')

    v = db.query(Version).filter(Version.id == version_id).first()
    if not v:
        raise HTTPException(status_code=404, detail='Version not found')

    resolved_software_id = payload.software_id
    if payload.version_type == VersionType.MAJOR:
        if payload.parent_id is not None:
            raise HTTPException(status_code=400, detail='大版本不能指定父版本')
        if not payload.software_id:
            raise HTTPException(status_code=400, detail='大版本必须指定 software_id')
    else:
        parent = db.query(Version).filter(Version.id == payload.parent_id, Version.version_type == VersionType.MAJOR).first()
        if not parent:
            raise HTTPException(status_code=400, detail='父大版本不存在')
        resolved_software_id = parent.software_id

    if payload.version_type == VersionType.MAJOR:
        exists = db.query(Version).filter(
            Version.id != version_id,
            Version.version_type == VersionType.MAJOR,
            Version.version_no == version_no,
            Version.software_id == resolved_software_id,
        ).first()
        if exists:
            raise HTTPException(status_code=400, detail=f'大版本重复：{version_no} 已存在')
    else:
        exists = db.query(Version).filter(
            Version.id != version_id,
            Version.version_type == VersionType.MINOR,
            Version.version_no == version_no,
            Version.software_id == resolved_software_id,
        ).first()
        if exists:
            parent_no = exists.parent.version_no if exists.parent else None
            detail = (
                f'子版本重复：{version_no} 已存在（归属于大版本 {parent_no}）'
                if parent_no
                else f'子版本重复：{version_no} 已存在'
            )
            raise HTTPException(status_code=400, detail=detail)

    v.version_no = version_no
    v.version_type = payload.version_type
    v.parent_id = payload.parent_id
    v.software_id = resolved_software_id

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=400, detail=f'版本号冲突：{version_no} 已存在')
    return {'message': '版本更新成功'}


@router.delete('/versions/{version_id}')
def delete_version(version_id: int, current_user=Depends(get_current_user), db: Session = Depends(get_db)):
    ensure_admin(current_user)
    v = db.query(Version).filter(Version.id == version_id).first()
    if not v:
        raise HTTPException(status_code=404, detail='Version not found')
    db.delete(v)
    db.commit()
    return {'message': 'Version deleted'}
