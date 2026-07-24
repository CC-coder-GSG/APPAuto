"""Derive the business lifecycle state from Zentao's task payload.

The customized ipd4.3 deployment keeps ``status=changed`` across both
reactivation and completion.  ``left`` plus a completion fact disambiguates
the two states:

* changed + left > 0: active after restart
* changed + left <= 0 + finishedDate/finishedBy: completed
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


def effective_task_status(task: dict | None) -> str:
    """Return the status the platform should use for lifecycle decisions."""
    if not isinstance(task, dict):
        return ""
    status = raw_task_status(task)
    if status == "changed":
        left = _task_left(task)
        if left is not None and left <= 0 and task_has_completion_fact(task):
            return "done"
    return status


__all__ = [
    "effective_task_status",
    "raw_task_status",
    "task_has_completion_fact",
]
