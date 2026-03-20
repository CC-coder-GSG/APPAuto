from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _clean_optional_text(value: Any) -> str | None:
    text = str(value or '').strip()
    return text or None


class ZentaoSyncDraftPayload(BaseModel):
    model_config = ConfigDict(extra='allow')

    productId: str | None = None
    productName: str | None = None
    projectId: str | None = None
    projectName: str | None = None
    openedBuildIds: list[str] = Field(default_factory=list)
    affectedVersion: str | None = None
    bugTitle: str | None = None
    executionId: str | None = None
    executionName: str | None = None
    requirementId: str | None = None
    requirementName: str | None = None
    creatorName: str | None = None
    caseTitle: str | None = None
    sourceType: str | None = None
    linkedCaseId: str | None = None
    linkedCaseLabel: str | None = None
    linkedCaseHref: str | None = None

    @field_validator(
        'productId',
        'productName',
        'projectId',
        'projectName',
        'affectedVersion',
        'bugTitle',
        'executionId',
        'executionName',
        'requirementId',
        'requirementName',
        'creatorName',
        'caseTitle',
        'sourceType',
        'linkedCaseId',
        'linkedCaseLabel',
        'linkedCaseHref',
        mode='before',
    )
    @classmethod
    def normalize_optional_text(cls, value: Any) -> str | None:
        return _clean_optional_text(value)

    @field_validator('openedBuildIds', mode='before')
    @classmethod
    def normalize_opened_build_ids(cls, value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        text = str(value).strip()
        return [text] if text else []


class ZentaoSyncResultPayload(BaseModel):
    model_config = ConfigDict(extra='allow')

    zentaoBugId: str | None = None
    zentaoBugUrl: str | None = None
    zentaoCaseId: str | None = None
    zentaoCaseUrl: str | None = None

    @field_validator('zentaoBugId', 'zentaoBugUrl', 'zentaoCaseId', 'zentaoCaseUrl', mode='before')
    @classmethod
    def normalize_optional_text(cls, value: Any) -> str | None:
        return _clean_optional_text(value)


class ZentaoBrowserSyncPayload(BaseModel):
    model_config = ConfigDict(extra='allow')

    entityType: Literal['bug', 'testcase']
    action: str = 'create'
    source: str | None = None
    clientRecordId: str | None = None
    capturedAt: int | str | None = None
    topHref: str | None = None
    pageUrl: str | None = None
    pageType: str | None = None
    scriptVersion: str | None = None
    pushMessage: str | None = None
    operatorName: str | None = None
    draft: ZentaoSyncDraftPayload = Field(default_factory=ZentaoSyncDraftPayload)
    result: ZentaoSyncResultPayload = Field(default_factory=ZentaoSyncResultPayload)

    @field_validator('action', mode='before')
    @classmethod
    def normalize_action(cls, value: Any) -> str:
        text = str(value or '').strip().lower()
        if not text:
            raise ValueError('action 不能为空')
        return text

    @field_validator('source', 'clientRecordId', 'topHref', 'pageUrl', 'pageType', 'scriptVersion', 'pushMessage', 'operatorName', mode='before')
    @classmethod
    def normalize_optional_text(cls, value: Any) -> str | None:
        return _clean_optional_text(value)

    @field_validator('capturedAt', mode='before')
    @classmethod
    def normalize_captured_at(cls, value: Any) -> int | None:
        if value is None or value == '':
            return None
        try:
            return int(str(value).strip())
        except Exception as exc:  # pragma: no cover
            raise ValueError('capturedAt 必须是时间戳整数') from exc


__all__ = ['ZentaoBrowserSyncPayload']
