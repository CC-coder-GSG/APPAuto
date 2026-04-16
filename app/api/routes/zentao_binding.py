"""
Zentao account binding API.

Allows each authenticated user to manage their own Zentao account binding:
- GET  /api/zentao-binding/me         — view current binding (password never returned)
- PUT  /api/zentao-binding/me         — create or update binding
- POST /api/zentao-binding/me/test    — test connection without saving
- POST /api/zentao-binding/me/refresh — force refresh the stored token
- DELETE /api/zentao-binding/me       — remove binding
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.models.user_zentao_binding import UserZentaoBinding
from app.services.zentao_auth_service import (
    decrypt_password,
    encrypt_password,
    get_valid_token,
    test_connection,
)

router = APIRouter()


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class ZentaoBindingUpdatePayload(BaseModel):
    base_url: str = Field(min_length=1, max_length=500, description="禅道地址，如 http://192.168.2.148:81/zentao")
    zentao_account: str = Field(min_length=1, max_length=120)
    zentao_password: str = Field(min_length=1, max_length=256, description="明文密码，服务端加密存储")


class ZentaoBindingTestPayload(BaseModel):
    base_url: str = Field(min_length=1, max_length=500)
    zentao_account: str = Field(min_length=1, max_length=120)
    zentao_password: str = Field(min_length=1, max_length=256)


def _utc_iso(dt: datetime | None) -> str | None:
    """Return an ISO-8601 string with explicit 'Z' suffix so JS parses it as UTC."""
    if dt is None:
        return None
    return dt.strftime('%Y-%m-%dT%H:%M:%SZ')


def _serialize_binding(b: UserZentaoBinding) -> dict:
    return {
        "id": b.id,
        "user_id": b.user_id,
        "base_url": b.base_url,
        "zentao_account": b.zentao_account,
        "has_password": bool(b.zentao_password_ciphertext),
        "has_token": bool(b.token_value),
        "token_expires_at": _utc_iso(b.token_expires_at),
        "last_refresh_at": _utc_iso(b.last_refresh_at),
        "last_refresh_status": b.last_refresh_status,
        "last_error_message": b.last_error_message,
        "created_at": _utc_iso(b.created_at),
        "updated_at": _utc_iso(b.updated_at),
    }


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/zentao-binding/me")
def get_my_binding(current_user=Depends(get_current_user), db: Session = Depends(get_db)):
    """Return the current user's Zentao binding info (never returns password)."""
    binding = db.query(UserZentaoBinding).filter(UserZentaoBinding.user_id == current_user.id).first()
    if not binding:
        return {"binding": None}
    return {"binding": _serialize_binding(binding)}


@router.put("/zentao-binding/me")
def upsert_my_binding(
    payload: ZentaoBindingUpdatePayload,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Create or update the current user's Zentao account binding."""
    binding = db.query(UserZentaoBinding).filter(UserZentaoBinding.user_id == current_user.id).first()
    ciphertext = encrypt_password(payload.zentao_password)

    if binding:
        binding.base_url = payload.base_url.rstrip('/')
        binding.zentao_account = payload.zentao_account
        binding.zentao_password_ciphertext = ciphertext
        binding.zentao_password_iv = None  # embedded in Fernet token
        # Clear stale token when credentials change
        binding.token_value = None
        binding.token_expires_at = None
        binding.last_refresh_status = None
        binding.last_error_message = None
    else:
        binding = UserZentaoBinding(
            user_id=current_user.id,
            base_url=payload.base_url.rstrip('/'),
            zentao_account=payload.zentao_account,
            zentao_password_ciphertext=ciphertext,
        )
        db.add(binding)

    db.commit()
    db.refresh(binding)
    return {"ok": True, "binding": _serialize_binding(binding)}


@router.post("/zentao-binding/me/test")
def test_my_binding(
    payload: ZentaoBindingTestPayload,
    current_user=Depends(get_current_user),
):
    """Test a Zentao credential without saving it."""
    success, message = test_connection(payload.base_url, payload.zentao_account, payload.zentao_password)
    return {"ok": success, "message": message}


@router.post("/zentao-binding/me/refresh")
def refresh_my_token(current_user=Depends(get_current_user), db: Session = Depends(get_db)):
    """Force refresh the stored Zentao token for the current user."""
    binding = db.query(UserZentaoBinding).filter(UserZentaoBinding.user_id == current_user.id).first()
    if not binding:
        raise HTTPException(status_code=404, detail="当前用户未配置禅道绑定")

    # Force expiry to trigger refresh
    binding.token_expires_at = None
    db.commit()

    token = get_valid_token(current_user.id, db)
    db.refresh(binding)
    if token:
        return {"ok": True, "message": "Token 刷新成功", "binding": _serialize_binding(binding)}
    else:
        return {
            "ok": False,
            "message": binding.last_error_message or "Token 刷新失败，请检查禅道账号配置",
            "binding": _serialize_binding(binding),
        }


@router.delete("/zentao-binding/me")
def delete_my_binding(current_user=Depends(get_current_user), db: Session = Depends(get_db)):
    """Remove the current user's Zentao binding."""
    binding = db.query(UserZentaoBinding).filter(UserZentaoBinding.user_id == current_user.id).first()
    if not binding:
        raise HTTPException(status_code=404, detail="当前用户未配置禅道绑定")
    db.delete(binding)
    db.commit()
    return {"ok": True, "message": "禅道绑定已删除"}
