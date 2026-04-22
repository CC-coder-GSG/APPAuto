"""Per-user AI provider (DeepSeek etc.) API Key configuration.

GET  /user/ai-config           — current-user config (masked only, never plaintext)
POST /user/ai-config           — save or replace the current-user's API Key
POST /user/ai-config/validate  — probe the upstream provider (uses inline key if
                                 provided; otherwise the stored one)
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.schemas.user_ai_config import (
    UserAIConfigOut,
    UserAIConfigUpsert,
    UserAIConfigValidateRequest,
    UserAIConfigValidateResponse,
)
from app.services import user_ai_config_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/user/ai-config", tags=["user_ai_config"])


@router.get("", response_model=UserAIConfigOut)
def get_my_ai_config(
    provider_name: str = "deepseek",
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    cfg = user_ai_config_service.get_user_ai_config(db, current_user.id, provider_name)
    return user_ai_config_service.to_public_dict(cfg, provider_name)


@router.post("", response_model=UserAIConfigOut)
def upsert_my_ai_config(
    payload: UserAIConfigUpsert,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        cfg = user_ai_config_service.save_user_ai_config(
            db,
            user_id=current_user.id,
            provider_name=payload.provider_name,
            api_key=payload.api_key,
            is_enabled=payload.is_enabled,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    logger.info(
        "user_ai_config: saved user_id=%s provider=%s masked=%s",
        current_user.id,
        cfg.provider_name,
        cfg.api_key_masked,
    )
    return user_ai_config_service.to_public_dict(cfg, cfg.provider_name)


@router.post("/validate", response_model=UserAIConfigValidateResponse)
def validate_my_ai_config(
    payload: UserAIConfigValidateRequest,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    inline_key = (payload.api_key or "").strip() if payload.api_key else ""
    if inline_key:
        key_to_check = inline_key
    else:
        key_to_check = user_ai_config_service.get_plaintext_api_key(
            db, current_user.id, payload.provider_name
        ) or ""
        if not key_to_check:
            return UserAIConfigValidateResponse(
                ok=False,
                message="尚未配置 API Key，无法校验",
                provider_name=payload.provider_name,
            )

    ok, message = user_ai_config_service.validate_api_key(payload.provider_name, key_to_check)
    logger.info(
        "user_ai_config.validate user_id=%s provider=%s ok=%s",
        current_user.id,
        payload.provider_name,
        ok,
    )
    return UserAIConfigValidateResponse(
        ok=ok, message=message, provider_name=payload.provider_name
    )
