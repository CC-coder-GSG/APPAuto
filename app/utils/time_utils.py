from __future__ import annotations

from datetime import datetime, timezone


def local_now() -> datetime:
    """Return the server local time as a naive datetime."""
    return datetime.now()


def utc_now_naive() -> datetime:
    """Return UTC time as a naive datetime for internal token/cache handling."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def parse_external_datetime_to_local_naive(raw: str | None) -> datetime | None:
    """
    Parse external datetime text and normalize it to server-local naive datetime.

    Supported inputs:
    - 2026-04-17 10:30:00
    - 2026-04-17T10:30:00
    - 2026-04-17T10:30:00Z
    - 2026-04-17T10:30:00+08:00
    - 2026-04-17
    """
    if not raw:
        return None

    text = str(raw).strip()
    if not text or text in {"0000-00-00", "0000-00-00 00:00:00"}:
        return None

    normalized = text.replace("Z", "+00:00")

    try:
        dt = datetime.fromisoformat(normalized)
    except ValueError:
        dt = None

    if dt is None:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
            try:
                dt = datetime.strptime(text[:19] if fmt != "%Y-%m-%d" else text[:10], fmt)
                break
            except ValueError:
                continue

    if dt is None:
        return None

    if dt.tzinfo is not None:
        return dt.astimezone().replace(tzinfo=None)
    return dt


__all__ = ["local_now", "utc_now_naive", "parse_external_datetime_to_local_naive"]
