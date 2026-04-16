"""
Shared utility functions for Zentao integration.

These are used by both backend services and exposed via API
so that the frontend can use the same parsing logic.
"""
from __future__ import annotations

import re

# Matches job names like: s4031, s40311, s40311-1, s40311_free
# Captures only the digit group; any suffix after - or _ is ignored.
_JOB_PATTERN = re.compile(r'^s(\d{3,})(?:[-_].*)?$', re.IGNORECASE)

# Platform suffixes to strip from version_name
_PLATFORM_SUFFIX_PATTERN = re.compile(r'\s*\((?:64|32)-bit\)\s*', re.IGNORECASE)


def parse_job_name_to_major_version_no(job_name: str) -> str | None:
    """
    Parse a Jenkins job_name to the corresponding local major version number.

    Mapping rules (consistent with frontend jobNameToMajorLabel):
        s4030     -> V4.0.3.0
        s4031     -> V4.0.3.1
        s40311    -> V4.0.3.11
        s40311-1  -> V4.0.3.11   (suffix after - is ignored)
        s40311_x  -> V4.0.3.11   (suffix after _ is ignored)

    Returns None if job_name does not match the expected pattern.
    """
    text = (job_name or '').strip()
    match = _JOB_PATTERN.match(text)
    if not match:
        return None
    digits = match.group(1)
    if len(digits) < 3:
        return None
    a = digits[0]
    b = digits[1]
    c = digits[2]
    rest = digits[3:]
    if rest:
        return f'V{a}.{b}.{c}.{rest}'
    return f'V{a}.{b}.{c}'


def normalize_version_name(version_name: str) -> str:
    """
    Normalize a raw version_name string for use as a minor version identifier.

    Operations:
    1. Remove (64-bit) / (32-bit) platform suffixes (case-insensitive).
    2. Strip leading/trailing whitespace.
    3. Preserve business suffixes such as _alpha.1, _BD, _Geofennel.

    Returns an empty string if the input is empty or normalizes to empty.

    Examples:
        '4.0.3.1.260413(40301023)(64-bit)' -> '4.0.3.1.260413(40301023)'
        '4.0.3.18.260413_Geofennel(40318007) (32-bit)' -> '4.0.3.18.260413_Geofennel(40318007)'
        '' -> ''
    """
    if not version_name:
        return ''
    name = _PLATFORM_SUFFIX_PATTERN.sub('', version_name).strip()
    return name


__all__ = ["parse_job_name_to_major_version_no", "normalize_version_name"]
