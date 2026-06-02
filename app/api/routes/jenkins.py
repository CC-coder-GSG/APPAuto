"""
Jenkins integration API.

每个用户用自己的 Jenkins 账号 + API Token 绑定，由本系统代为触发"自动化测试"视图下
已配置好的 Job：

- GET    /api/jenkins/binding/me        — 查看当前绑定（不回传 Token）
- PUT    /api/jenkins/binding/me        — 创建 / 更新绑定
- POST   /api/jenkins/binding/me/test   — 测试凭据（不保存）
- DELETE /api/jenkins/binding/me        — 删除绑定
- GET    /api/jenkins/jobs              — 列视图下的 Job（默认 APP_JENKINS_DEFAULT_VIEW）
- GET    /api/jenkins/jobs/{job}        — 取单个 Job 状态 / 参数
- POST   /api/jenkins/jobs/{job}/build  — 触发构建（可带参数）
- GET    /api/jenkins/queue?url=...     — 查询触发后的队列项 → 真实 build number
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.core.config import settings
from app.services import jenkins_service
from app.services.jenkins_client import JenkinsError

router = APIRouter()


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class JenkinsBindingPayload(BaseModel):
    base_url: str = Field(min_length=1, max_length=500, description="Jenkins 地址，如 http://192.168.2.229:8080")
    jenkins_account: str = Field(min_length=1, max_length=120)
    jenkins_token: str = Field(min_length=1, max_length=256, description="Jenkins API Token（明文，服务端加密存储）")


class JenkinsBuildPayload(BaseModel):
    params: Optional[dict] = Field(default=None, description="可选的构建参数键值对")


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _serialize_binding(b) -> dict:
    return {
        "id": b.id,
        "user_id": b.user_id,
        "base_url": b.base_url,
        "jenkins_account": b.jenkins_account,
        "has_token": bool(b.jenkins_token_ciphertext),
        "last_check_at": _iso(b.last_check_at),
        "last_check_status": b.last_check_status,
        "last_error_message": b.last_error_message,
        "created_at": _iso(b.created_at),
        "updated_at": _iso(b.updated_at),
    }


def _require_client(db: Session, user_id: int):
    client = jenkins_service.get_client_for_user(db, user_id)
    if not client:
        raise HTTPException(status_code=400, detail="当前用户尚未配置 Jenkins 绑定，请先在「自动化测试」中绑定账号")
    return client


# ---------------------------------------------------------------------------
# Binding endpoints
# ---------------------------------------------------------------------------

@router.get("/jenkins/binding/me")
def get_my_jenkins_binding(current_user=Depends(get_current_user), db: Session = Depends(get_db)):
    binding = jenkins_service.get_binding(db, current_user.id)
    return {
        "binding": _serialize_binding(binding) if binding else None,
        "defaults": {
            "base_url": settings.jenkins_default_base_url,
            "view": settings.jenkins_default_view,
        },
    }


@router.put("/jenkins/binding/me")
def upsert_my_jenkins_binding(
    payload: JenkinsBindingPayload,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    binding = jenkins_service.upsert_binding(
        db,
        current_user.id,
        base_url=payload.base_url,
        account=payload.jenkins_account,
        token=payload.jenkins_token,
    )
    # Validate the freshly saved credential and record the result.
    ok, message = jenkins_service.test_credentials(
        payload.base_url, payload.jenkins_account, payload.jenkins_token
    )
    jenkins_service.record_check_result(db, current_user.id, ok=ok, message=message)
    db.refresh(binding)
    return {"ok": True, "verified": ok, "message": message, "binding": _serialize_binding(binding)}


@router.post("/jenkins/binding/me/test")
def test_my_jenkins_binding(payload: JenkinsBindingPayload, current_user=Depends(get_current_user)):
    ok, message = jenkins_service.test_credentials(
        payload.base_url, payload.jenkins_account, payload.jenkins_token
    )
    return {"ok": ok, "message": message}


@router.delete("/jenkins/binding/me")
def delete_my_jenkins_binding(current_user=Depends(get_current_user), db: Session = Depends(get_db)):
    removed = jenkins_service.delete_binding(db, current_user.id)
    if not removed:
        raise HTTPException(status_code=404, detail="当前用户未配置 Jenkins 绑定")
    return {"ok": True, "message": "Jenkins 绑定已删除"}


# ---------------------------------------------------------------------------
# Job endpoints
# ---------------------------------------------------------------------------

@router.get("/jenkins/jobs")
def list_jenkins_jobs(
    view: Optional[str] = Query(default=None, description="Jenkins 视图名，默认取配置的自动化测试视图"),
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    client = _require_client(db, current_user.id)
    view_name = (view or settings.jenkins_default_view or "").strip()
    if not view_name:
        raise HTTPException(status_code=400, detail="未指定 Jenkins 视图名")
    try:
        jobs = client.list_view_jobs(view_name)
    except JenkinsError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    return {"view": view_name, "jobs": jobs}


@router.get("/jenkins/jobs/{job_name:path}")
def get_jenkins_job(job_name: str, current_user=Depends(get_current_user), db: Session = Depends(get_db)):
    client = _require_client(db, current_user.id)
    try:
        return client.get_job(job_name)
    except JenkinsError as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@router.post("/jenkins/jobs/{job_name:path}/build")
def trigger_jenkins_job(
    job_name: str,
    payload: JenkinsBuildPayload | None = None,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    client = _require_client(db, current_user.id)
    params = payload.params if payload else None
    try:
        queue_url = client.trigger_build(job_name, params=params)
    except JenkinsError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    return {
        "ok": True,
        "message": f"已触发 Job「{job_name}」构建",
        "queue_item_url": queue_url,
        "triggered_by": current_user.shown_name,
    }


@router.get("/jenkins/queue")
def get_jenkins_queue_item(
    url: str = Query(..., description="触发返回的 queue item URL"),
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    client = _require_client(db, current_user.id)
    try:
        data = client.get_queue_item(url)
    except JenkinsError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    executable = data.get("executable") if isinstance(data, dict) else None
    build_number = executable.get("number") if isinstance(executable, dict) else None
    build_url = executable.get("url") if isinstance(executable, dict) else None
    return {
        "cancelled": bool(data.get("cancelled")) if isinstance(data, dict) else False,
        "why": data.get("why") if isinstance(data, dict) else None,
        "started": build_number is not None,
        "build_number": build_number,
        "build_url": build_url,
    }


__all__ = ["router"]
