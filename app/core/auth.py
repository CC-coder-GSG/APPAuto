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


def build_user_token(user: User) -> str:
    return create_access_token(
        {
            "sub": user.username,
            "role": user.role.value,
            "session": user.session_token,
        },
        expires_delta=timedelta(minutes=720),
    )
