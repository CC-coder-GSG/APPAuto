"""
Zentao → n8n AI webhook forwarder.

GET  /zentao/ai/executions                       List executions visible to caller
GET  /zentao/ai/executions/{id}/stories          List stories under an execution
POST /zentao/ai/generate                         Build story payload and POST to n8n

All endpoints require the "zentao-ai" tab permission (managed in admin UI).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.schemas.zentao_ai import (
    ExecutionOut,
    GenerateRequest,
    GenerateResponse,
    StoryListItem,
)
from app.services import zentao_ai_service
from app.services.permission_service import ensure_tab_access
from app.services.zentao_ai_service import ZentaoAIServiceError

router = APIRouter(prefix="/zentao/ai", tags=["zentao_ai"])

_TAB_KEY = "zentao-ai"


@router.get("/executions", response_model=list[ExecutionOut])
def list_executions(
    project_id: int | None = None,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    ensure_tab_access(current_user, _TAB_KEY)
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
    ensure_tab_access(current_user, _TAB_KEY)
    if execution_id <= 0:
        raise HTTPException(status_code=400, detail="execution_id 不能为空")
    try:
        return zentao_ai_service.list_stories(current_user.id, db, execution_id)
    except ZentaoAIServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message)


@router.post("/generate", response_model=GenerateResponse)
async def generate(
    payload: GenerateRequest,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    ensure_tab_access(current_user, _TAB_KEY)
    if not payload.select_all and not payload.story_ids:
        raise HTTPException(status_code=400, detail="请至少选择一条需求或勾选全部")
    try:
        result = await zentao_ai_service.generate(
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
    return result
