from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Inputs / list DTOs (kept from the original feature)
# ---------------------------------------------------------------------------

class ExecutionOut(BaseModel):
    id: int
    name: str = ""
    project: int | None = None
    project_name: str = ""
    begin: str = ""
    end: str = ""
    status: str = ""


class StoryListItem(BaseModel):
    id: int
    title: str = ""
    pri: int | None = None
    status: str = ""
    stage: str = ""
    assigned_to: str = ""
    product: int | None = None
    product_name: str = ""


class GenerateRequest(BaseModel):
    execution_id: int = Field(..., gt=0)
    story_ids: list[int] = Field(default_factory=list)
    select_all: bool = False
    user_note: str = ""


# ---------------------------------------------------------------------------
# Result DTOs
# ---------------------------------------------------------------------------

class StoryAIResultOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    batch_id: str
    story_id: int
    execution_id: int | None = None
    execution_name: str | None = None
    title: str | None = None
    briefing: str | None = None
    module_name: str | None = None
    scene_name: str | None = None
    stage_name: str | None = None
    case_type: str | None = None
    priority: str | None = None
    precondition: str | None = None

    # The *_json columns are stored as text. The route hydrates these to
    # structured Python objects before serializing.
    steps: list[dict[str, Any]] = Field(default_factory=list)
    keywords: str | None = None
    risk_points: list[Any] = Field(default_factory=list)
    questions_to_confirm: list[Any] = Field(default_factory=list)
    testcase_template: str | None = None
    raw_ai_result: Any = None

    ai_status: str
    ai_error_message: str | None = None
    created_by: int | None = None
    created_at: datetime
    updated_at: datetime


class StoryAIResultSummary(BaseModel):
    """Lightweight view for list pages to know 'does this story have AI output'."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    batch_id: str
    story_id: int
    title: str | None = None
    ai_status: str
    updated_at: datetime


class GenerateAndSaveResponse(BaseModel):
    batch_id: str
    status: str = "pending"
    story_ids: list[int]
    expected_duration_seconds: int


class BatchLatestRequest(BaseModel):
    story_ids: list[int] = Field(default_factory=list)


class BatchLatestResponse(BaseModel):
    latest: dict[int, StoryAIResultSummary] = Field(default_factory=dict)


class BatchStatusResponse(BaseModel):
    batch_id: str
    total: int
    success: int
    failed: int
    pending: int
    results: list[StoryAIResultSummary] = Field(default_factory=list)
