from __future__ import annotations

import json

from app.core.exceptions import PermissionDenied

# Canonical tab keys. "overall-test" is the new name for the tab
# historically known as "stage5"; the legacy key is preserved as an
# alias in `_TAB_KEY_ALIASES` so persisted permissions keep working
# without a data migration.
ALL_TAB_KEYS = [
    "assign",
    "mine",
    "task-board",
    "feedback",
    "retest",
    "overall-test",
    "field-test",
    "build-records",
    "testcase-center",
    "report",
    "activity",
    "data",
    "dispatch",
    "zentao-ai",
    "cad-test",
    "terminal",
]

DEFAULT_USER_TAB_KEYS = [
    "mine",
    "task-board",
    "feedback",
    "retest",
    "overall-test",
    "field-test",
    "testcase-center",
    "report",
]

# Legacy → canonical aliases applied when reading persisted permissions.
_TAB_KEY_ALIASES = {
    "stage5": "overall-test",
}


def _canonical_tab_key(raw: str) -> str:
    text = str(raw or "").strip()
    return _TAB_KEY_ALIASES.get(text, text)


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
        canonical = _canonical_tab_key(key)
        if not canonical or canonical not in ALL_TAB_KEYS or canonical in seen:
            continue
        normalized.append(canonical)
        seen.add(canonical)
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
    return _canonical_tab_key(tab_key) in get_allowed_tabs(user)


def ensure_tab_access(user, tab_key: str, message: str | None = None) -> None:
    if not has_tab_access(user, tab_key):
        raise PermissionDenied(message or "无权限访问该页面")
