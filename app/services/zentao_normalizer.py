"""
Zentao API response normalizer.

Converts raw Zentao v1 API responses to flat, UI-friendly dicts.
These dicts are returned by the hydration proxy endpoints and consumed
by the frontend zentao-hydrator.js module.
"""
from __future__ import annotations

from typing import Any


# ---------------------------------------------------------------------------
# Bug normalization
# ---------------------------------------------------------------------------

_BUG_STATUS_ZH: dict[str, str] = {
    "active": "激活",
    "resolved": "已修复",
    "closed": "已关闭",
    "notrepro": "无法重现",
}

_BUG_RESOLUTION_ZH: dict[str, str] = {
    "fixed": "修复",
    "duplicate": "重复",
    "notrepro": "无法重现",
    "bydesign": "设计如此",
    "external": "外部原因",
    "notabug": "非缺陷",
    "postponed": "推迟",
    "wontfix": "不予修复",
}

_SEV_COLORS: dict[str | int, str] = {
    1: "#dc2626",
    2: "#ea580c",
    3: "#ca8a04",
    4: "#64748b",
}


def normalize_bug(raw: dict) -> dict:
    """
    Normalize a raw Zentao bug API response.

    Zentao v1 wraps the resource: {"bug": {...}} or returns the object directly.
    """
    if not raw or not isinstance(raw, dict):
        return {}

    bug = raw.get("bug") or raw
    if not isinstance(bug, dict):
        return {}

    status = bug.get("status") or ""
    resolution = bug.get("resolution") or ""
    severity = bug.get("severity")

    try:
        sev_int = int(severity)
    except (TypeError, ValueError):
        sev_int = 0

    return {
        "id": bug.get("id"),
        "title": bug.get("title") or "",
        "status": status,
        "status_zh": _BUG_STATUS_ZH.get(status, status),
        "resolution": resolution,
        "resolution_zh": _BUG_RESOLUTION_ZH.get(resolution, resolution) if resolution else "",
        "severity": sev_int,
        "sev_color": _SEV_COLORS.get(sev_int, "#64748b"),
        "pri": bug.get("pri"),
        "assigned_to": _extract_person_name(bug.get("assignedTo")),
        "opened_by": _extract_person_name(bug.get("openedBy")),
        "build": _extract_build_name(bug.get("openedBuild") or bug.get("foundBuild")),
        "resolved_build": _extract_build_name(bug.get("resolvedBuild")),
        "closed_date": bug.get("closedDate") or bug.get("resolvedDate") or "",
    }


_BUG_TYPE_ZH: dict[str, str] = {
    "codeerror": "代码错误",
    "config": "配置相关",
    "install": "安装部署",
    "security": "安全相关",
    "performance": "性能问题",
    "standard": "标准规范",
    "automation": "测试脚本",
    "designchange": "设计变更",
    "others": "其他",
}


_BUG_ACTION_ZH: dict[str, str] = {
    "opened": "提交",
    "created": "提交",
    "confirmed": "确认",
    "assigned": "指派",
    "resolved": "解决",
    "closed": "关闭",
    "activated": "激活",
    "edited": "编辑",
    "commented": "备注",
}


def normalize_bug_detail(raw: dict, base_url: str = "") -> dict:
    """Normalize a full Zentao bug payload for preview dialogs."""
    if not raw or not isinstance(raw, dict):
        return {}

    bug = raw.get("bug") or raw
    if not isinstance(bug, dict):
        return {}

    bug_id = bug.get("id")
    status = str(bug.get("status") or "")
    resolution = str(bug.get("resolution") or "")
    bug_type = str(bug.get("type") or "")
    severity = _safe_int(bug.get("severity"))
    pri = _safe_int(bug.get("pri"))

    files = []
    raw_files = bug.get("files") or []
    if isinstance(raw_files, dict):
        raw_files = raw_files.values()
    for item in raw_files:
        if not isinstance(item, dict):
            continue
        file_url = _normalize_zentao_url(
            base_url,
            item.get("url") or item.get("downloadUrl") or item.get("webPath") or item.get("pathname") or "",
        )
        extension = str(item.get("extension") or item.get("ext") or "").strip().lower()
        files.append(
            {
                "title": item.get("title") or item.get("name") or "",
                "url": file_url,
                "extension": extension,
                "is_image": _is_image_file(
                    item.get("title") or item.get("name") or "",
                    extension=extension,
                    mime=item.get("mimeType") or item.get("mime"),
                ),
            }
        )

    actions = []
    raw_actions = bug.get("actions") or []
    if isinstance(raw_actions, dict):
        raw_actions = raw_actions.values()
    for item in raw_actions:
        if not isinstance(item, dict):
            continue
        action_code = str(item.get("action") or item.get("objectType") or "").lower()
        actions.append(
            {
                "date": item.get("date") or item.get("createdDate") or "",
                "actor": _extract_person_name(item.get("actor") or item.get("actionBy") or item.get("createdBy")),
                "action": action_code,
                "action_zh": _BUG_ACTION_ZH.get(action_code, action_code or "操作"),
                "comment": item.get("comment") or item.get("extra") or "",
            }
        )

    zentao_url = ""
    if base_url and bug_id:
        zentao_url = f"{base_url.rstrip('/')}/bug-view-{bug_id}.html"

    return {
        "id": bug_id,
        "title": bug.get("title") or "",
        "status": status,
        "status_zh": _BUG_STATUS_ZH.get(status, status),
        "resolution": resolution,
        "resolution_zh": _BUG_RESOLUTION_ZH.get(resolution, resolution) if resolution else "",
        "severity": severity,
        "pri": pri,
        "type": bug_type,
        "type_zh": _BUG_TYPE_ZH.get(bug_type, bug_type),
        "os": _extract_simple_value(bug.get("os")),
        "browser": _extract_simple_value(bug.get("browser")),
        "module": _extract_simple_value(bug.get("moduleTitle") or bug.get("module")),
        "execution": _extract_simple_value(bug.get("executionName") or bug.get("execution")),
        "story_title": _extract_simple_value(bug.get("storyTitle") or bug.get("story")),
        "project": _extract_simple_value(bug.get("projectName") or bug.get("project")),
        "opened_by": _extract_person_name(bug.get("openedBy")),
        "opened_date": bug.get("openedDate") or "",
        "assigned_to": _extract_person_name(bug.get("assignedTo")),
        "deadline": bug.get("deadline") or "",
        "resolved_by": _extract_person_name(bug.get("resolvedBy")),
        "resolved_date": bug.get("resolvedDate") or "",
        "closed_by": _extract_person_name(bug.get("closedBy")),
        "closed_date": bug.get("closedDate") or "",
        "opened_build": _extract_build_name(bug.get("openedBuild") or bug.get("foundBuild")),
        "resolved_build": _extract_build_name(bug.get("resolvedBuild")),
        "steps": bug.get("steps") or "",
        "keywords": _extract_simple_value(bug.get("keywords")),
        "zentao_url": zentao_url,
        "files": files,
        "actions": actions,
    }


# ---------------------------------------------------------------------------
# Story / requirement normalization
# ---------------------------------------------------------------------------

_STORY_STATUS_ZH: dict[str, str] = {
    "draft": "草稿",
    "active": "激活",
    "closed": "已关闭",
    "changing": "变更中",
}

_STORY_STAGE_ZH: dict[str, str] = {
    "wait": "待开发",
    "planned": "已计划",
    "projected": "已立项",
    "developing": "开发中",
    "developed": "开发完成",
    "testing": "测试中",
    "tested": "测试完成",
    "verified": "已验收",
    "released": "已发布",
}


def normalize_story(raw: dict) -> dict:
    """
    Normalize a raw Zentao story (requirement) API response.
    """
    if not raw or not isinstance(raw, dict):
        return {}

    story = raw.get("story") or raw
    if not isinstance(story, dict):
        return {}

    status = story.get("status") or ""
    stage = story.get("stage") or ""

    return {
        "id": story.get("id"),
        "title": story.get("title") or "",
        "status": status,
        "status_zh": _STORY_STATUS_ZH.get(status, status),
        "stage": stage,
        "stage_zh": _STORY_STAGE_ZH.get(stage, stage),
        "pri": story.get("pri"),
        "assigned_to": _extract_person_name(story.get("assignedTo")),
        "plan": _extract_plan_name(story.get("plan")),
        "estimate": story.get("estimate"),
        "consumed": story.get("consumed"),
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _extract_person_name(val: Any) -> str:
    """
    Zentao person fields may be a dict {"realname": "…", "account": "…"} or a string.
    """
    if not val:
        return ""
    if isinstance(val, dict):
        return val.get("realname") or val.get("account") or ""
    return str(val)


def _extract_build_name(val: Any) -> str:
    """
    openedBuild / resolvedBuild can be a build number string or a dict.
    """
    if not val:
        return ""
    if isinstance(val, dict):
        return val.get("name") or val.get("title") or ", ".join(str(v) for v in val.values() if v)
    if isinstance(val, list):
        return ", ".join(_extract_build_name(item) for item in val if item)
    return str(val)


def _extract_plan_name(val: Any) -> str:
    """
    plan can be a dict of {plan_id: plan_title} or a single value.
    """
    if not val:
        return ""
    if isinstance(val, dict):
        # May be {id: name} mapping
        parts = [str(v) for v in val.values() if v]
        return ", ".join(parts) if parts else ""
    return str(val)


def _extract_simple_value(val: Any) -> str:
    if not val:
        return ""
    if isinstance(val, dict):
        return val.get("name") or val.get("title") or val.get("realname") or val.get("account") or ", ".join(str(v) for v in val.values() if v)
    if isinstance(val, list):
        return ", ".join(_extract_simple_value(item) for item in val if item)
    return str(val)


def _safe_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _normalize_zentao_url(base_url: str, raw_url: Any) -> str:
    text = str(raw_url or "").strip()
    if not text:
        return ""
    if text.startswith(("http://", "https://")):
        return text
    if not base_url:
        return text
    if text.startswith("/"):
        return f"{base_url.rstrip('/')}{text}"
    return f"{base_url.rstrip('/')}/{text.lstrip('/')}"


def _is_image_file(name: Any, *, extension: str = "", mime: Any = None) -> bool:
    mime_text = str(mime or "").strip().lower()
    if mime_text.startswith("image/"):
        return True
    ext = str(extension or "").strip().lower().lstrip(".")
    if not ext:
        text = str(name or "").strip().lower()
        if "." in text:
            ext = text.rsplit(".", 1)[-1]
    return ext in {"png", "jpg", "jpeg", "gif", "bmp", "webp", "svg"}


__all__ = ["normalize_bug", "normalize_bug_detail", "normalize_story"]
