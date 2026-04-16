from __future__ import annotations

import json

from app.core.exceptions import PermissionDenied

ALL_TAB_KEYS = [
    "assign",
    "mine",
    "feedback",
    "retest",
    "stage5",
    "field-test",
    "build-records",
    "zentao-sync",
    "report",
    "activity",
    "data",
    "dispatch",
]

DEFAULT_USER_TAB_KEYS = [
    "mine",
    "feedback",
    "retest",
    "stage5",
    "field-test",
    "zentao-sync",
    "report",
]


def ensure_admin(user) -> None:
    if getattr(user.role, "value", user.role) != "admin":
        raise PermissionDenied()


def ensure_not_self(target_user_id: int, current_user_id: int, message: str = "不能删除当前登录用户") -> None:
    if target_user_id == current_user_id:
        raise PermissionDenied(message)


def normalize_tab_permissions(tab_keys: list[str] | None) -> list[str]:
    if not tab_keys:
        return []
    normalized: list[str] = []
    seen: set[str] = set()
    for key in tab_keys:
        text = str(key or "").strip()
        if not text or text not in ALL_TAB_KEYS or text in seen:
            continue
        normalized.append(text)
        seen.add(text)
    return normalized


def get_allowed_tabs(user) -> list[str]:
    role_value = getattr(user.role, "value", user.role)
    if role_value == "admin":
        return list(ALL_TAB_KEYS)

    raw = getattr(user, "tab_permissions", None)
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return normalize_tab_permissions(parsed)
        except Exception:
            pass
    return list(DEFAULT_USER_TAB_KEYS)


def serialize_tab_permissions(tab_keys: list[str] | None) -> str:
    return json.dumps(normalize_tab_permissions(tab_keys), ensure_ascii=False)


def has_tab_access(user, tab_key: str) -> bool:
    return tab_key in get_allowed_tabs(user)


def ensure_tab_access(user, tab_key: str, message: str | None = None) -> None:
    if not has_tab_access(user, tab_key):
        raise PermissionDenied(message or "无权限访问该页面")

