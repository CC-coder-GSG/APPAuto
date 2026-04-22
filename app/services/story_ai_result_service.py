from __future__ import annotations

import json
import logging
from typing import Any

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


def _load_json_if_possible(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    text = value.strip()
    if not text or text[0] not in "[{":
        return value
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return value


def _extract_story_id(item: dict[str, Any]) -> int | None:
    candidates = [item.get("story_id"), item.get("storyId"), item.get("storyID")]
    story = item.get("story")
    if isinstance(story, dict):
        candidates.extend([story.get("id"), story.get("story_id"), story.get("storyId")])
    for value in candidates:
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return None


def _extract_results(n8n_response: Any) -> tuple[list[Any] | None, Any]:
    payload = _load_json_if_possible(n8n_response)

    if isinstance(payload, list):
        wrapped_results: list[Any] = []
        wrapped = True
        for item in payload:
            if not isinstance(item, dict):
                wrapped = False
                break
            nested = _load_json_if_possible(item.get("results"))
            if isinstance(nested, list):
                wrapped_results.extend(nested)
                continue
            if isinstance(nested, dict) and _extract_story_id(nested) is not None:
                wrapped_results.append(nested)
                continue
            wrapped = False
            break
        if wrapped and wrapped_results:
            return wrapped_results, payload
        return payload, payload
    if not isinstance(payload, dict):
        return None, payload

    candidates = [
        payload.get("results"),
        payload.get("data"),
        payload.get("result"),
        payload.get("output"),
        ((payload.get("data") or {}).get("results") if isinstance(payload.get("data"), dict) else None),
        ((payload.get("result") or {}).get("results") if isinstance(payload.get("result"), dict) else None),
        ((payload.get("output") or {}).get("results") if isinstance(payload.get("output"), dict) else None),
    ]
    for candidate in candidates:
        normalized = _load_json_if_possible(candidate)
        if isinstance(normalized, list):
            return normalized, payload
        if isinstance(normalized, dict) and _extract_story_id(normalized) is not None:
            return [normalized], payload

    if _extract_story_id(payload) is not None:
        return [payload], payload
    return None, payload


def _first_present(item: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in item and item.get(key) is not None:
            return item.get(key)
    return None


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
    for story in stories:
        try:
            sid = int(story.get("id"))
        except (TypeError, ValueError):
            continue
        row = StoryAIResult(
            batch_id=batch_id,
            story_id=sid,
            execution_id=execution_id,
            execution_name=execution_name or "",
            title=_str_or_none(story.get("title")),
            ai_status="pending",
            created_by=user_id,
        )
        db.add(row)
        rows.append(row)
    db.commit()
    for row in rows:
        db.refresh(row)
    return rows


def save_ai_results(
    db: Session,
    *,
    batch_id: str,
    n8n_response: Any,
) -> dict:
    """
    Update pending rows for `batch_id` based on n8n output.

    The primary contract is:
        {"results": [{"story_id": 123, ...}, ...]}

    We also accept common n8n variants such as a top-level list, a nested
    `data/results` wrapper, JSON strings produced by Set/Code nodes, camelCase
    field aliases, and a single-result single-story batch with no explicit
    `story_id`.
    """
    rows = (
        db.query(StoryAIResult)
        .filter(StoryAIResult.batch_id == batch_id, StoryAIResult.ai_status == "pending")
        .all()
    )
    by_story: dict[int, StoryAIResult] = {row.story_id: row for row in rows}

    success_count = 0
    failed_count = 0
    per_story: dict[int, str] = {}

    results, normalized_payload = _extract_results(n8n_response)
    if not isinstance(results, list):
        raw = _dump(normalized_payload if normalized_payload is not None else n8n_response) or ""
        err = f"n8n returned an invalid payload without a usable results list: {raw[:800]}"
        for row in rows:
            row.ai_status = "failed"
            row.ai_error_message = err
            row.raw_ai_result_json = raw[:_MAX_RAW_LEN]
            row.updated_at = local_now()
            per_story[row.story_id] = "failed"
            failed_count += 1
        db.commit()
        logger.warning("zentao_ai.save_ai_results: batch=%s invalid n8n response", batch_id)
        return {"success": success_count, "failed": failed_count, "per_story": per_story}

    single_pending_story_id = next(iter(by_story)) if len(by_story) == 1 else None

    for item in results:
        if not isinstance(item, dict):
            logger.warning("zentao_ai.save_ai_results: batch=%s skip non-dict item", batch_id)
            continue

        sid = _extract_story_id(item)
        if sid is None and single_pending_story_id is not None and len(results) == 1:
            sid = single_pending_story_id
            logger.warning(
                "zentao_ai.save_ai_results: batch=%s inferred story_id=%s for single-result batch",
                batch_id,
                sid,
            )
        if sid is None:
            logger.warning("zentao_ai.save_ai_results: batch=%s skip item missing story_id", batch_id)
            continue

        row = by_story.get(sid)
        if row is None:
            logger.warning(
                "zentao_ai.save_ai_results: batch=%s unknown story_id=%s (not in pending set)",
                batch_id,
                sid,
            )
            continue

        row.title = _str_or_none(_first_present(item, "title")) or row.title
        row.briefing = _str_or_none(_first_present(item, "briefing"))
        row.module_name = _str_or_none(_first_present(item, "module_name", "module"))
        row.scene_name = _str_or_none(_first_present(item, "scene_name", "scene"))
        row.stage_name = _str_or_none(_first_present(item, "stage_name", "stage"))
        row.case_type = _str_or_none(_first_present(item, "case_type", "caseType"))
        row.priority = _str_or_none(_first_present(item, "priority"))
        row.precondition = _str_or_none(_first_present(item, "precondition"))
        row.keywords = _str_or_none(_first_present(item, "keywords"))
        row.testcase_template = _str_or_none(_first_present(item, "testcase_template", "testcaseTemplate"))

        steps = _load_json_if_possible(_first_present(item, "steps"))
        if steps is not None:
            row.steps_json = _dump(steps)
        risks = _load_json_if_possible(_first_present(item, "risk_points", "riskPoints"))
        if risks is not None:
            row.risk_points_json = _dump(risks)
        questions = _load_json_if_possible(_first_present(item, "questions_to_confirm", "questionsToConfirm"))
        if questions is not None:
            row.questions_to_confirm_json = _dump(questions)

        row.raw_ai_result_json = _dump(item)
        row.ai_status = "success"
        row.ai_error_message = None
        row.updated_at = local_now()
        per_story[sid] = "success"
        success_count += 1

    for row in rows:
        if row.ai_status == "pending":
            row.ai_status = "failed"
            row.ai_error_message = "n8n did not return a result for this story"
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
    for row in rows:
        row.ai_status = "failed"
        row.ai_error_message = error_message
        if dumped is not None:
            row.raw_ai_result_json = dumped[:_MAX_RAW_LEN]
        row.updated_at = local_now()
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
    for row in rows:
        if row.story_id not in out:
            out[row.story_id] = row
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
    success = sum(1 for row in rows if row.ai_status == "success")
    failed = sum(1 for row in rows if row.ai_status == "failed")
    pending = sum(1 for row in rows if row.ai_status == "pending")
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
