from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.models import SoftwareProduct, User
from app.services.permission_service import ensure_admin
from app.services.zentao_version_sync_service import ZentaoVersionSyncService

router = APIRouter()


class SoftwareCreatePayload(BaseModel):
    name: str
    zentao_product_id: Optional[int] = None
    zentao_product_name: Optional[str] = None


class SoftwareFromZentaoPayload(BaseModel):
    name: str
    zentao_project_id: int
    zentao_project_name: Optional[str] = None
    sync_minor: bool = True


def _serialize(item: SoftwareProduct) -> dict:
    return {
        "id": item.id,
        "name": item.name,
        "created_at": item.created_at.isoformat(),
        "zentao_product_id": item.zentao_product_id,
        "zentao_product_name_cache": item.zentao_product_name_cache,
    }


@router.get("/softwares")
def list_softwares(_: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.query(SoftwareProduct).order_by(SoftwareProduct.created_at.asc()).all()
    return [_serialize(r) for r in rows]


@router.post("/softwares", status_code=201)
def create_software(payload: SoftwareCreatePayload, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    ensure_admin(current_user)
    name = (payload.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="软件名称不能为空")
    existing = db.query(SoftwareProduct).filter(SoftwareProduct.name == name).first()
    if existing:
        raise HTTPException(status_code=400, detail="软件名称已存在")
    item = SoftwareProduct(
        name=name,
        zentao_product_id=payload.zentao_product_id,
        zentao_product_name_cache=(payload.zentao_product_name or "").strip() or None,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return _serialize(item)


@router.post("/softwares/from-zentao", status_code=201)
async def create_software_from_zentao(
    payload: SoftwareFromZentaoPayload,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    合并入口：新建软件 + 锚定禅道项目 + 立即同步版本。

    一步完成「数据管理」里原本拆成三步的流程（建软件 → 选禅道项目 → 开始同步）。
    选中的禅道项目 id 会作为软件的 ``zentao_product_id`` 锚点存下来，后续同步/对账
    可直接复用，不必每次手填。
    """
    ensure_admin(current_user)
    name = (payload.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="软件名称不能为空")
    if payload.zentao_project_id <= 0:
        raise HTTPException(status_code=400, detail="请选择禅道项目")
    existing = db.query(SoftwareProduct).filter(SoftwareProduct.name == name).first()
    if existing:
        raise HTTPException(status_code=400, detail="软件名称已存在")

    item = SoftwareProduct(
        name=name,
        zentao_product_id=payload.zentao_project_id,
        zentao_product_name_cache=(payload.zentao_project_name or "").strip() or None,
    )
    db.add(item)
    db.commit()
    db.refresh(item)

    service = ZentaoVersionSyncService(db)
    result = await service.sync_versions(
        user_id=current_user.id,
        software_id=item.id,
        zentao_project_id=payload.zentao_project_id,
        sync_minor=payload.sync_minor,
    )

    return {
        "software": _serialize(item),
        "sync": {
            "created_major": result.created_major,
            "updated_major": result.updated_major,
            "created_minor": result.created_minor,
            "updated_minor": result.updated_minor,
            "skipped": result.skipped,
            "summary": (
                f"大版本 +{len(result.created_major)} 更新{len(result.updated_major)}，"
                f"子版本 +{len(result.created_minor)} 更新{len(result.updated_minor)}"
                + (f"，跳过 {len(result.skipped)} 个" if result.skipped else "")
            ),
        },
    }
