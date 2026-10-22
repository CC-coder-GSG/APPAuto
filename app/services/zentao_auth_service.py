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
from app.services.zentao_v2_protocol import ZentaoAPIError, decode_v2, normalize_base_url

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
_V2_REFRESH_STATUS = "ok_v2"  # Existing 'ok' tokens came from REST v1; refresh once.


def _is_token_valid(binding: UserZentaoBinding) -> bool:
    if (binding.last_refresh_status != _V2_REFRESH_STATUS
            or not binding.token_value or not binding.token_expires_at):
        return False
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    remaining = (binding.token_expires_at - now).total_seconds()
    return remaining > _TOKEN_BUFFER_SECONDS


def _fetch_new_token(binding: UserZentaoBinding) -> tuple[str, datetime] | None:
    """
    POST /v2/users/login; expiry is a local refresh deadline, not a server TTL.
    Returns None on failure.
    """
    binding.last_error_message = None
    plain_password = decrypt_password(binding.zentao_password_ciphertext or '')
    if not plain_password:
        binding.last_error_message = "无法解密已保存的禅道密码，请重新保存禅道账号绑定"
        logger.warning("zentao_auth: cannot decrypt password for user_id=%s", binding.user_id)
        return None

    try:
        base_url = normalize_base_url(binding.base_url or '')
        url = f"{base_url}/api.php/v2/users/login"
        resp = httpx.post(
            url,
            json={"account": binding.zentao_account, "password": plain_password},
            timeout=10.0,
            headers={"Content-Type": "application/json"},
            follow_redirects=False,
        )
        data = decode_v2(resp)
        token = data.get("token")
        if not isinstance(token, str) or not token.strip():
            binding.last_error_message = "禅道登录成功但未返回有效 Token，请稍后重试"
            logger.warning("zentao_auth: no token in v2 response for user_id=%s", binding.user_id)
            return None
        # v2 documents no expiry field. Use a conservative local refresh budget;
        # upstream 401 is still handled independently by business retry paths.
        from datetime import timedelta
        expires_at = utc_now_naive().replace(microsecond=0) + timedelta(seconds=3600)
        return token, expires_at
    except ZentaoAPIError as e:
        if e.status_code == 401:
            binding.last_error_message = "禅道登录认证失败（HTTP 401），请检查已保存的账号和密码"
        elif e.status_code == 403:
            binding.last_error_message = "禅道拒绝登录（HTTP 403），请检查账号权限或登录限制"
        elif e.status_code >= 500:
            binding.last_error_message = f"禅道登录接口暂时不可用（HTTP {e.status_code}），请稍后重试"
        else:
            binding.last_error_message = f"禅道登录请求失败（HTTP {e.status_code}），请检查地址及账号配置"
        logger.warning("zentao_auth: login failed for user_id=%s status=%s", binding.user_id, e.status_code)
    except httpx.TimeoutException:
        binding.last_error_message = "连接禅道登录接口超时，请稍后重试"
        logger.warning("zentao_auth: login timed out for user_id=%s", binding.user_id)
    except httpx.RequestError:
        binding.last_error_message = "无法连接禅道登录接口，请检查网络后重试"
        logger.warning("zentao_auth: login connection failed for user_id=%s", binding.user_id)
    except httpx.HTTPStatusError as e:
        binding.last_error_message = f"禅道登录请求失败（HTTP {e.response.status_code}），请稍后重试"
        logger.warning("zentao_auth: HTTP error for user_id=%s status=%s", binding.user_id, e.response.status_code)
    except Exception as e:
        binding.last_error_message = "禅道 Token 刷新异常，请稍后重试"
        logger.warning("zentao_auth: error for user_id=%s type=%s", binding.user_id, type(e).__name__)
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
        binding.last_refresh_status = _V2_REFRESH_STATUS
        binding.last_error_message = None
        db.commit()
        return token_value
    else:
        binding.last_refresh_at = now
        binding.last_refresh_status = "error"
        binding.last_error_message = binding.last_error_message or "Token 刷新失败，请稍后重试或检查禅道账号配置"
        db.commit()
        return None


def test_connection(base_url: str, account: str, plain_password: str) -> tuple[bool, str]:
    """
    Test a Zentao credential by attempting to fetch a token.
    Returns (success: bool, message: str).
    """
    try:
        url = f"{normalize_base_url(base_url)}/api.php/v2/users/login"
        resp = httpx.post(
            url,
            json={"account": account, "password": plain_password},
            timeout=10.0,
            headers={"Content-Type": "application/json"},
            follow_redirects=False,
        )
        data = decode_v2(resp)
        if isinstance(data.get("token"), str) and data["token"].strip():
            return True, "连接成功，v2 Token 已获取"
        return False, "禅道 v2 登录未返回 Token"
    except Exception as e:
        return False, f"连接异常：{e}"


__all__ = [
    "encrypt_password",
    "decrypt_password",
    "get_valid_token",
    "invalidate_token",
    "test_connection",
]
