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


# 占位符识别 / 生成：把版本号"规范化"成一个共享占位 —— 同一大版本下，
# 不管是普通版、_BD、_mfield、_Gnss7 等任何变体进来，都 rename 同一个占位。
#
# 处理步骤：
#   1) 去掉 (64-bit) / (32-bit) 平台后缀
#   2) 去掉 "日期数字 与 ( 之间" 的变体后缀（_BD / _mfield / _alpha.3 / _GALAIESSURVEY_free.95 …）
#   3) 把所有长度 >=4 的连续数字段的最后 4 位换成 'xxxx'
#
# 4.0.3.1.260513(40301044)                            -> 4.0.3.1.26xxxx(4030xxxx)
# 4.0.3.1.260513_BD(40301043)                         -> 4.0.3.1.26xxxx(4030xxxx)
# 4.0.3.1.260513_BD(40301048)(64-bit)                 -> 4.0.3.1.26xxxx(4030xxxx)
# 4.0.3.20.260513_JFJ_alpha.3(40320003)               -> 4.0.3.20.26xxxx(4032xxxx)
# 4.0.3.0.260513_GALAIESSURVEY_free.95(40300129)      -> 4.0.3.0.26xxxx(4030xxxx)
#
# 之前的逻辑会把 _BD / _Gnss7 等保留进占位名，导致每个变体维护一个占位、且
# 这种"带 _BD(xxxx)"的名字禅道 POST /executions/{id}/builds 会静默拒绝。
# 现在统一回归"无后缀"的规范占位，匹配禅道历史上能成功创建的格式。
_DIGIT_RUN_4PLUS = re.compile(r'\d{4,}')
_VARIANT_SUFFIX_PATTERN = re.compile(r'(\d)_[^()]*(\()')


def make_placeholder_name(real_name: str) -> str:
    """Turn a real version name into the canonical (variant-stripped) placeholder name."""
    text = (real_name or '').strip()
    if not text:
        return ''

    # 1) 去平台后缀 (64-bit) / (32-bit)
    text = _PLATFORM_SUFFIX_PATTERN.sub('', text).strip()
    # 2) 去变体后缀：日期数字 与 ( 之间的 _xxx 段
    text = _VARIANT_SUFFIX_PATTERN.sub(r'\1\2', text)

    # 3) 数字段尾部替换为 xxxx
    def _repl(m: re.Match[str]) -> str:
        digits = m.group(0)
        if len(digits) <= 4:
            return 'xxxx'
        return digits[:-4] + 'xxxx'

    return _DIGIT_RUN_4PLUS.sub(_repl, text)


def is_placeholder_name(name: str) -> bool:
    """A name is a placeholder if it contains an 'xxxx' marker (case-insensitive)."""
    return 'xxxx' in (name or '').lower()


__all__ = [
    "parse_job_name_to_major_version_no",
    "normalize_version_name",
    "make_placeholder_name",
    "is_placeholder_name",
]
