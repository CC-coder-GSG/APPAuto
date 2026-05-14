"""
Jenkins 构建 → 禅道 build 自动写回。

收到 Jenkins build-report 后，本服务负责把对应小版本写到禅道每一个匹配的执行下面。

策略（按用户需求）：
  - 每个禅道执行里"占位 build"是名字带 'xxxx' 的那一条（如 4.0.3.1.26xxxx(4030xxxx)）。
  - 新版本进来时：
      * 找到该执行的占位 build → rename 成本次的真实版本名
      * 没有占位 build → 直接 create 一条真实版本名的 build
      * 再 create 一条新的占位 build，保持执行下"最新"那一条总是占位
  - 一个 job_name 在本地可能对应多个 MAJOR（如 s4031 / s4031定制），全部推送一遍。

错误处理：任何一步异常都不抛，记录到 BuildRecord.zentao_push_* 字段。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from app.models import BuildRecord, Version
from app.models.enums import VersionType
from app.services.zentao_client_service import ZentaoClient
from app.services.zentao_system_client import get_system_zentao_client
from app.services.zentao_utils import (
    is_placeholder_name,
    make_placeholder_name,
    parse_job_name_to_major_version_no,
)
from app.utils.time_utils import local_now

logger = logging.getLogger(__name__)


@dataclass
class PushExecutionResult:
    execution_id: int
    major_version_no: str
    renamed_build_id: Optional[int] = None
    created_build_id: Optional[int] = None
    new_placeholder_build_id: Optional[int] = None
    error: Optional[str] = None


@dataclass
class PushResult:
    status: str = "ok"          # ok | no_binding | no_execution | not_success | empty_version_name | error
    message: str = ""
    executions: list[PushExecutionResult] = field(default_factory=list)


def push_build_to_zentao(db: Session, record: BuildRecord) -> PushResult:
    """
    主入口。注意：写完后调用方需要把 PushResult 写到 record.zentao_push_* 并 commit。
    本函数自身只对禅道做调用，并通过 db 读本地 majors。
    """
    out = PushResult()

    if (record.build_status or "").upper() != "SUCCESS":
        out.status = "not_success"
        out.message = f"构建状态 {record.build_status}，不推送禅道"
        return out

    version_name = (record.version_name or "").strip()
    if not version_name:
        out.status = "empty_version_name"
        out.message = "version_name 为空，不推送禅道"
        return out

    major_version_no = parse_job_name_to_major_version_no(record.job_name or "")
    if not major_version_no:
        out.status = "no_execution"
        out.message = f"job_name {record.job_name} 无法解析大版本号"
        return out

    majors = (
        db.query(Version)
        .filter(
            Version.version_no == major_version_no,
            Version.version_type == VersionType.MAJOR,
        )
        .all()
    )
    bound_majors = [m for m in majors if m.zentao_execution_id]
    if not bound_majors:
        out.status = "no_execution"
        out.message = f"未找到大版本 {major_version_no} 或其没有绑定禅道执行"
        return out

    client = get_system_zentao_client(db)
    if not client:
        out.status = "no_binding"
        out.message = "找不到可用的禅道账号绑定，无法写入禅道"
        return out

    errors: list[str] = []
    for major in bound_majors:
        item = _push_one_execution(client, major, version_name)
        out.executions.append(item)
        if item.error:
            errors.append(f"执行 {item.execution_id}: {item.error}")

    if errors and not any(e.error is None for e in out.executions):
        out.status = "error"
        out.message = "；".join(errors)
    elif errors:
        out.status = "ok"
        out.message = f"部分执行写入失败：{'；'.join(errors)}"
    else:
        ok_descs = [
            f"执行 {e.execution_id} 已写入"
            for e in out.executions
        ]
        out.status = "ok"
        out.message = "；".join(ok_descs) if ok_descs else "无需写入"
    return out


def _resolve_execution_product(client: ZentaoClient, exec_id: int) -> tuple[int | None, str | None]:
    """
    禅道 v1 `POST /executions/{id}/builds` 实际要求 body 带 `product`，否则会
    返回 200 但什么都不写（看起来"成功"但禅道侧空）。这里抓一次 execution
    detail 把 product id 拿出来。返回 (product_id, builder_account)。

    注意：禅道 v1 GET /executions/{id} 返回字段名是 `products`（复数列表），
    不是 `product`。`product`（单数）只在老版本/部分 IPD fork 上出现，留作
    fallback。两个都读不到 → 兜底再调一次 get_execution_context()。
    """
    try:
        detail = client.get(f"executions/{exec_id}")
    except Exception as exc:
        logger.warning("get execution(%s) failed: %s", exec_id, exc)
        return None, None
    if not isinstance(detail, dict):
        return None, None
    exec_obj = detail.get("execution") if isinstance(detail.get("execution"), dict) else detail
    if not isinstance(exec_obj, dict):
        return None, None

    pid = _extract_first_product_id(exec_obj.get("products"))
    if pid is None:
        pid = _extract_first_product_id(exec_obj.get("product"))
    if pid is None:
        # 兜底：用现成的 helper 再解析一次（它内部用的就是 products 复数）
        try:
            ctx = client.get_execution_context(exec_id)
            ids = ctx.get("product_ids") if isinstance(ctx, dict) else None
            if isinstance(ids, list) and ids:
                pid = _coerce_int(ids[0])
        except Exception as exc:
            logger.warning("get_execution_context(%s) failed: %s", exec_id, exc)

    builder = None
    raw_builder = exec_obj.get("PM") or exec_obj.get("openedBy")
    if isinstance(raw_builder, dict):
        builder = raw_builder.get("account") or raw_builder.get("realname")
    elif isinstance(raw_builder, str):
        builder = raw_builder
    return pid, builder


def _extract_first_product_id(raw) -> int | None:
    """从禅道返回里抓第一个 product id —— 支持 list[dict] / list[int] / dict / 标量四种 shape。"""
    if raw is None:
        return None
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict):
                pid = _coerce_int(item.get("id"))
                if pid:
                    return pid
            else:
                pid = _coerce_int(item)
                if pid:
                    return pid
        return None
    if isinstance(raw, dict):
        return _coerce_int(raw.get("id"))
    return _coerce_int(raw)


def _safe_create_build(
    client: ZentaoClient,
    exec_id: int,
    name: str,
    *,
    product_id: int | None,
    builder: str | None,
) -> tuple[int | None, str | None]:
    """
    创建 build 后做"二次验证"：调一次 list_execution_builds 用 name 精确匹配
    把真实的 build id 找回来。这样：
      - 禅道写接口返回体不带 id（v1 部分版本如此）也能拿到 id
      - 静默失败（缺 product 等）会被识别为"name 不在 list 里 → 失败"

    返回 (new_build_id, error)。
    """
    today = datetime.now().strftime("%Y-%m-%d")
    raw_response = None
    try:
        raw_response = client.create_execution_build(
            exec_id,
            name,
            product_id=product_id,
            builder=builder,
            date=today,
        )
    except Exception as exc:
        logger.warning("create build under exec=%s name=%s failed: %s", exec_id, name, exc)
        return None, f"create_execution_build 抛错：{exc}"

    # 禅道返回如果带 id，直接信任它 —— 部分禅道版本 list 接口有缓存延迟
    rid = _extract_id_from_create_response(raw_response)
    if rid:
        return rid, None

    # Re-list 验证是否真的写进去了
    try:
        builds = client.list_execution_builds(exec_id, limit=500) or []
    except Exception as exc:
        logger.warning("re-list builds(%s) after create failed: %s", exec_id, exc)
        return None, f"创建后回查 build 列表失败：{exc}"

    match = next((b for b in builds if str(b.get("name") or "").strip() == name), None)
    if match is None:
        hint = f"product_id={product_id}" if product_id else "product_id=None（未解析到）"
        # 把禅道原始返回塞进 message —— "可能缺 product 或权限"太含糊，看不到根因
        resp_preview = _preview_create_response(raw_response)
        return None, f"禅道未真正创建 build（name='{name}'，{hint}，resp={resp_preview}）"
    return _coerce_int(match.get("id")), None


def _extract_id_from_create_response(resp) -> int | None:
    """从 create 返回里抓 build id —— 兼容 {id} / {build:{id}} / {data:{id}} 多种 shape。"""
    if not isinstance(resp, dict):
        return None
    for candidate in (resp, resp.get("build"), resp.get("data")):
        if isinstance(candidate, dict):
            bid = _coerce_int(candidate.get("id"))
            if bid:
                return bid
    return None


def _preview_create_response(resp) -> str:
    """把禅道返回压成一段短字符串塞到错误消息里。"""
    if resp is None:
        return "None"
    try:
        text = str(resp)
    except Exception:
        text = repr(resp)
    return text[:200]


def _push_one_execution(client: ZentaoClient, major: Version, version_name: str) -> PushExecutionResult:
    exec_id = int(major.zentao_execution_id or 0)
    result = PushExecutionResult(execution_id=exec_id, major_version_no=major.version_no)
    if exec_id <= 0:
        result.error = "execution_id 非法"
        return result

    try:
        builds = client.list_execution_builds(exec_id, limit=500) or []
    except Exception as exc:
        logger.warning("list_execution_builds(%s) failed: %s", exec_id, exc)
        result.error = f"读取禅道 build 列表失败：{exc}"
        return result

    # 预取 execution 的 product / builder，给所有后续 create 用
    product_id, builder = _resolve_execution_product(client, exec_id)

    # 判重：禅道里如果已经有同名 build，跳过 rename/create，但仍尝试补一个占位
    for b in builds:
        if str(b.get("name") or "").strip() == version_name:
            result.created_build_id = _coerce_int(b.get("id"))
            break

    # 占位 build：名字带 xxxx 的，取列表里"最新"的一条（一般禅道返回 desc，第 0 条就是）
    placeholder = next((b for b in builds if is_placeholder_name(str(b.get("name") or ""))), None)

    if result.created_build_id is None:
        if placeholder is not None:
            placeholder_id = _coerce_int(placeholder.get("id"))
            try:
                client.update_build(placeholder_id, name=version_name)
                result.renamed_build_id = placeholder_id
            except Exception as exc:
                logger.warning("rename placeholder build %s failed: %s", placeholder_id, exc)
                result.error = f"重命名占位 build 失败：{exc}"
                return result
        else:
            new_id, err = _safe_create_build(
                client, exec_id, version_name,
                product_id=product_id, builder=builder,
            )
            if err:
                result.error = f"创建 build 失败：{err}"
                return result
            result.created_build_id = new_id

    # 创建新占位（仅当当前列表里还没有占位、或者刚把唯一占位重命名了）
    refreshed_placeholder = None
    if placeholder is None or result.renamed_build_id is not None:
        new_name = make_placeholder_name(version_name)
        if new_name and new_name != version_name:
            new_id, err = _safe_create_build(
                client, exec_id, new_name,
                product_id=product_id, builder=builder,
            )
            if err:
                # 不视为致命 — 真实版本已经写进去了
                existing = result.error or ""
                result.error = (existing + "；" if existing else "") + f"补占位 build 失败：{err}"
            else:
                result.new_placeholder_build_id = new_id
                refreshed_placeholder = new_name

    if refreshed_placeholder:
        logger.info(
            "Zentao build push ok | exec=%s real=%s placeholder=%s",
            exec_id,
            version_name,
            refreshed_placeholder,
        )
    return result


def _coerce_int(value) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def apply_push_result_to_record(record: BuildRecord, push: PushResult) -> None:
    """把 PushResult 序列化到 BuildRecord.zentao_push_* 三列。调用方负责 commit。"""
    record.zentao_push_status = push.status
    msg = (push.message or "")[:480]
    if push.executions:
        details = []
        for e in push.executions:
            parts = [f"exec={e.execution_id}"]
            if e.renamed_build_id:
                parts.append(f"renamed={e.renamed_build_id}")
            if e.created_build_id and not e.renamed_build_id:
                parts.append(f"created={e.created_build_id}")
            if e.new_placeholder_build_id:
                parts.append(f"placeholder={e.new_placeholder_build_id}")
            if e.error:
                parts.append(f"err={e.error[:80]}")
            details.append(" ".join(parts))
        joined = "|".join(details)
        msg = (msg + " | " + joined)[:480] if msg else joined[:480]
    record.zentao_push_message = msg
    record.zentao_pushed_at = local_now()


__all__ = [
    "push_build_to_zentao",
    "apply_push_result_to_record",
    "PushResult",
    "PushExecutionResult",
]
