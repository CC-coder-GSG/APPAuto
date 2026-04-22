from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.story_ai_result import StoryAIResult
from app.utils.time_utils import local_now

logger = logging.getLogger(__name__)

_MAX_RAW_LEN = 20000


def _dump(value: Any) -> str | None:
    if value is None:
        return None
    try:
        return json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(value)[:_MAX_RAW_LEN]


def _str_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def create_pending_rows(
    db: Session,
    *,
    batch_id: str,
    user_id: int,
    execution_id: int | None,
    execution_name: str,
    stories: list[dict],
) -> list[StoryAIResult]:
    """Seed one pending row per selected story so the UI can display progress."""
    rows: list[StoryAIResult] = []
    for s in stories:
        try:
            sid = int(s.get("id"))
        except (TypeError, ValueError):
            continue
        row = StoryAIResult(
            batch_id=batch_id,
            story_id=sid,
            execution_id=execution_id,
            execution_name=execution_name or "",
            title=_str_or_none(s.get("title")),
            ai_status="pending",
            created_by=user_id,
        )
        db.add(row)
        rows.append(row)
    db.commit()
    for r in rows:
        db.refresh(r)
    return rows


def save_ai_results(
    db: Session,
    *,
    batch_id: str,
    n8n_response: Any,
) -> dict:
    """
    Update the pending rows for `batch_id` based on n8n's structured response.

    Contract with n8n:
        { "results": [ { "story_id": 123, ...fields... }, ... ] }

    Unknown story_ids are logged as warnings and skipped.
    Pending rows not returned by n8n are marked failed.
    If the response is not a parsable object with a `results` list, every pending
    row is marked failed and the raw payload is stashed in `ai_error_message`.
    """
    rows = (
        db.query(StoryAIResult)
        .filter(StoryAIResult.batch_id == batch_id, StoryAIResult.ai_status == "pending")
        .all()
    )
    by_story: dict[int, StoryAIResult] = {r.story_id: r for r in rows}

    success_count = 0
    failed_count = 0
    per_story: dict[int, str] = {}

    results = None
    if isinstance(n8n_response, dict):
        results = n8n_response.get("results")

    if not isinstance(results, list):
        raw = _dump(n8n_response) or ""
        err = f"n8n 返回格式非法（缺少 results 数组）。原始内容：{raw[:800]}"
        for r in rows:
            r.ai_status = "failed"
            r.ai_error_message = err
            r.raw_ai_result_json = raw[:_MAX_RAW_LEN]
            r.updated_at = local_now()
            per_story[r.story_id] = "failed"
            failed_count += 1
        db.commit()
        logger.warning("zentao_ai.save_ai_results: batch=%s invalid n8n response", batch_id)
        return {"success": success_count, "failed": failed_count, "per_story": per_story}

    for item in results:
        if not isinstance(item, dict):
            logger.warning("zentao_ai.save_ai_results: batch=%s skip non-dict item", batch_id)
            continue
        sid_raw = item.get("story_id")
        try:
            sid = int(sid_raw)
        except (TypeError, ValueError):
            logger.warning("zentao_ai.save_ai_results: batch=%s skip item missing story_id", batch_id)
            continue
        row = by_story.get(sid)
        if row is None:
            logger.warning(
                "zentao_ai.save_ai_results: batch=%s unknown story_id=%s (not in pending set)",
                batch_id, sid,
            )
            continue

        row.title = _str_or_none(item.get("title")) or row.title
        row.briefing = _str_or_none(item.get("briefing"))
        row.module_name = _str_or_none(item.get("module_name"))
        row.scene_name = _str_or_none(item.get("scene_name"))
        row.stage_name = _str_or_none(item.get("stage_name"))
        row.case_type = _str_or_none(item.get("case_type"))
        row.priority = _str_or_none(item.get("priority"))
        row.precondition = _str_or_none(item.get("precondition"))
        row.keywords = _str_or_none(item.get("keywords"))
        row.testcase_template = _str_or_none(item.get("testcase_template"))

        steps = item.get("steps")
        if steps is not None:
            row.steps_json = _dump(steps)
        risks = item.get("risk_points")
        if risks is not None:
            row.risk_points_json = _dump(risks)
        questions = item.get("questions_to_confirm")
        if questions is not None:
            row.questions_to_confirm_json = _dump(questions)
        row.raw_ai_result_json = _dump(item)
        row.ai_status = "success"
        row.ai_error_message = None
        row.updated_at = local_now()

        per_story[sid] = "success"
        success_count += 1

    # anything still pending = missing from n8n response → failed
    for row in rows:
        if row.ai_status == "pending":
            row.ai_status = "failed"
            row.ai_error_message = "n8n 未返回该 story 的结果"
            row.updated_at = local_now()
            per_story[row.story_id] = "failed"
            failed_count += 1

    db.commit()
    return {"success": success_count, "failed": failed_count, "per_story": per_story}


def mark_batch_failed(db: Session, *, batch_id: str, error_message: str, raw: Any = None) -> None:
    rows = (
        db.query(StoryAIResult)
        .filter(StoryAIResult.batch_id == batch_id, StoryAIResult.ai_status == "pending")
        .all()
    )
    dumped = _dump(raw) if raw is not None else None
    for r in rows:
        r.ai_status = "failed"
        r.ai_error_message = error_message
        if dumped is not None:
            r.raw_ai_result_json = dumped[:_MAX_RAW_LEN]
        r.updated_at = local_now()
    db.commit()


def get_latest_by_story_id(db: Session, story_id: int) -> StoryAIResult | None:
    return (
        db.query(StoryAIResult)
        .filter(StoryAIResult.story_id == story_id)
        .order_by(StoryAIResult.created_at.desc(), StoryAIResult.id.desc())
        .first()
    )


def list_latest_by_story_ids(db: Session, story_ids: list[int]) -> dict[int, StoryAIResult]:
    """Return {story_id: latest StoryAIResult} for each id that has any row."""
    if not story_ids:
        return {}
    rows = (
        db.query(StoryAIResult)
        .filter(StoryAIResult.story_id.in_(story_ids))
        .order_by(StoryAIResult.story_id, StoryAIResult.created_at.desc(), StoryAIResult.id.desc())
        .all()
    )
    out: dict[int, StoryAIResult] = {}
    for r in rows:
        if r.story_id not in out:
            out[r.story_id] = r
    return out


def list_by_batch(db: Session, batch_id: str) -> list[StoryAIResult]:
    return (
        db.query(StoryAIResult)
        .filter(StoryAIResult.batch_id == batch_id)
        .order_by(StoryAIResult.id.asc())
        .all()
    )


def batch_summary(rows: list[StoryAIResult]) -> dict:
    total = len(rows)
    success = sum(1 for r in rows if r.ai_status == "success")
    failed = sum(1 for r in rows if r.ai_status == "failed")
    pending = sum(1 for r in rows if r.ai_status == "pending")
    return {"total": total, "success": success, "failed": failed, "pending": pending}


__all__ = [
    "batch_summary",
    "create_pending_rows",
    "get_latest_by_story_id",
    "list_by_batch",
    "list_latest_by_story_ids",
    "mark_batch_failed",
    "save_ai_results",
]
