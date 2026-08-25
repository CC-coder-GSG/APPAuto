"""
Zentao authentication service.

Responsibilities:
- Encrypt / decrypt per-user Zentao passwords stored in user_zentao_bindings.
- Fetch or refresh Zentao API tokens on behalf of the current user.
- Expose a single entry-point: get_valid_token(user_id, db) -> str | None.

Encryption scheme: Fernet (AES-128-CBC + HMAC-SHA256) from the cryptography package.
Key source: APP_ZENTAO_BINDING_SECRET env var. If unset in dev, a deterministic key is
derived from the application's SECRET_KEY so the system still works without extra config.
"""
from __future__ import annotations

import base64
import hashlib
import logging
from datetime import datetime, timezone

import httpx
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.user_zentao_binding import UserZentaoBinding
from app.utils.time_utils import utc_now_naive

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Key derivation helpers
# ---------------------------------------------------------------------------

def _get_fernet() -> Fernet:
    """Return a Fernet instance from the configured or derived key."""
    raw_secret = settings.zentao_binding_secret.strip()
    if raw_secret:
        key = raw_secret.encode()
    else:
        # Deterministic fallback: derive a valid 32-byte Fernet key from SECRET_KEY.
        # Only used in dev (no explicit ZENTAO_BINDING_SECRET).
        digest = hashlib.sha256(settings.secret_key.encode()).digest()
        key = base64.urlsafe_b64encode(digest)
    return Fernet(key)


def encrypt_password(plain: str) -> str:
    """Encrypt a plain-text password and return a Fernet token (base64 string)."""
    fernet = _get_fernet()
    return fernet.encrypt(plain.encode()).decode()


def decrypt_password(ciphertext: str) -> str | None:
    """Decrypt a Fernet token back to plain text. Returns None on failure."""
    try:
        fernet = _get_fernet()
        return fernet.decrypt(ciphertext.encode()).decode()
    except (InvalidToken, Exception):
        return None


# ---------------------------------------------------------------------------
# Token management
# ---------------------------------------------------------------------------

_TOKEN_BUFFER_SECONDS = 60  # refresh when fewer than this many seconds remain


def _is_token_valid(binding: UserZentaoBinding) -> bool:
    if not binding.token_value or not binding.token_expires_at:
        return False
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    remaining = (binding.token_expires_at - now).total_seconds()
    return remaining > _TOKEN_BUFFER_SECONDS


def _fetch_new_token(binding: UserZentaoBinding) -> tuple[str, datetime] | None:
    """
    POST /v1/tokens to Zentao and return (token_value, expires_at).
    Returns None on failure.
    """
    plain_password = decrypt_password(binding.zentao_password_ciphertext or '')
    if not plain_password:
        logger.warning("zentao_auth: cannot decrypt password for user_id=%s", binding.user_id)
        return None

    base_url = (binding.base_url or '').rstrip('/')
    url = f"{base_url}/api.php/v1/tokens"
    try:
        resp = httpx.post(
            url,
            json={"account": binding.zentao_account, "password": plain_password},
            timeout=10.0,
            headers={"Content-Type": "application/json"},
        )
        if resp.status_code not in (200, 201):
            resp.raise_for_status()
        data = resp.json()
        token = data.get("token") or data.get("data", {}).get("token") if isinstance(data, dict) else None
        if not token:
            logger.warning("zentao_auth: no token in response for user_id=%s body=%s", binding.user_id, str(data)[:200])
            return None
        # Zentao tokens typically last ~2 hours; default 7200 s if not provided
        expired_at_ts = data.get("expired") or data.get("data", {}).get("expired") if isinstance(data, dict) else None
        if expired_at_ts:
            try:
                expires_at = datetime.utcfromtimestamp(int(expired_at_ts))
            except Exception:
                expires_at = utc_now_naive().replace(microsecond=0)
                from datetime import timedelta
                expires_at += timedelta(seconds=7200)
        else:
            from datetime import timedelta
            expires_at = utc_now_naive().replace(microsecond=0) + timedelta(seconds=7200)
        return token, expires_at
    except httpx.HTTPStatusError as e:
        logger.warning("zentao_auth: HTTP error for user_id=%s status=%s", binding.user_id, e.response.status_code)
    except Exception as e:
        logger.warning("zentao_auth: error for user_id=%s: %s", binding.user_id, e)
    return None


def invalidate_token(user_id: int, db: Session) -> None:
    """
    Mark the stored token as expired so the next call to get_valid_token()
    will fetch a fresh one.  Call this when Zentao returns 401 Unauthorized.
    """
    binding = db.query(UserZentaoBinding).filter(UserZentaoBinding.user_id == user_id).first()
    if not binding:
        return
    binding.token_value = None
    binding.token_expires_at = None
    db.commit()
    logger.info("zentao_auth: token invalidated for user_id=%s (received 401)", user_id)


def get_valid_token(user_id: int, db: Session) -> str | None:
    """
    Return a valid Zentao API token for the given user, refreshing automatically if needed.

    Returns None if:
    - The user has no binding configured.
    - The binding password cannot be decrypted.
    - The token refresh request fails.
    """
    binding = db.query(UserZentaoBinding).filter(UserZentaoBinding.user_id == user_id).first()
    if not binding:
        return None

    if _is_token_valid(binding):
        return binding.token_value

    # Attempt refresh
    result = _fetch_new_token(binding)
    now = utc_now_naive().replace(microsecond=0)
    if result:
        token_value, expires_at = result
        binding.token_value = token_value
        binding.token_expires_at = expires_at
        binding.last_refresh_at = now
        binding.last_refresh_status = "ok"
        binding.last_error_message = None
        db.commit()
        return token_value
    else:
        binding.last_refresh_at = now
        binding.last_refresh_status = "error"
        binding.last_error_message = "Token refresh failed — check Zentao credentials."
        db.commit()
        return None


def test_connection(base_url: str, account: str, plain_password: str) -> tuple[bool, str]:
    """
    Test a Zentao credential by attempting to fetch a token.
    Returns (success: bool, message: str).
    """
    url = f"{base_url.rstrip('/')}/api.php/v1/tokens"
    try:
        resp = httpx.post(
            url,
            json={"account": account, "password": plain_password},
            timeout=10.0,
            headers={"Content-Type": "application/json"},
        )
        if resp.status_code in (200, 201):
            data = resp.json()
            token = data.get("token") or (data.get("data", {}) or {}).get("token")
            if token:
                return True, "连接成功，Token 已获取"
            return False, f"服务器返回 {resp.status_code} 但未找到 token，响应：{str(data)[:200]}"
        return False, f"HTTP {resp.status_code}：{resp.text[:200]}"
    except Exception as e:
        return False, f"连接异常：{e}"


__all__ = [
    "encrypt_password",
    "decrypt_password",
    "get_valid_token",
    "invalidate_token",
    "test_connection",
]
