"""
Zentao → n8n AI webhook forwarder + result persistence.

GET  /zentao/ai/executions
GET  /zentao/ai/executions/{id}/stories
POST /zentao/ai/generate-and-save       Kick off background n8n run, seed pending rows
GET  /zentao/ai/batch/{batch_id}        Poll batch progress (SSE is the primary channel)
GET  /zentao/ai/story/{story_id}/latest Fetch the most recent AI result for a story
POST /zentao/ai/story/batch-latest      Bulk-query latest results for a set of stories

Access control:
  - "zentao-ai" tab permission (managed in 权限管理)
  - Each user must configure their own DeepSeek API Key in 个人设置 → 🤖 AI密钥
"""
from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.core.config import settings
from app.models.story_ai_result import StoryAIResult
from app.schemas.zentao_ai import (
    BatchLatestRequest,
    BatchLatestResponse,
    BatchStatusResponse,
    ExecutionOut,
    GenerateAndSaveResponse,
    GenerateRequest,
    StoryAIResultOut,
    StoryAIResultSummary,
    StoryListItem,
)
from app.services import story_ai_result_service, zentao_ai_service
from app.services.permission_service import ensure_tab_access
from app.services.zentao_ai_service import ZentaoAIServiceError
from app.services import zentao_ai_case_service as case_service
from app.schemas.zentao_ai_case import (
    AddSourcesRequest, EditDraftRequest, DraftSelectionRequest,
    DraftActionRequest, PushCasesRequest, ReconcileCaseRequest,
)
from app.models.zentao_ai_case import AICasePushBatch, AICasePushItem

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/zentao/ai", tags=["zentao_ai"])

_TAB_KEY = "zentao-ai"


def _gate(current_user) -> None:
    ensure_tab_access(current_user, _TAB_KEY)


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------

@router.get("/executions", response_model=list[ExecutionOut])
def list_executions(
    project_id: int | None = None,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _gate(current_user)
    try:
        return zentao_ai_service.list_executions(current_user.id, db, project_id)
    except ZentaoAIServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message)


@router.get("/executions/{execution_id}/stories", response_model=list[StoryListItem])
def list_stories(
    execution_id: int,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _gate(current_user)
    if execution_id <= 0:
        raise HTTPException(status_code=400, detail="execution_id 不能为空")
    try:
        return zentao_ai_service.list_stories(current_user.id, db, execution_id)
    except ZentaoAIServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message)


# ---------------------------------------------------------------------------
# Generate + save (async background run)
# ---------------------------------------------------------------------------

@router.post("/generate-and-save", response_model=GenerateAndSaveResponse)
def generate_and_save(
    payload: GenerateRequest,
    background_tasks: BackgroundTasks,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _gate(current_user)
    if not payload.select_all and not payload.story_ids:
        raise HTTPException(status_code=400, detail="请至少选择一条需求或勾选全部")
    try:
        prepared = zentao_ai_service.prepare_batch(
            user_id=current_user.id,
            username=current_user.username,
            db=db,
            execution_id=payload.execution_id,
            story_ids=payload.story_ids,
            select_all=payload.select_all,
            user_note=payload.user_note,
        )
    except ZentaoAIServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message)

    background_tasks.add_task(
        zentao_ai_service.run_background_batch,
        prepared["batch_id"],
        prepared["payload"],
    )
    return GenerateAndSaveResponse(
        batch_id=prepared["batch_id"],
        status="pending",
        story_ids=prepared["story_ids"],
        expected_duration_seconds=settings.zentao_ai_expected_duration_seconds,
    )


# ---------------------------------------------------------------------------
# Result queries
# ---------------------------------------------------------------------------

def _loads(value: str | None, default):
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def _hydrate(row: StoryAIResult) -> dict[str, Any]:
    """Convert ORM row into a dict suitable for StoryAIResultOut."""
    raw_ai_result = _loads(row.raw_ai_result_json, None)
    raw_test_cases = raw_ai_result.get("test_cases", []) if isinstance(raw_ai_result, dict) else []
    test_cases = (
        [item for item in raw_test_cases if isinstance(item, dict)]
        if isinstance(raw_test_cases, list)
        else []
    )
    raw_title = None
    if isinstance(raw_ai_result, dict):
        raw_title = (
            raw_ai_result.get("title")
            or raw_ai_result.get("story_title")
            or raw_ai_result.get("storyTitle")
        )
    return {
        "id": row.id,
        "batch_id": row.batch_id,
        "story_id": row.story_id,
        "execution_id": row.execution_id,
        "execution_name": row.execution_name,
        "title": raw_title or row.title,
        "briefing": row.briefing,
        "module_name": row.module_name,
        "scene_name": row.scene_name,
        "stage_name": row.stage_name,
        "case_type": row.case_type,
        "priority": row.priority,
        "precondition": row.precondition,
        "steps": _loads(row.steps_json, []) if isinstance(_loads(row.steps_json, []), list) else [],
        "keywords": row.keywords,
        "risk_points": _loads(row.risk_points_json, []) if isinstance(_loads(row.risk_points_json, []), list) else [],
        "questions_to_confirm": _loads(row.questions_to_confirm_json, []) if isinstance(_loads(row.questions_to_confirm_json, []), list) else [],
        "test_cases": test_cases,
        "testcase_template": row.testcase_template,
        "raw_ai_result": raw_ai_result,
        "ai_status": row.ai_status,
        "ai_error_message": row.ai_error_message,
        "created_by": row.created_by,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


@router.get("/story/{story_id}/latest", response_model=StoryAIResultOut | None)
def get_latest_for_story(
    story_id: int,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _gate(current_user)
    row = story_ai_result_service.get_latest_by_story_id(db, story_id)
    if not row:
        return None
    return _hydrate(row)


@router.post("/story/batch-latest", response_model=BatchLatestResponse)
def batch_latest(
    payload: BatchLatestRequest,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _gate(current_user)
    rows = story_ai_result_service.list_latest_by_story_ids(db, payload.story_ids)
    latest = {
        sid: StoryAIResultSummary.model_validate(row)
        for sid, row in rows.items()
    }
    return BatchLatestResponse(latest=latest)


@router.get("/batch/{batch_id}", response_model=BatchStatusResponse)
def batch_status(
    batch_id: str,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _gate(current_user)
    rows = story_ai_result_service.list_by_batch(db, batch_id)
    if not rows:
        raise HTTPException(status_code=404, detail="未找到对应批次")
    summary = story_ai_result_service.batch_summary(rows)
    return BatchStatusResponse(
        batch_id=batch_id,
        total=summary["total"],
        success=summary["success"],
        failed=summary["failed"],
        pending=summary["pending"],
        results=[StoryAIResultSummary.model_validate(r) for r in rows],
    )


@router.post("/results/{result_id}/workspace")
def open_case_workspace(result_id: int, current_user=Depends(get_current_user), db: Session = Depends(get_db)):
    _gate(current_user)
    return case_service.open_workspace(db, current_user.id, result_id)


@router.get("/workspaces/{workspace_id}")
def get_case_workspace(workspace_id: str, current_user=Depends(get_current_user), db: Session = Depends(get_db)):
    _gate(current_user)
    row = case_service.get_workspace(db, current_user.id, workspace_id)
    case_service.recover_stale_items(db)
    return case_service.workspace_view(db, row)


@router.post("/workspaces/{workspace_id}/sources")
def add_case_sources(workspace_id: str, payload: AddSourcesRequest, current_user=Depends(get_current_user), db: Session = Depends(get_db)):
    _gate(current_user)
    row = case_service.get_workspace(db, current_user.id, workspace_id)
    return case_service.add_sources(db, row, payload.revision, payload.source_indexes)


@router.patch("/workspaces/{workspace_id}/drafts/{draft_id}")
def edit_case_draft(workspace_id: str, draft_id: str, payload: EditDraftRequest, current_user=Depends(get_current_user), db: Session = Depends(get_db)):
    _gate(current_user)
    row = case_service.get_workspace(db, current_user.id, workspace_id)
    return case_service.edit_draft(db, row, payload.revision, draft_id, payload.content)


@router.post("/workspaces/{workspace_id}/merge")
def merge_case_drafts(workspace_id: str, payload: DraftSelectionRequest, current_user=Depends(get_current_user), db: Session = Depends(get_db)):
    _gate(current_user)
    row = case_service.get_workspace(db, current_user.id, workspace_id)
    return case_service.merge_drafts(db, row, payload.revision, [str(x) for x in payload.draft_ids])


@router.post("/workspaces/{workspace_id}/drafts/{draft_id}/action")
def change_case_draft(workspace_id: str, draft_id: str, payload: DraftActionRequest, current_user=Depends(get_current_user), db: Session = Depends(get_db)):
    _gate(current_user)
    row = case_service.get_workspace(db, current_user.id, workspace_id)
    return case_service.draft_action(db, row, payload.revision, draft_id, payload.action)


@router.post("/workspaces/{workspace_id}/push")
def push_case_drafts(workspace_id: str, payload: PushCasesRequest, background_tasks: BackgroundTasks,
                     current_user=Depends(get_current_user), db: Session = Depends(get_db)):
    _gate(current_user)
    row = case_service.get_workspace(db, current_user.id, workspace_id)
    batch = case_service.push_cases(db, current_user.id, row, payload.revision, str(payload.request_id), [str(x) for x in payload.draft_ids])
    if batch["queued"]:
        background_tasks.add_task(case_service.run_batch, batch["id"])
    return batch


@router.get("/push-batches/{batch_id}")
def get_case_push_batch(batch_id: str, current_user=Depends(get_current_user), db: Session = Depends(get_db)):
    _gate(current_user)
    case_service.recover_stale_items(db)
    return case_service.batch_view(db, current_user.id, batch_id)


@router.post("/workspaces/{workspace_id}/resume")
def resume_case_push(workspace_id: str, background_tasks: BackgroundTasks, current_user=Depends(get_current_user), db: Session = Depends(get_db)):
    _gate(current_user)
    row = case_service.get_workspace(db, current_user.id, workspace_id)
    case_service.recover_stale_items(db)
    batches = db.query(AICasePushBatch).join(AICasePushItem, AICasePushItem.batch_id == AICasePushBatch.id).filter(
        AICasePushBatch.workspace_id == row.id, AICasePushBatch.user_id == current_user.id, AICasePushItem.status == "queued").all()
    for batch in batches:
        background_tasks.add_task(case_service.run_batch, batch.id)
    return case_service.workspace_view(db, row)


@router.post("/workspaces/{workspace_id}/drafts/{draft_id}/reconcile")
def reconcile_case_push(workspace_id: str, draft_id: str, payload: ReconcileCaseRequest,
                        current_user=Depends(get_current_user), db: Session = Depends(get_db)):
    _gate(current_user)
    row = case_service.get_workspace(db, current_user.id, workspace_id)
    return case_service.reconcile(db, current_user.id, row, draft_id, payload.case_id)


@router.post("/workspaces/{workspace_id}/drafts/{draft_id}/sync")
def sync_case_push(workspace_id: str, draft_id: str, current_user=Depends(get_current_user), db: Session = Depends(get_db)):
    _gate(current_user)
    row = case_service.get_workspace(db, current_user.id, workspace_id)
    return case_service.reconcile(db, current_user.id, row, draft_id)
