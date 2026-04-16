"""
Zentao hydration proxy endpoints.

The frontend zentao-hydrator.js calls these endpoints to batch-fetch
live Zentao data (bug status, story stage, etc.) for IDs visible on
the current page.  The backend proxies the requests using the calling
user's stored Zentao token, refreshing it automatically if needed.

Token handling
--------------
If Zentao returns 401 (token expired/invalid), the stored token is
invalidated and a fresh one is fetched before retrying the batch.
This handles the common case where Zentao's actual token lifetime is
shorter than our 2-hour assumption.

Concurrency
-----------
Requests to Zentao are made concurrently (up to _MAX_CONCURRENCY at a
time) using asyncio.gather + asyncio.to_thread, dramatically reducing
total latency when a page contains many bugs/stories.

Error semantics
---------------
The response includes an optional "__fetch_errors__" list with IDs that
could not be fetched due to network/server errors (distinct from IDs
simply not present in Zentao).  Callers should NOT cache these as
"not found"; they should retry on the next render cycle.

Routes
------
GET /zentao/hydrate/bugs?ids=29875,29876
GET /zentao/hydrate/stories?ids=5604,5605
"""
from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.models.bug import BugTracking
from app.models.user_zentao_binding import UserZentaoBinding
from app.services.zentao_auth_service import get_valid_token, invalidate_token
from app.services.zentao_client_service import ZentaoClient, ZentaoAPIError
from app.services.zentao_normalizer import normalize_bug, normalize_story

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/zentao", tags=["zentao_hydrate"])

# Hard cap: don't let a single request fan out to an unbounded number of
# upstream calls.  Stage5 now supports configurable page sizes, so the cap is
# raised to 500 to avoid silently truncating the lower half of long pages.
_MAX_BATCH = 500
# Maximum concurrent upstream requests per hydration call
_MAX_CONCURRENCY = 10


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/hydrate/bugs")
async def hydrate_bugs(
    ids: str = Query(..., description="Comma-separated Zentao bug IDs (numeric)"),
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Batch-fetch live bug data from Zentao (concurrent).

    Returns a mapping: { "<bug_id>": normalized_bug_dict, … }
    IDs with transient errors are listed under "__fetch_errors__" so
    callers can distinguish them from confirmed-not-found IDs.
    If the user has no Zentao binding, returns an empty object.
    """
    id_list = _parse_id_list(ids)
    if not id_list:
        return {}

    ctx = _get_client_ctx(current_user.id, db)
    if ctx is None:
        return {}
    client, base_url = ctx

    result, got_401, errors = await _fetch_bugs_concurrent(client, base_url, id_list)

    if got_401:
        invalidate_token(current_user.id, db)
        ctx2 = _get_client_ctx(current_user.id, db)
        if ctx2 is None:
            return _build_response({}, errors)
        client2, base_url2 = ctx2
        result, _, errors = await _fetch_bugs_concurrent(client2, base_url2, id_list)

    # Write-back: persist title + URL for bugs that don't have them yet.
    # Matches on bug_id like "b#29009" → Zentao numeric ID 29009.
    _writeback_bug_fields(db, result, base_url)

    return _build_response(result, errors)


@router.get("/hydrate/stories")
async def hydrate_stories(
    ids: str = Query(..., description="Comma-separated Zentao story IDs (numeric)"),
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Batch-fetch live story/requirement data from Zentao (concurrent).

    Returns a mapping: { "<story_id>": normalized_story_dict, … }
    Transient errors are listed under "__fetch_errors__".
    """
    id_list = _parse_id_list(ids)
    if not id_list:
        return {}

    ctx = _get_client_ctx(current_user.id, db)
    if ctx is None:
        return {}
    client, base_url = ctx

    result, got_401, errors = await _fetch_stories_concurrent(client, id_list)

    if got_401:
        invalidate_token(current_user.id, db)
        ctx2 = _get_client_ctx(current_user.id, db)
        if ctx2 is None:
            return _build_response({}, errors)
        client2, _ = ctx2
        result, _, errors = await _fetch_stories_concurrent(client2, id_list)

    return _build_response(result, errors)


# ---------------------------------------------------------------------------
# Concurrent fetch helpers
# ---------------------------------------------------------------------------

async def _fetch_bugs_concurrent(
    client: ZentaoClient,
    base_url: str,
    id_list: list[int],
) -> tuple[dict, bool, list[str]]:
    """
    Fetch bug data for all IDs concurrently (up to _MAX_CONCURRENCY at once).

    Returns (result_dict, got_401, error_id_strings).
    """
    sem = asyncio.Semaphore(_MAX_CONCURRENCY)
    result: dict[str, dict] = {}
    errors: list[str] = []
    got_401 = False

    async def _one(bug_id: int) -> None:
        nonlocal got_401
        async with sem:
            try:
                raw = await asyncio.to_thread(client.get_bug, bug_id)
                if raw:
                    data = normalize_bug(raw)
                    data["zentao_url"] = f"{base_url}/bug-view-{bug_id}.html"
                    result[str(bug_id)] = data
            except ZentaoAPIError as exc:
                if exc.status_code == 401:
                    got_401 = True
                else:
                    logger.warning("hydrate_bugs: bug_id=%s err=%s", bug_id, exc)
                    errors.append(str(bug_id))
            except Exception as exc:  # noqa: BLE001
                logger.warning("hydrate_bugs: bug_id=%s unexpected=%s", bug_id, exc)
                errors.append(str(bug_id))

    await asyncio.gather(*[_one(bug_id) for bug_id in id_list])
    return result, got_401, errors


async def _fetch_stories_concurrent(
    client: ZentaoClient,
    id_list: list[int],
) -> tuple[dict, bool, list[str]]:
    """
    Fetch story data for all IDs concurrently (up to _MAX_CONCURRENCY at once).

    Returns (result_dict, got_401, error_id_strings).
    """
    sem = asyncio.Semaphore(_MAX_CONCURRENCY)
    result: dict[str, dict] = {}
    errors: list[str] = []
    got_401 = False

    async def _one(story_id: int) -> None:
        nonlocal got_401
        async with sem:
            try:
                raw = await asyncio.to_thread(client.get_story, story_id)
                if raw:
                    result[str(story_id)] = normalize_story(raw)
            except ZentaoAPIError as exc:
                if exc.status_code == 401:
                    got_401 = True
                else:
                    logger.warning("hydrate_stories: story_id=%s err=%s", story_id, exc)
                    errors.append(str(story_id))
            except Exception as exc:  # noqa: BLE001
                logger.warning("hydrate_stories: story_id=%s unexpected=%s", story_id, exc)
                errors.append(str(story_id))

    await asyncio.gather(*[_one(story_id) for story_id in id_list])
    return result, got_401, errors


# ---------------------------------------------------------------------------
# Response builder
# ---------------------------------------------------------------------------

def _build_response(result: dict, errors: list[str]) -> dict:
    """Attach __fetch_errors__ to the result dict when there are transient errors."""
    response = dict(result)
    if errors:
        response["__fetch_errors__"] = errors
    return response


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _parse_id_list(raw: str) -> list[int]:
    """Parse a comma-separated string of integers, capped at _MAX_BATCH."""
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    result: list[int] = []
    for part in parts[:_MAX_BATCH]:
        try:
            result.append(int(part))
        except ValueError:
            pass
    return result


def _get_client_ctx(user_id: int, db: Session) -> tuple[ZentaoClient, str] | None:
    """
    Return (ZentaoClient, base_url) for the given user, or None if the user
    has no binding or a valid token cannot be obtained.
    """
    binding = (
        db.query(UserZentaoBinding)
        .filter(UserZentaoBinding.user_id == user_id)
        .first()
    )
    if not binding or not binding.base_url:
        return None

    token = get_valid_token(user_id, db)
    if not token:
        return None

    base_url = binding.base_url.rstrip('/')
    return ZentaoClient(base_url=base_url, token=token), base_url


def _writeback_bug_fields(db: Session, result: dict, base_url: str) -> None:
    """
    For each fetched bug, persist title, URL, zentao_bug_id and live status
    back to BugTracking so they are available on next page load without
    requiring a fresh hydration call.

    Matching key: BugTracking.bug_id == "b#<zt_id>"
    """
    if not result:
        return
    try:
        any_changed = False
        for zt_id_str, data in result.items():
            if not data or not isinstance(data, dict):
                continue
            title = data.get("title") or ""
            status = data.get("status") or ""
            zt_url = f"{base_url}/bug-view-{zt_id_str}.html"
            bug_id_str = f"b#{zt_id_str}"
            row = db.query(BugTracking).filter(BugTracking.bug_id == bug_id_str).first()
            if row is None:
                continue
            if not row.zentao_bug_title and title:
                row.zentao_bug_title = title
                any_changed = True
            if not row.zentao_bug_url:
                row.zentao_bug_url = zt_url
                any_changed = True
            if not row.zentao_bug_id:
                row.zentao_bug_id = zt_id_str
                any_changed = True
            if status and row.zentao_live_status != status:
                row.zentao_live_status = status
                any_changed = True
        if any_changed:
            db.commit()
    except Exception as exc:
        logger.warning("_writeback_bug_fields: %s", exc)
        db.rollback()
