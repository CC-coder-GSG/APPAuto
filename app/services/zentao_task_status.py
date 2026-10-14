"""Task lifecycle and independent requirement acknowledgement for Zentao IPD.

Detail rawStatus is preferred for lifecycle. Sparse legacy changed payloads
retain the existing effort/completion fallback. needConfirm and story versions
are never collapsed into that lifecycle state.
"""
from __future__ import annotations

from typing import Any


def raw_task_status(task: dict | None) -> str:
    return str((task or {}).get("status") or "").strip().lower()


def _task_left(task: dict) -> float | None:
    try:
        value = task.get("left")
        return float(value) if value is not None and str(value).strip() != "" else None
    except (TypeError, ValueError):
        return None


def _meaningful_completion_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, dict):
        return any(_meaningful_completion_value(item) for item in value.values())
    text = str(value).strip()
    return bool(text and not text.startswith("0000-00-00"))


def task_has_completion_fact(task: dict | None) -> bool:
    if not isinstance(task, dict):
        return False
    return any(
        _meaningful_completion_value(task.get(key))
        for key in ("finishedDate", "finished_date", "finishedBy", "finished_by")
    )


def task_finish_side_effect_observed(before: dict | None, *responses: dict | None) -> bool:
    """Return whether a finish attempt changed effort/remaining-time state.

    Zentao IPD can accept ``currentConsumed`` yet leave the task active.  That
    is still a successful, non-idempotent write and must not be retried through
    another transport, otherwise the same effort is recorded twice.
    """
    if not isinstance(before, dict):
        return False

    def _number(task: dict, key: str) -> float | None:
        try:
            value = task.get(key)
            return float(value) if value is not None and str(value).strip() != "" else None
        except (TypeError, ValueError):
            return None

    before_consumed = _number(before, "consumed")
    before_left = _number(before, "left")
    before_completed = task_has_completion_fact(before)

    candidates: list[dict] = []
    for response in responses:
        if not isinstance(response, dict):
            continue
        candidates.append(response)
        for key in ("data", "task"):
            nested = response.get(key)
            if isinstance(nested, dict):
                candidates.append(nested)

    for task in candidates:
        consumed = _number(task, "consumed")
        left = _number(task, "left")
        if before_consumed is not None and consumed is not None and consumed > before_consumed + 1e-9:
            return True
        if before_left is not None and left is not None and left < before_left - 1e-9:
            return True
        if not before_completed and task_has_completion_fact(task):
            return True
    return False


def effective_task_status(task: dict | None) -> str:
    """Return the status the platform should use for lifecycle decisions."""
    if not isinstance(task, dict):
        return ""
    status = raw_task_status(task)
    if status == "changed":
        original = str(task.get("rawStatus") or "").strip().lower()
        if original in {"wait", "doing", "pause", "done", "closed", "cancel"}:
            return original
        left = _task_left(task)
        if left is not None and left <= 0 and task_has_completion_fact(task):
            return "done"
    return status


def _positive_int(value: Any) -> int | None:
    try:
        number = int(value)
        return number if number > 0 else None
    except (TypeError, ValueError):
        return None


def task_story_change_pending(task: dict | None) -> bool | None:
    """Keep requirement acknowledgement separate from task completion.

    Sparse legacy payloads have no acknowledgement information. Never clear
    an existing pending cache with such a payload. IPD list rawStatus can be
    'changed' even when the detail rawStatus is 'done'.
    """
    task = task or {}
    version = _positive_int(task.get("storyVersion"))
    latest = _positive_int(task.get("latestStoryVersion"))
    if version and latest and version < latest:
        return True
    value = task.get("needConfirm", task.get("needconfirm"))
    if value is not None:
        normalized = str(value).strip().lower()
        if normalized in {"true", "1"}:
            return True
        if normalized in {"false", "0", ""}:
            return False
    if version and latest and version == latest:
        return False
    if raw_task_status(task) == "changed" and _positive_int(task.get("story") or task.get("storyID")):
        return True
    return None


def apply_task_story_change(target: Any, task: dict) -> bool:
    """Update either requirement or mirror without losing sparse cache facts."""
    from app.utils.time_utils import local_now

    values = {
        "task_story_change_pending": task_story_change_pending(task),
        "task_story_version": _positive_int(task.get("storyVersion")),
        "task_latest_story_version": _positive_int(task.get("latestStoryVersion")),
    }
    changed = False
    for key, value in values.items():
        if value is not None and getattr(target, key, None) != value:
            setattr(target, key, value)
            changed = True
    if values["task_story_change_pending"] is not None:
        target.task_change_checked_at = local_now()
    return changed


def serialize_task_story_change(target: Any) -> dict:
    checked = getattr(target, "task_change_checked_at", None)
    return {
        "task_story_change_pending": getattr(target, "task_story_change_pending", None),
        "task_story_version": getattr(target, "task_story_version", None),
        "task_latest_story_version": getattr(target, "task_latest_story_version", None),
        "task_change_checked_at": checked.isoformat() if checked else None,
    }


def ensure_no_pending_story_change(task: dict | None, cached_pending: bool | None = None) -> None:
    from fastapi import HTTPException

    pending = task_story_change_pending(task)
    if pending is True or (pending is None and cached_pending):
        raise HTTPException(status_code=409, detail="研发需求已变更，请点击任务的需求变更标签，查看内容并确认后再操作")


__all__ = [
    "effective_task_status",
    "raw_task_status",
    "task_finish_side_effect_observed",
    "task_has_completion_fact",
    "task_story_change_pending",
    "apply_task_story_change",
    "serialize_task_story_change",
    "ensure_no_pending_story_change",
]
