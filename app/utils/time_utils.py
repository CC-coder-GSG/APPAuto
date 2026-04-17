from __future__ import annotations

from datetime import datetime, timezone


def local_now() -> datetime:
    """Return the server local time as a naive datetime."""
    return datetime.now()


def utc_now_naive() -> datetime:
    """Return UTC time as a naive datetime for internal token/cache handling."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


__all__ = ["local_now", "utc_now_naive"]
