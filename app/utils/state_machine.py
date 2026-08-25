from __future__ import annotations

from app.core.exceptions import InvalidStateTransition

ALLOWED_REQ_TRANSITIONS = {
    "pending": {"assigned"},
    "assigned": {"case_done", "testing"},
    "case_done": {"testing"},
    "testing": {"test_done", "retest_pending"},
    "test_done": {"retest_pending", "retest_done"},
    "retest_pending": {"retest_done"},
    "retest_done": set(),
}


def ensure_requirement_transition(old_status: str, new_status: str) -> None:
    if old_status == new_status:
        return
    if new_status not in ALLOWED_REQ_TRANSITIONS.get(old_status, set()):
        raise InvalidStateTransition(f"Requirement state transition not allowed: {old_status} -> {new_status}")
