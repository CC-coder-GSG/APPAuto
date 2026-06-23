"""
对外开放的「机器人 API」（企业微信 AI 机器人 / 第三方集成）。

鉴权：固定密钥头 X-Bot-Api-Key，与服务端 APP_BOT_API_KEY 比对（常数时间）。
未配置 APP_BOT_API_KEY 时全部端点返回 503，避免误开放到公网。

与用户登录态（JWT，单会话、会过期）解耦：机器人持长期密钥即可稳定调用。
所有路径统一前缀 /api/bot/v1，便于反向代理/网关按前缀单独放行或限流。
"""
from __future__ import annotations

import secrets
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.core.config import settings
from app.services.bot_api_service import BotApiService

router = APIRouter(prefix="/api/bot/v1", tags=["bot-api"])


def verify_bot_api_key(x_bot_api_key: Optional[str] = Header(default=None, alias="X-Bot-Api-Key")) -> None:
    server_key = (settings.bot_api_key or "").strip()
    if not server_key:
        raise HTTPException(status_code=503, detail="机器人 API 未启用（服务端未配置 APP_BOT_API_KEY）")
    provided = (x_bot_api_key or "").strip()
    if not provided or not secrets.compare_digest(provided, server_key):
        raise HTTPException(status_code=401, detail="X-Bot-Api-Key 无效或缺失")


# 所有端点统一依赖密钥校验。
_guard = Depends(verify_bot_api_key)


# --------------------------------------------------------------------- 只读端点
@router.get("/ping")
def ping(_: None = _guard, db: Session = Depends(get_db)):
    """连通性 / 鉴权自检。返回应用名、版本与服务器时间。"""
    return BotApiService(db).ping()


@router.get("/softwares")
def list_softwares(_: None = _guard, db: Session = Depends(get_db)):
    """列出所有软件产品（id + 名称）。"""
    return BotApiService(db).list_softwares()


@router.get("/versions")
def list_versions(software_id: Optional[int] = None, _: None = _guard, db: Session = Depends(get_db)):
    """列出大版本及其子版本（用于查到版本 id）。可按 software_id 过滤。"""
    return BotApiService(db).list_versions(software_id)


@router.get("/resolve-version")
def resolve_version(q: str, _: None = _guard, db: Session = Depends(get_db)):
    """版本解析：把口语化版本（'V4.0.3.1' / '40315' / '40300103'）解析为候选大版本及其 id。

    - resolved=true：唯一高置信，matches[0] 即目标，可直接拿 major_version_id 调后续接口。
    - ambiguous=true：多个候选，请让用户从 matches 里指明。
    """
    return BotApiService(db).resolve_version(q)


@router.get("/version-status")
def version_status(q: str, _: None = _guard, db: Session = Depends(get_db)):
    """一问到底：按自由文本版本解析，并返回该大版本的进度+Bug+反馈紧凑汇总（单次调用、JSON 体积受控）。

    解析不唯一时返回 resolved=false + candidates 候选，便于机器人追问。
    """
    return BotApiService(db).version_status(q)


@router.get("/version-progress")
def version_progress(major_version_id: int, _: None = _guard, db: Session = Depends(get_db)):
    """某大版本的需求测试 / 用例编写 / 复测进度概览。"""
    return BotApiService(db).version_progress(major_version_id)


@router.get("/bugs/search")
def search_bugs(q: str, limit: int = 5, _: None = _guard, db: Session = Depends(get_db)):
    """搜索 Bug：纯数字按禅道 Bug id 精确匹配，否则按标题模糊匹配，返回最可能的若干候选。

    候选含 zentao_bug_id，确定目标后用 /bugs/detail 查精细内容。
    """
    return BotApiService(db).search_bugs(q, limit)


@router.get("/bugs/detail")
def bug_detail(zentao_bug_id: str, _: None = _guard, db: Session = Depends(get_db)):
    """已知禅道 Bug id 的精细信息：状态/指派/标题/受影响版本/关联需求/关闭信息等。"""
    return BotApiService(db).bug_detail(zentao_bug_id)


@router.get("/requirements/search")
def search_requirements(q: str, limit: int = 5, _: None = _guard, db: Session = Depends(get_db)):
    """搜索需求：纯数字按禅道需求 id 精确匹配，否则按标题模糊匹配，返回最可能的若干候选。

    候选含 zentao_req_id（+ major_version_no），确定目标后用 /requirements/detail 查精细内容。
    """
    return BotApiService(db).search_requirements(q, limit)


@router.get("/requirements/detail")
def requirement_detail(
    zentao_req_id: str,
    major_version_id: Optional[int] = None,
    _: None = _guard,
    db: Session = Depends(get_db),
):
    """已知禅道需求 id 的精细信息。同一 id 跨多个大版本且未指定 major_version_id 时返回候选列表。"""
    return BotApiService(db).requirement_detail(zentao_req_id, major_version_id)


@router.get("/bugs/summary")
def bugs_summary(
    software_id: Optional[int] = None,
    major_version_id: Optional[int] = None,
    _: None = _guard,
    db: Session = Depends(get_db),
):
    """Bug 统计：按有效状态（活跃/已解决/已关闭/本地）与来源类型分组。"""
    return BotApiService(db).bug_summary(software_id, major_version_id)


@router.get("/feedback/summary")
def feedback_summary(
    software_id: Optional[int] = None,
    major_version_id: Optional[int] = None,
    _: None = _guard,
    db: Session = Depends(get_db),
):
    """用户反馈统计：按状态（待处理/处理中/已处理/已关闭）分组。"""
    return BotApiService(db).feedback_summary(software_id, major_version_id)


@router.get("/report/summary")
def report_summary(
    start_date: date,
    end_date: date,
    software_id: Optional[int] = None,
    major_version_id: Optional[int] = None,
    _: None = _guard,
    db: Session = Depends(get_db),
):
    """团队整体工作量报表（执行需求数、新建用例、新建/关闭 Bug、反馈处理等）。"""
    return BotApiService(db).report_summary(start_date, end_date, software_id, major_version_id)


# --------------------------------------------------------------------- 写操作端点
class BotFeedbackPayload(BaseModel):
    major_version_id: int = Field(..., description="大版本 id")
    minor_version_id: int = Field(..., description="子版本 id")
    summary: str = Field(..., min_length=1, max_length=2000, description="反馈内容")
    feedback_no: Optional[str] = Field(default=None, description="反馈编号（可选，纯数字）")


@router.post("/feedback", status_code=201)
def create_feedback(payload: BotFeedbackPayload, _: None = _guard, db: Session = Depends(get_db)):
    """创建一条用户反馈（以机器人执行身份落库，状态=待处理）。"""
    return BotApiService(db).create_feedback(
        major_version_id=payload.major_version_id,
        minor_version_id=payload.minor_version_id,
        summary=payload.summary,
        feedback_no=payload.feedback_no,
    )


class BotPushProgressPayload(BaseModel):
    major_version_id: int = Field(..., description="大版本 id")


@router.post("/push/version-progress")
async def push_version_progress(payload: BotPushProgressPayload, _: None = _guard, db: Session = Depends(get_db)):
    """触发企业微信群推送：该大版本的用例编写进度。"""
    return await BotApiService(db).push_version_progress(payload.major_version_id)
