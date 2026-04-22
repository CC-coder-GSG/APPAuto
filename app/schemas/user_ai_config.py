from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class UserAIConfigOut(BaseModel):
    """Public view — never contains plaintext api_key."""

    model_config = ConfigDict(from_attributes=True)

    provider_name: str = "deepseek"
    is_enabled: bool = False
    configured: bool = False
    api_key_masked: str | None = None
    updated_at: datetime | None = None


class UserAIConfigUpsert(BaseModel):
    """Payload for POST /user/ai-config — caller sends plaintext key once."""

    provider_name: str = Field(default="deepseek", max_length=40)
    api_key: str = Field(..., min_length=8, max_length=256)
    is_enabled: bool = True


class UserAIConfigValidateRequest(BaseModel):
    """POST /user/ai-config/validate — either use stored key (api_key empty)
    or validate a brand-new one provided inline before saving."""

    provider_name: str = Field(default="deepseek", max_length=40)
    api_key: str | None = Field(default=None, max_length=256)


class UserAIConfigValidateResponse(BaseModel):
    ok: bool
    message: str = ""
    provider_name: str = "deepseek"


__all__ = [
    "UserAIConfigOut",
    "UserAIConfigUpsert",
    "UserAIConfigValidateRequest",
    "UserAIConfigValidateResponse",
]
