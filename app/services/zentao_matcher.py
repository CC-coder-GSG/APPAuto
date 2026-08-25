from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable


REQ_ID_RE = re.compile(r"(?:^|\b)(?:r#|sr\s*|需求\s*)?(\d{2,})\b", re.IGNORECASE)
EXEC_RE = re.compile(r"\bs\d{3,}\b", re.IGNORECASE)
BUILD_RE = re.compile(r"\((\d{5,})\)")
VERSION_PREFIX_RE = re.compile(r"\d+\.\d+\.\d+\.\d+")
MAJOR_V_RE = re.compile(r"\bV\d+\.\d+\.\d+\.\d+\b", re.IGNORECASE)


@dataclass
class AffectedVersionParsed:
    raw: str
    version_prefix: str | None
    build_no: str | None


def extract_requirement_numeric_id(value: str | None) -> str | None:
    if not value:
        return None
    m = REQ_ID_RE.search(value)
    if not m:
        return None
    return m.group(1)


def normalize_requirement_title(value: str | None) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    text = re.sub(r"^[\[\(]?(?:sr|r#|需求)\s*\d+[\]:：\-\s]*", "", text, flags=re.IGNORECASE)
    text = text.replace("\u3000", " ")
    text = re.sub(r"[\t\r\n]+", " ", text)
    text = re.sub(r"\s+", " ", text)
    text = text.replace("：", ":")
    text = text.replace("，", ",")
    text = text.replace("（", "(").replace("）", ")")
    return text.strip()


def normalize_execution_name(value: str | None) -> str | None:
    text = (value or "").strip()
    if not text:
        return None
    m = EXEC_RE.search(text)
    if m:
        return m.group(0).lower()
    text = text.replace("（", "(").replace("）", ")")
    m2 = re.search(r"\bs\d+", text, re.IGNORECASE)
    return m2.group(0).lower() if m2 else text.lower()


def convert_s_token_to_major_version(value: str | None) -> str | None:
    """Map s4030/s40311 style execution token to major version V4.0.3.0/V4.0.3.11."""
    token = normalize_execution_name(value)
    if not token or not token.startswith("s"):
        return None
    digits = token[1:]
    if len(digits) < 4:
        return None
    # Rule: first 3 digits are major.minor.patch, the remaining suffix is the tail segment.
    return f"V{digits[0]}.{digits[1]}.{digits[2]}.{digits[3:]}"


def extract_major_version_token(value: str | None) -> str | None:
    text = (value or "").strip()
    if not text:
        return None
    normalized = text.replace("（", "(").replace("）", ")")
    m = MAJOR_V_RE.search(normalized)
    if not m:
        return None
    return m.group(0).upper()


def parse_affected_version(value: str | None) -> AffectedVersionParsed:
    raw = (value or "").strip()
    if not raw:
        return AffectedVersionParsed(raw="", version_prefix=None, build_no=None)
    text = raw.replace("（", "(").replace("）", ")")
    b = BUILD_RE.search(text)
    v = VERSION_PREFIX_RE.search(text)
    return AffectedVersionParsed(raw=raw, version_prefix=v.group(0) if v else None, build_no=b.group(1) if b else None)


def similarity_score(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    sa = set(a)
    sb = set(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def pick_best_title_match(target: str, candidates: Iterable[tuple[int, str]], threshold: float = 0.82) -> list[int]:
    if not target:
        return []
    scored = []
    for item_id, title in candidates:
        s = similarity_score(target, normalize_requirement_title(title))
        scored.append((item_id, s))
    if not scored:
        return []
    scored.sort(key=lambda x: x[1], reverse=True)
    best = scored[0][1]
    if best < threshold:
        return []
    return [item_id for item_id, s in scored if abs(s - best) < 1e-9]
