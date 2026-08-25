"""
Jenkins integration service.

- 管理每个用户的 Jenkins 账号绑定（base_url + account + 加密 API Token）。
- 用绑定为当前用户构造 JenkinsClient，代为列 Job / 触发构建 / 查状态。

API Token 的加解密复用禅道绑定那套 Fernet 工具（通用对称加密，与禅道无耦合），
密钥来源同为 APP_ZENTAO_BINDING_SECRET，缺省在 dev 下由 SECRET_KEY 派生。
"""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.models.user_jenkins_binding import UserJenkinsBinding
from app.services.jenkins_client import JenkinsClient, JenkinsError
from app.services.zentao_auth_service import decrypt_password, encrypt_password
from app.utils.time_utils import utc_now_naive

logger = logging.getLogger(__name__)


def get_binding(db: Session, user_id: int) -> UserJenkinsBinding | None:
    return db.query(UserJenkinsBinding).filter(UserJenkinsBinding.user_id == user_id).first()


def encrypt_token(plain_token: str) -> str:
    return encrypt_password(plain_token)


def upsert_binding(db: Session, user_id: int, *, base_url: str, account: str, token: str) -> UserJenkinsBinding:
    binding = get_binding(db, user_id)
    ciphertext = encrypt_token(token)
    if binding:
        binding.base_url = base_url.rstrip("/")
        binding.jenkins_account = account
        binding.jenkins_token_ciphertext = ciphertext
        binding.last_check_status = None
        binding.last_error_message = None
    else:
        binding = UserJenkinsBinding(
            user_id=user_id,
            base_url=base_url.rstrip("/"),
            jenkins_account=account,
            jenkins_token_ciphertext=ciphertext,
        )
        db.add(binding)
    db.commit()
    db.refresh(binding)
    return binding


def delete_binding(db: Session, user_id: int) -> bool:
    binding = get_binding(db, user_id)
    if not binding:
        return False
    db.delete(binding)
    db.commit()
    return True


def get_client_for_user(db: Session, user_id: int) -> JenkinsClient | None:
    """Build a JenkinsClient from the user's stored binding, or None if unusable."""
    creds = get_binding_creds(db, user_id)
    if not creds:
        return None
    base_url, account, token = creds
    return JenkinsClient(base_url, account, token)


def get_binding_creds(db: Session, user_id: int) -> tuple[str, str, str] | None:
    """
    Return (base_url, account, plaintext_token) for the user's Jenkins binding,
    or None if missing / undecryptable. Used by the reverse proxy and artifact
    download endpoints which need raw HTTP access (not the high-level client).
    """
    binding = get_binding(db, user_id)
    if not binding or not binding.base_url or not binding.jenkins_token_ciphertext:
        return None
    token = decrypt_password(binding.jenkins_token_ciphertext)
    if not token:
        logger.warning("jenkins: cannot decrypt token for user_id=%s", user_id)
        return None
    return binding.base_url.rstrip("/"), binding.jenkins_account, token


def record_check_result(db: Session, user_id: int, *, ok: bool, message: str | None) -> None:
    binding = get_binding(db, user_id)
    if not binding:
        return
    binding.last_check_at = utc_now_naive().replace(microsecond=0)
    binding.last_check_status = "ok" if ok else "error"
    binding.last_error_message = None if ok else (message or "连接失败")[:500]
    db.commit()


def test_credentials(base_url: str, account: str, token: str) -> tuple[bool, str]:
    """Probe a Jenkins credential without persisting it."""
    client = JenkinsClient(base_url, account, token)
    try:
        info = client.whoami()
        name = info.get("fullName") or info.get("id") or account
        return True, f"连接成功，当前 Jenkins 用户：{name}"
    except JenkinsError as exc:
        return False, str(exc)
    except Exception as exc:  # noqa: BLE001
        return False, f"连接异常：{exc}"


__all__ = [
    "get_binding",
    "upsert_binding",
    "delete_binding",
    "get_client_for_user",
    "get_binding_creds",
    "record_check_result",
    "test_credentials",
]
