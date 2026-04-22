from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


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


class StoryPayload(BaseModel):
    id: int
    title: str = ""
    pri: int | None = None
    status: str = ""
    stage: str = ""
    product: int | None = None
    product_name: str = ""
    module: int | None = None
    module_name: str = ""
    assigned_to: str = ""
    opened_by: str = ""
    reviewed_by: str = ""
    spec_html: str = ""
    spec_text: str = ""
    verify_html: str = ""
    estimate: Any = None
    consumed: Any = None
    actions: list[dict] = Field(default_factory=list)


class GenerateResponse(BaseModel):
    ok: bool
    forwarded_story_count: int
    payload: dict
    n8n_status: int | None = None
    n8n_response: Any = None
