from __future__ import annotations

import re

from app.core.exceptions import ValidationFailed

R_PATTERN = re.compile(r"^r#\d+$")
U_PATTERN = re.compile(r"^u#\d+$")
B_PATTERN = re.compile(r"^b#\d+$")
V_PATTERN = re.compile(r"^v#\d+$")


def validate_req_id(value: str) -> bool:
    return bool(R_PATTERN.match(value))


def validate_case_id(value: str) -> bool:
    return bool(U_PATTERN.match(value))


def validate_bug_id(value: str) -> bool:
    return bool(B_PATTERN.match(value))


def parse_prefixed_ids(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [item.strip() for item in raw.split(",") if item.strip()]


def require_prefixed_id(value: str, kind: str) -> str:
    rules = {
        "requirement": validate_req_id,
        "case": validate_case_id,
        "bug": validate_bug_id,
    }
    checker = rules.get(kind)
    if checker is None or not checker(value):
        raise ValidationFailed(f"Invalid {kind} id: {value}")
    return value
