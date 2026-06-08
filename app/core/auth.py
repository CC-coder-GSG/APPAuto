from __future__ import annotations

from datetime import timedelta

from sqlalchemy.orm import Session

from app.core.security import create_access_token, verify_password
from app.models import User


def authenticate_user(db: Session, username: str, password: str) -> User | None:
    user = db.query(User).filter(User.username == username).first()
    if not user:
        return None
    if not verify_password(password, user.password_hash):
        return None
    return user


# 支持的客户端类型。"web"=桌面浏览器（默认/旧版兼容），"mobile"=移动端 App。
# 不同类型各自持有独立的 session，从而"不同终端可并存、相同终端互斥登录"。
CLIENT_WEB = "web"
CLIENT_MOBILE = "mobile"


def normalize_client_type(value: str | None) -> str:
    return CLIENT_MOBILE if (value or "").strip().lower() == CLIENT_MOBILE else CLIENT_WEB


def session_token_for(user: User, client_type: str) -> str | None:
    """返回该用户在指定客户端类型下当前有效的 session token。"""
    if client_type == CLIENT_MOBILE:
        return user.session_token_mobile
    return user.session_token


def build_user_token(user: User, client_type: str = CLIENT_WEB) -> str:
    return create_access_token(
        {
            "sub": user.username,
            "role": user.role.value,
            "session": session_token_for(user, client_type),
            "client": client_type,
        },
        expires_delta=timedelta(minutes=720),
    )
