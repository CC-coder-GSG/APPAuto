"""
Per-user AI provider (DeepSeek etc.) API key management.

Encryption scheme: Fernet (AES-128-CBC + HMAC-SHA256), same as zentao_auth_service.
Key source (in order): APP_USER_AI_CONFIG_SECRET → APP_ZENTAO_BINDING_SECRET → SHA-256 of SECRET_KEY.
This lets operators use a dedicated key, or just reuse the existing Zentao binding
key, or — in dev — let the system derive a stable key automatically.

No function in this module ever logs a plaintext api_key.
"""
from __future__ import annotations

import base64
import hashlib
import logging
import os
from typing import Any

import httpx
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.user_ai_provider_config import UserAIProviderConfig

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Encryption helpers
# ---------------------------------------------------------------------------

def _get_fernet() -> Fernet:
    explicit = (os.getenv("APP_USER_AI_CONFIG_SECRET") or os.getenv("USER_AI_CONFIG_SECRET") or "").strip()
    if explicit:
        return Fernet(explicit.encode())
    binding_secret = (settings.zentao_binding_secret or "").strip()
    if binding_secret:
        return Fernet(binding_secret.encode())
    digest = hashlib.sha256(settings.secret_key.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def _encrypt(plain: str) -> str:
    return _get_fernet().encrypt(plain.encode()).decode()


def _decrypt(ciphertext: str) -> str | None:
    if not ciphertext:
        return None
    try:
        return _get_fernet().decrypt(ciphertext.encode()).decode()
    except (InvalidToken, Exception):
        return None


def _mask(api_key: str) -> str:
    """Build a display form that never reveals the middle section."""
    if not api_key:
        return ""
    key = api_key.strip()
    if len(key) <= 8:
        return key[:2] + "***"
    head = key[:6]
    tail = key[-4:]
    return f"{head}********{tail}"


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

def get_user_ai_config(
    db: Session, user_id: int, provider_name: str = "deepseek"
) -> UserAIProviderConfig | None:
    return (
        db.query(UserAIProviderConfig)
        .filter(
            UserAIProviderConfig.user_id == user_id,
            UserAIProviderConfig.provider_name == provider_name,
        )
        .first()
    )


def save_user_ai_config(
    db: Session,
    *,
    user_id: int,
    provider_name: str,
    api_key: str,
    is_enabled: bool = True,
) -> UserAIProviderConfig:
    key = (api_key or "").strip()
    if not key:
        raise ValueError("api_key 不能为空")

    cfg = get_user_ai_config(db, user_id, provider_name)
    encrypted = _encrypt(key)
    masked = _mask(key)

    if cfg is None:
        cfg = UserAIProviderConfig(
            user_id=user_id,
            provider_name=provider_name or "deepseek",
            api_key_encrypted=encrypted,
            api_key_masked=masked,
            is_enabled=bool(is_enabled),
        )
        db.add(cfg)
    else:
        cfg.api_key_encrypted = encrypted
        cfg.api_key_masked = masked
        cfg.is_enabled = bool(is_enabled)

    db.commit()
    db.refresh(cfg)
    return cfg


def get_plaintext_api_key(
    db: Session, user_id: int, provider_name: str = "deepseek"
) -> str | None:
    """Decrypt and return the caller's own plaintext key, or None if missing/broken.

    ONLY call this from trusted service code that needs to forward the key to an
    upstream service. Never include the return value in logs or API responses.
    """
    cfg = get_user_ai_config(db, user_id, provider_name)
    if not cfg or not cfg.is_enabled or not cfg.api_key_encrypted:
        return None
    return _decrypt(cfg.api_key_encrypted)


def to_public_dict(cfg: UserAIProviderConfig | None, provider_name: str = "deepseek") -> dict[str, Any]:
    if cfg is None:
        return {
            "provider_name": provider_name,
            "is_enabled": False,
            "configured": False,
            "api_key_masked": None,
            "updated_at": None,
        }
    return {
        "provider_name": cfg.provider_name,
        "is_enabled": bool(cfg.is_enabled),
        "configured": bool(cfg.api_key_encrypted),
        "api_key_masked": cfg.api_key_masked,
        "updated_at": cfg.updated_at,
    }


# ---------------------------------------------------------------------------
# Upstream validation
# ---------------------------------------------------------------------------

_DEEPSEEK_MODELS_URL = "https://api.deepseek.com/v1/models"


def validate_api_key(provider_name: str, api_key: str) -> tuple[bool, str]:
    """Check that the key is accepted by the upstream provider.

    Returns (ok, message). Never logs the api_key.
    """
    key = (api_key or "").strip()
    if not key:
        return False, "API Key 为空"

    provider = (provider_name or "deepseek").lower()
    if provider == "deepseek":
        return _validate_deepseek(key)
    return True, "暂未对该 provider 做校验，仅保存"


def _validate_deepseek(api_key: str) -> tuple[bool, str]:
    try:
        resp = httpx.get(
            _DEEPSEEK_MODELS_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=10.0,
        )
    except httpx.TimeoutException:
        return False, "连接 DeepSeek 超时"
    except httpx.HTTPError as exc:
        return False, f"连接 DeepSeek 失败：{exc.__class__.__name__}"

    if resp.status_code == 200:
        return True, "API Key 有效"
    if resp.status_code == 401:
        return False, "DeepSeek 拒绝该 API Key（401 Unauthorized）"
    if resp.status_code == 403:
        return False, "DeepSeek 返回 403（Key 可能无权限访问 /models）"
    snippet = resp.text[:200] if resp.text else ""
    return False, f"DeepSeek 返回 HTTP {resp.status_code}：{snippet}"


__all__ = [
    "get_user_ai_config",
    "save_user_ai_config",
    "get_plaintext_api_key",
    "to_public_dict",
    "validate_api_key",
]
