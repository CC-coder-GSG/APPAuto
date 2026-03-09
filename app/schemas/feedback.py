from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from app.models.enums import FeedbackStatus


class FeedbackCreatePayload(BaseModel):
    feedback_no_num: Optional[str] = Field(default=None, description="仅数字部分，例如 12")
    major_version_id: int
    minor_version_id: int
    summary: str = Field(min_length=1, max_length=5000)


class FeedbackAssignPayload(BaseModel):
    assignee_id: int
    status: Optional[FeedbackStatus] = None


class FeedbackHandlePayload(BaseModel):
    handling_result: str = Field(min_length=1, max_length=5000)
    handled_major_version_id: int
    handled_minor_version_id: int
    status: FeedbackStatus = FeedbackStatus.RESOLVED


class FeedbackStatusPayload(BaseModel):
    status: FeedbackStatus


class FeedbackLinkBugPayload(BaseModel):
    bug_id: int


class FeedbackCreateBugPayload(BaseModel):
    bug_id: str

