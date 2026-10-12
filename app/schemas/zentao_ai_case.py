from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class CaseStep(BaseModel):
    step: str = Field(default="", max_length=20000)
    expected: str = Field(default="", max_length=20000)


class CaseContent(BaseModel):
    title: str = Field(default="", max_length=255)
    case_type: str = Field(default="", max_length=32)
    priority: int | None = Field(default=None, ge=1, le=4)
    module_id: int = Field(default=0, ge=0)
    stage: str = Field(default="", max_length=32)
    precondition: str = Field(default="", max_length=50000)
    keywords: str = Field(default="", max_length=500)
    steps: list[CaseStep] = Field(default_factory=list, max_length=1000)


class RevisionRequest(BaseModel):
    revision: int = Field(ge=1)


class AddSourcesRequest(RevisionRequest):
    source_indexes: list[int] = Field(min_length=1, max_length=1000)


class EditDraftRequest(RevisionRequest):
    content: CaseContent


class DraftSelectionRequest(RevisionRequest):
    draft_ids: list[UUID] = Field(min_length=2, max_length=1000)


class DraftActionRequest(RevisionRequest):
    action: Literal["remove", "unmerge", "confirm-not-created"]


class PushCasesRequest(RevisionRequest):
    request_id: UUID
    draft_ids: list[UUID] = Field(min_length=1, max_length=1000)


class ReconcileCaseRequest(BaseModel):
    case_id: int = Field(gt=0)
