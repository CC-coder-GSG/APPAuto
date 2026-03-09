from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.models import SoftwareProduct, User
from app.services.permission_service import ensure_admin

router = APIRouter()


class SoftwareCreatePayload(BaseModel):
    name: str


@router.get("/softwares")
def list_softwares(_: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.query(SoftwareProduct).order_by(SoftwareProduct.created_at.asc()).all()
    return [{"id": r.id, "name": r.name, "created_at": r.created_at.isoformat()} for r in rows]


@router.post("/softwares", status_code=201)
def create_software(payload: SoftwareCreatePayload, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    ensure_admin(current_user)
    name = (payload.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="软件名称不能为空")
    existing = db.query(SoftwareProduct).filter(SoftwareProduct.name == name).first()
    if existing:
        raise HTTPException(status_code=400, detail="软件名称已存在")
    item = SoftwareProduct(name=name)
    db.add(item)
    db.commit()
    db.refresh(item)
    return {"id": item.id, "name": item.name, "created_at": item.created_at.isoformat()}

