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
        return val.get("name") or val.get("title") or str(next(iter(val.values()), ""))
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


__all__ = ["normalize_bug", "normalize_story"]
