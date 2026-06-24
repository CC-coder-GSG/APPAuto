from __future__ import annotations

import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.models import User
from app.services.feature_tree_service import FeatureTreeService

router = APIRouter()

# 复用既有的内联图片目录与公开服务路由（/zentao/inline-images/...），
# 富文本备注 / 标记说明里的 <img> 走它即可，浏览器 img 请求不带鉴权头。
_INLINE_IMAGES_ROOT = Path(__file__).resolve().parents[3] / "uploads" / "inline_images"


class NodeCreatePayload(BaseModel):
    software_id: int
    parent_id: int
    name: str


class NodeUpdatePayload(BaseModel):
    name: Optional[str] = None
    note_html: Optional[str] = None


class MarkPayload(BaseModel):
    version_id: int
    comment_html: Optional[str] = None


@router.get("/feature-tree")
def get_feature_tree(
    software_id: int,
    version_id: Optional[int] = Query(None),
    _: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return FeatureTreeService(db).get_tree(software_id, version_id=version_id)


@router.post("/feature-tree/nodes", status_code=201)
def create_node(
    payload: NodeCreatePayload,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return FeatureTreeService(db).create_node(
        payload.software_id, payload.parent_id, payload.name, current_user
    )


@router.put("/feature-tree/nodes/{node_id}")
def update_node(
    node_id: int,
    payload: NodeUpdatePayload,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return FeatureTreeService(db).update_node(
        node_id, current_user, name=payload.name, note_html=payload.note_html
    )


@router.delete("/feature-tree/nodes/{node_id}")
def delete_node(
    node_id: int,
    _: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return FeatureTreeService(db).delete_node(node_id)


@router.put("/feature-tree/nodes/{node_id}/mark")
def set_mark(
    node_id: int,
    payload: MarkPayload,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return FeatureTreeService(db).set_mark(
        node_id, payload.version_id, payload.comment_html, current_user
    )


@router.delete("/feature-tree/nodes/{node_id}/mark")
def delete_mark(
    node_id: int,
    version_id: int = Query(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return FeatureTreeService(db).delete_mark(node_id, version_id, current_user)


@router.post("/feature-tree/upload-image")
async def upload_image(
    file: UploadFile = File(...),
    _: User = Depends(get_current_user),
):
    """富文本备注 / 标记说明的内联贴图：本地保存，返回公开可访问的 URL。"""
    if not file or not file.filename:
        raise HTTPException(status_code=400, detail="未提供图片文件")
    content_type = (file.content_type or "").lower()
    if not content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="仅支持上传图片文件")
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="图片内容为空")
    if len(data) > 10 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="图片过大（最大 10MB）")

    ext = content_type.split("/")[-1].split(";")[0].strip() or "png"
    if ext == "jpeg":
        ext = "jpg"
    today = datetime.now().strftime("%Y%m%d")
    target_dir = _INLINE_IMAGES_ROOT / today
    target_dir.mkdir(parents=True, exist_ok=True)
    name = f"{uuid.uuid4().hex}.{ext}"
    (target_dir / name).write_bytes(data)
    return {"url": f"/zentao/inline-images/{today}/{name}"}
