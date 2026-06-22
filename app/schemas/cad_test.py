from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class CadBoardPayload(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: Optional[str] = None


class CadVersionPayload(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class CadColumnPayload(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class CadItemPayload(BaseModel):
    seq: Optional[int] = None
    title: Optional[str] = Field(default=None, max_length=255)
    zentao_bug_id: Optional[int] = None


class CadRecordPayload(BaseModel):
    item_id: int
    version_id: int
    normal_count: int = 0
    abnormal_count: int = 0
    description: Optional[str] = None
    # 自定义列值：{ "<custom_column_id>": "值" }
    custom_values: Optional[dict[str, str]] = None


class CadFolderPayload(BaseModel):
    name: str = Field(min_length=1, max_length=255)


class CadReorderPayload(BaseModel):
    # 有序 id 列表，按数组顺序写回 sort_order
    ids: list[int] = Field(default_factory=list)
