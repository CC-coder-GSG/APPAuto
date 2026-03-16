from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator


class JenkinsBuildReportPayload(BaseModel):
    job_name: str = Field(..., description="Jenkins Job ??")
    build_number: str | int = Field(..., description="???")
    build_status: str = Field(..., description="??????? SUCCESS / FAILURE")
    version_name: str | None = Field(default=None, description="软件版本字符串")
    branch: str | None = Field(default=None, description="Git ???")
    build_url: str | None = Field(default=None, description="Jenkins ????")
    change_log: str | None = Field(default=None, description="????")

    @field_validator("job_name", mode="before")
    @classmethod
    def validate_job_name(cls, value: Any) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError("job_name ????")
        return text

    @field_validator("build_number", mode="before")
    @classmethod
    def validate_build_number(cls, value: Any) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError("build_number ????")
        return text

    @field_validator("build_status", mode="before")
    @classmethod
    def validate_build_status(cls, value: Any) -> str:
        text = str(value or "").strip().upper()
        if not text:
            raise ValueError("build_status ????")
        return text

    @field_validator("version_name", "branch", "build_url", "change_log", mode="before")
    @classmethod
    def normalize_optional_text(cls, value: Any) -> str | None:
        text = str(value or "").strip()
        return text or None


__all__ = ["JenkinsBuildReportPayload"]
