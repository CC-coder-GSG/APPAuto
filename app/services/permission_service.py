from __future__ import annotations

from app.core.exceptions import PermissionDenied


def ensure_admin(user) -> None:
    if getattr(user.role, "value", user.role) != "admin":
        raise PermissionDenied()


def ensure_not_self(target_user_id: int, current_user_id: int, message: str = "不能删除当前登录用户") -> None:
    if target_user_id == current_user_id:
        raise PermissionDenied(message)
