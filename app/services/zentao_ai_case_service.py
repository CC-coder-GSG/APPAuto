"""Persistent personal drafts and conservative, recoverable ZenTao writes."""
from __future__ import annotations

import html
import json
import logging
import re
import uuid
from datetime import timedelta
from typing import Any

from fastapi import HTTPException
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models.story_ai_result import StoryAIResult
from app.models.user_zentao_binding import UserZentaoBinding
from app.models.zentao_ai_case import AICaseDraft, AICasePushBatch, AICasePushItem, AICaseWorkspace
from app.schemas.zentao_ai_case import CaseContent
from app.services import zentao_ai_service
from app.services.zentao_client_service import ZentaoAPIError
from app.services.zentao_testcase_service import ZentaoTestCaseService
from app.utils.time_utils import local_now

logger = logging.getLogger(__name__)
TYPES = {"feature": "功能测试", "performance": "性能测试", "config": "配置相关", "install": "安装部署", "security": "安全相关", "interface": "接口测试", "unit": "单元测试", "other": "其他"}
STAGES = {"unittest": "单元测试阶段", "feature": "功能测试阶段", "intergrate": "集成测试阶段", "system": "系统测试阶段", "smoke": "冒烟测试阶段", "bvt": "版本验证阶段"}
EDITABLE = {"draft", "failed"}


def dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def loads(value: str | None, default: Any = None) -> Any:
    try:
        return json.loads(value) if value else default
    except (ValueError, TypeError):
        return default


def uid() -> str:
    return str(uuid.uuid4())


def as_text(value: Any) -> str:
    if isinstance(value, list):
        return "\n".join(str(x) for x in value if x is not None)
    return str(value or "").strip()


def enum_value(value: Any, options: dict) -> str:
    text = as_text(value)
    return text if text in options else next((key for key, label in options.items() if text == label), "")


def priority_value(value: Any) -> int | None:
    text = as_text(value).lower()
    mapped = {"最高": 1, "紧急": 1, "高": 2, "high": 2, "中": 3, "medium": 3, "低": 4, "low": 4}.get(text)
    if mapped:
        return mapped
    try:
        number = int(text.removeprefix("p"))
        return number if 1 <= number <= 4 else None
    except ValueError:
        return None


def keywords(value: Any) -> str:
    raw = "；".join(str(x) for x in value) if isinstance(value, list) else as_text(value)
    return "；".join(dict.fromkeys(x.strip() for x in re.split(r"[；;,，\n]+", raw) if x.strip()))


def source_cases(result: StoryAIResult) -> list[dict]:
    raw = loads(result.raw_ai_result_json, {})
    cases = raw.get("test_cases") if isinstance(raw, dict) else None
    if isinstance(cases, list) and cases:
        return [x for x in cases if isinstance(x, dict)]
    return [{"title": result.title, "case_type": result.case_type, "priority": result.priority,
             "precondition": result.precondition, "keywords": result.keywords,
             "steps": loads(result.steps_json, [])}]


def normalize_case(raw: dict, module_id: int = 0) -> dict:
    steps = raw.get("steps") or []
    if isinstance(steps, str):
        steps = loads(steps, [])
    return {
        "title": as_text(raw.get("title")),
        "case_type": enum_value(raw.get("case_type") or raw.get("type"), TYPES),
        "priority": priority_value(raw.get("priority") or raw.get("pri")),
        "module_id": module_id,
        "stage": enum_value(raw.get("stage"), STAGES),
        "precondition": as_text(raw.get("precondition")),
        "keywords": keywords(raw.get("keywords")),
        "steps": [{"step": as_text(s.get("step", s.get("desc", ""))), "expected": as_text(s.get("expected", s.get("expect", "")))}
                  if isinstance(s, dict) else {"step": as_text(s), "expected": ""}
                  for s in steps] if isinstance(steps, list) else [],
    }


def binding_url(db: Session, user_id: int) -> str:
    binding = db.query(UserZentaoBinding).filter_by(user_id=user_id).first()
    if not binding or not binding.base_url:
        raise HTTPException(400, "请先绑定禅道账号")
    return binding.base_url.rstrip("/")


def remote_call(db: Session, user_id: int, fn):
    try:
        return zentao_ai_service._call_with_401_retry(user_id, db, fn)
    except zentao_ai_service.ZentaoAIServiceError as exc:
        raise HTTPException(exc.status_code, exc.message) from exc
    except ZentaoAPIError as exc:
        # Remote auth failures must not log the user out of this application.
        code = 400 if exc.status_code in (401, 403) else 502
        raise HTTPException(code, f"禅道访问失败：{exc.message}") from exc


def context(db: Session, user_id: int, story_id: int) -> dict:
    def fetch(client):
        raw = client.get_story(story_id)
        if not isinstance(raw, dict):
            raise HTTPException(404, "当前禅道账号无法访问该需求")
        story = zentao_ai_service._normalize_story_detail(raw)
        if story.get("id") != story_id or not story.get("product"):
            raise HTTPException(400, "无法确定需求所属产品，请检查禅道返回数据")
        options = {0: "根模块"}
        if story.get("module"):
            options[story["module"]] = story.get("module_name") or f"需求模块 {story['module']}"
        complete = False
        try:
            meta = client.get_create_testcase_meta(story["product"]) or {}
            field_meta = meta.get("data") if isinstance(meta.get("data"), dict) else meta
            menu = field_meta.get("moduleOptionMenu") or field_meta.get("modules")
            if isinstance(menu, dict):
                for key, value in menu.items():
                    try:
                        module = int(key)
                    except (ValueError, TypeError):
                        continue
                    if module >= 0:
                        options[module] = as_text(value.get("name") if isinstance(value, dict) else value)
                complete = True
        except ZentaoAPIError as exc:
            if exc.status_code == 401:
                raise
            logger.info("AI case module metadata unavailable product=%s", story["product"])
        return {"story_id": story_id, "story_title": story["title"], "product_id": story["product"],
                "product_name": story.get("product_name") or f"产品 {story['product']}",
                "module_id": story.get("module") or 0,
                "modules": [{"id": key, "name": name} for key, name in options.items()],
                "module_options_complete": complete, "types": TYPES, "stages": STAGES}
    return remote_call(db, user_id, fetch)


def get_workspace(db: Session, user_id: int, workspace_id: str) -> AICaseWorkspace:
    row = db.query(AICaseWorkspace).filter_by(id=workspace_id, user_id=user_id).first()
    if not row:
        raise HTTPException(404, "未找到你的用例草稿")
    if row.base_url != binding_url(db, user_id):
        raise HTTPException(409, "禅道绑定地址已变化，请切回草稿对应的禅道后操作")
    return row


def open_workspace(db: Session, user_id: int, result_id: int) -> dict:
    result = db.get(StoryAIResult, result_id)
    if not result or result.ai_status != "success":
        raise HTTPException(404, "没有可整理的 AI 结果")
    base_url = binding_url(db, user_id)
    row = db.query(AICaseWorkspace).filter_by(user_id=user_id, result_id=result_id, base_url=base_url).first()
    if not row:
        ctx = context(db, user_id, result.story_id)
        row = AICaseWorkspace(id=uid(), user_id=user_id, result_id=result_id, base_url=base_url,
                              product_id=ctx["product_id"], story_id=result.story_id, context_json=dump(ctx))
        db.add(row)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            row = db.query(AICaseWorkspace).filter_by(user_id=user_id, result_id=result_id, base_url=base_url).one()
    return workspace_view(db, row)


def draft_view(draft: AICaseDraft, base_url: str) -> dict:
    return {"id": draft.id, "revision": draft.revision, "content": loads(draft.content_json),
            "sources": loads(draft.sources_json, []), "parents": loads(draft.parents_json, []),
            "submit_status": draft.submit_status, "error": draft.error, "warning": draft.warning,
            "remote_case_id": draft.remote_case_id,
            "remote_url": f"{base_url}/testcase-view-{draft.remote_case_id}.html" if draft.remote_case_id else None}


def workspace_view(db: Session, row: AICaseWorkspace) -> dict:
    db.refresh(row)
    drafts = db.query(AICaseDraft).filter_by(workspace_id=row.id, active=True).order_by(AICaseDraft.position, AICaseDraft.created_at, AICaseDraft.id).all()
    result = db.get(StoryAIResult, row.result_id)
    ctx = loads(row.context_json, {})
    return {"id": row.id, "result_id": row.result_id, "revision": row.revision, "base_url": row.base_url,
            "context": ctx,
            "sources": [{"index": i, "content": normalize_case(case, ctx.get("module_id", 0))} for i, case in enumerate(source_cases(result))],
            "drafts": [draft_view(d, row.base_url) for d in drafts]}


def lock_workspace(db: Session, row: AICaseWorkspace, revision: int) -> None:
    count = db.execute(update(AICaseWorkspace).where(AICaseWorkspace.id == row.id, AICaseWorkspace.revision == revision)
                       .values(revision=revision + 1).execution_options(synchronize_session=False)).rowcount
    if count != 1:
        db.rollback()
        raise HTTPException(409, "草稿已在其他窗口变化，请刷新后再操作")
    db.expire_all()


def selected_drafts(db: Session, workspace_id: str, ids: list[str], editable: bool = True) -> list[AICaseDraft]:
    if len(set(ids)) != len(ids):
        raise HTTPException(400, "不能重复选择同一条草稿")
    by_id = {d.id: d for d in db.query(AICaseDraft).filter(AICaseDraft.workspace_id == workspace_id,
                                                        AICaseDraft.id.in_(ids), AICaseDraft.active.is_(True)).all()}
    if len(by_id) != len(ids):
        raise HTTPException(404, "选中的草稿不存在或已被合并／移出")
    drafts = [by_id[key] for key in ids]
    if editable and any(d.submit_status not in EDITABLE for d in drafts):
        raise HTTPException(409, "已提交、正在提交或结果待核实的草稿不能修改或再次提交")
    return drafts


def add_sources(db: Session, row: AICaseWorkspace, revision: int, indexes: list[int]) -> dict:
    lock_workspace(db, row, revision)
    cases = source_cases(db.get(StoryAIResult, row.result_id))
    if any(i < 0 or i >= len(cases) for i in indexes) or len(set(indexes)) != len(indexes):
        raise HTTPException(400, "选择的小用例无效")
    active = db.query(AICaseDraft).filter_by(workspace_id=row.id, active=True).all()
    used = {i for d in active for i in loads(d.sources_json, [])}
    # Removed failed/draft cases may be re-added; submitted sources may never be re-added.
    for d in db.query(AICaseDraft).filter_by(workspace_id=row.id).all():
        if d.submit_status not in EDITABLE:
            used.update(loads(d.sources_json, []))
    if used.intersection(indexes):
        raise HTTPException(409, "部分小用例已加入草稿或已经提交，不能重复加入")
    module_id = loads(row.context_json, {}).get("module_id", 0)
    for index in indexes:
        db.add(AICaseDraft(id=uid(), workspace_id=row.id, position=index, content_json=dump(normalize_case(cases[index], module_id)), sources_json=dump([index])))
    db.commit()
    return workspace_view(db, row)


def edit_draft(db: Session, row: AICaseWorkspace, revision: int, draft_id: str, content: CaseContent) -> dict:
    lock_workspace(db, row, revision)
    draft = selected_drafts(db, row.id, [draft_id])[0]
    draft.content_json = dump(content.model_dump())
    draft.revision += 1
    db.commit()
    return workspace_view(db, row)


def merge_drafts(db: Session, row: AICaseWorkspace, revision: int, ids: list[str]) -> dict:
    lock_workspace(db, row, revision)
    drafts = selected_drafts(db, row.id, ids)
    contents = [loads(d.content_json) for d in drafts]
    merged = dict(contents[0])
    merged["title"] = " / ".join(c["title"] for c in contents)
    merged["keywords"] = keywords("；".join(c["keywords"] for c in contents))
    merged["priority"] = min((c["priority"] for c in contents if c["priority"]), default=None)
    # Different field values remain unresolved instead of silently picking a winner.
    for field in ("case_type", "stage", "module_id"):
        if len({c[field] for c in contents}) > 1:
            merged[field] = None if field == "module_id" else ""
    lines = [list(dict.fromkeys(x.strip() for x in c["precondition"].splitlines() if x.strip())) for c in contents]
    common = [x for x in lines[0] if all(x in part for part in lines[1:])]
    sections = ["共同条件：\n" + "\n".join(common)] if common else []
    for i, part in enumerate(lines):
        unique = [x for x in part if x not in common]
        if unique:
            sections.append(f"场景：{contents[i]['title']}\n" + "\n".join(unique))
    merged["precondition"] = "\n\n".join(sections)
    merged["steps"] = [{"step": f"【{c['title']}】{s['step']}", "expected": s["expected"]} for c in contents for s in c["steps"]]
    sources = list(dict.fromkeys(i for d in drafts for i in loads(d.sources_json, [])))
    for draft in drafts:
        draft.active = False
    db.add(AICaseDraft(id=uid(), workspace_id=row.id, position=min(d.position for d in drafts), content_json=dump(merged), sources_json=dump(sources), parents_json=dump(ids),
                       warning="合并后请检查各场景前置条件及状态切换；类型、模块、阶段不同的字段需要重新选择。"))
    db.commit()
    return workspace_view(db, row)


def draft_action(db: Session, row: AICaseWorkspace, revision: int, draft_id: str, action: str) -> dict:
    lock_workspace(db, row, revision)
    if action == "confirm-not-created":
        draft = selected_drafts(db, row.id, [draft_id], editable=False)[0]
        if draft.submit_status != "unknown" or draft.remote_case_id:
            raise HTTPException(409, "只能恢复尚未关联禅道编号的待核实条目")
        item = db.get(AICasePushItem, draft.push_item_id)
        if not item or item.status != "unknown":
            raise HTTPException(409, "提交状态已变化，请刷新后核实")
        set_outcome(db, item, "failed", error="用户已在禅道核实未创建，可修改后重新提交")
        return workspace_view(db, row)
    draft = selected_drafts(db, row.id, [draft_id])[0]
    if action == "unmerge":
        parents = loads(draft.parents_json, [])
        if not parents:
            raise HTTPException(400, "这条草稿没有可恢复的合并来源")
        for parent_id in parents:
            parent = db.get(AICaseDraft, parent_id)
            if not parent or parent.workspace_id != row.id or parent.active:
                raise HTTPException(409, "合并来源状态已变化，无法撤销")
            parent.active = True
    draft.active = False
    db.commit()
    return workspace_view(db, row)


def payload_for(content: dict, row: AICaseWorkspace, ctx: dict) -> dict:
    try:
        clean = CaseContent.model_validate(content).model_dump()
    except ValueError as exc:
        raise HTTPException(400, "草稿字段无效或超出长度限制，请编辑后提交") from exc
    if not clean["title"].strip() or clean["case_type"] not in TYPES or clean["priority"] is None:
        raise HTTPException(400, "请补齐标题、用例类型和优先级")
    if clean["stage"] and clean["stage"] not in STAGES:
        raise HTTPException(400, "请选择有效的测试阶段")
    if clean["module_id"] not in {x["id"] for x in ctx["modules"]}:
        raise HTTPException(400, "模块不属于当前产品的可用模块，请重新选择")
    if not clean["steps"] or any(not s["step"].strip() or not s["expected"].strip() for s in clean["steps"]):
        raise HTTPException(400, "每条用例至少需要一步，且每步操作和预期都必须填写")
    return {"title": clean["title"].strip(), "type": clean["case_type"], "pri": clean["priority"],
            "story": row.story_id, "module": clean["module_id"], "stage": clean["stage"],
            "precondition": clean["precondition"], "keywords": keywords(clean["keywords"]),
            "steps": [{"desc": s["step"], "expect": s["expected"]} for s in clean["steps"]]}


def push_cases(db: Session, user_id: int, row: AICaseWorkspace, revision: int, request_id: str, ids: list[str]) -> dict:
    existing = db.query(AICasePushBatch).filter_by(user_id=user_id, request_id=request_id).first()
    if existing:
        previous_ids = {x.draft_id for x in db.query(AICasePushItem).filter_by(batch_id=existing.id).all()}
        if existing.workspace_id != row.id or previous_ids != set(ids):
            raise HTTPException(409, "提交请求编号已用于其他内容")
        return batch_view(db, user_id, existing.id)
    ctx = context(db, user_id, row.story_id)
    if ctx["product_id"] != row.product_id:
        raise HTTPException(409, "需求所属产品已变化，请重新整理草稿")
    lock_workspace(db, row, revision)
    drafts = selected_drafts(db, row.id, ids)
    payloads = [payload_for(loads(d.content_json), row, ctx) for d in drafts]
    batch = AICasePushBatch(id=uid(), workspace_id=row.id, user_id=user_id, request_id=request_id)
    db.add(batch)
    db.flush()
    row.context_json = dump(ctx)
    for position, (draft, payload) in enumerate(zip(drafts, payloads)):
        item = AICasePushItem(id=uid(), batch_id=batch.id, draft_id=draft.id, position=position,
                             snapshot_json=dump({"revision": draft.revision, "payload": payload}))
        db.add(item)
        draft.push_item_id = item.id
        draft.submit_status = "queued"
        draft.error = None
        draft.warning = None
    db.commit()
    return batch_view(db, user_id, batch.id)


def batch_view(db: Session, user_id: int, batch_id: str) -> dict:
    batch = db.query(AICasePushBatch).filter_by(id=batch_id, user_id=user_id).first()
    if not batch:
        raise HTTPException(404, "未找到你的提交批次")
    items = db.query(AICasePushItem).filter_by(batch_id=batch.id).order_by(AICasePushItem.position).all()
    counts = {status: sum(x.status == status for x in items) for status in ("queued", "submitting", "succeeded", "failed", "unknown")}
    return {"id": batch.id, "workspace_id": batch.workspace_id, "total": len(items), **counts,
            "items": [{"id": x.id, "draft_id": x.draft_id, "status": x.status, "remote_case_id": x.remote_case_id,
                       "error": x.error, "warning": x.warning} for x in items]}


def plain(value: Any) -> str:
    text = as_text(value)
    text = re.sub(r"<br\s*/?>|</p>", "\n", text, flags=re.I)
    return html.unescape(re.sub(r"<[^>]+>", "", text)).strip().replace("\r\n", "\n")


def unwrap_case(raw: Any) -> dict:
    if not isinstance(raw, dict):
        return {}
    for key in ("testcase", "case", "data"):
        if isinstance(raw.get(key), dict):
            return unwrap_case(raw[key])
    return raw


def verify_case(raw: Any, payload: dict, product_id: int) -> list[str]:
    case = unwrap_case(raw)
    mismatches = []
    for field, expected in {"product": product_id, "story": payload["story"], "module": payload["module"], "pri": payload["pri"]}.items():
        value = case.get(field)
        value = value.get("id") if isinstance(value, dict) else value
        if str(value) != str(expected):
            mismatches.append(field)
    for field in ("title", "type", "precondition", "keywords", "stage"):
        if plain(case.get(field)) != plain(payload.get(field)):
            mismatches.append(field)
    actual_steps = case.get("steps") or []
    if isinstance(actual_steps, dict):
        actual_steps = list(actual_steps.values())
    if not isinstance(actual_steps, list) or [(plain(s.get("desc")), plain(s.get("expect"))) for s in actual_steps if isinstance(s, dict)] != [(plain(s["desc"]), plain(s["expect"])) for s in payload["steps"]]:
        mismatches.append("steps")
    return mismatches


def set_outcome(db: Session, item: AICasePushItem, status: str, *, case_id: int | None = None, error: str | None = None, warning: str | None = None) -> None:
    item.status, item.error, item.warning = status, error, warning
    if case_id:
        item.remote_case_id = case_id
    draft = db.get(AICaseDraft, item.draft_id)
    if draft and draft.push_item_id == item.id:
        draft.submit_status, draft.error, draft.warning = status, error, warning
        if case_id:
            draft.remote_case_id = case_id
    db.commit()


def sync_created(db: Session, row: AICaseWorkspace, item: AICasePushItem, user_id: int) -> None:
    try:
        raw = remote_call(db, user_id, lambda client: client.get_testcase(item.remote_case_id))
        if not raw:
            raise ValueError("未取得用例详情")
        mismatches = verify_case(raw, loads(item.snapshot_json)["payload"], row.product_id)
        service = ZentaoTestCaseService(db)
        normalized = service._normalize_testcase(raw, base_url=row.base_url)
        if not normalized or normalized["zentao_case_numeric_id"] != item.remote_case_id:
            raise ValueError("返回的用例编号不一致")
        service._upsert_case(normalized, sync_source="ai_case_push")
        warning = "已创建，但字段回读不一致：" + "、".join(mismatches) if mismatches else None
        set_outcome(db, item, "succeeded", warning=warning)
    except Exception:
        db.rollback()
        item = db.get(AICasePushItem, item.id)
        set_outcome(db, item, "succeeded", warning="禅道已创建，本地回读／同步待恢复；可点击重新同步，勿重复创建。")


def run_batch(batch_id: str, session_factory=SessionLocal) -> None:
    with session_factory() as db:
        batch = db.get(AICasePushBatch, batch_id)
        if not batch:
            return
        row = db.get(AICaseWorkspace, batch.workspace_id)
        item_ids = [x.id for x in db.query(AICasePushItem).filter_by(batch_id=batch.id, status="queued").order_by(AICasePushItem.position).all()]
        for item_id in item_ids:
            # Persist the claim before issuing any write; multiple workers cannot send it twice.
            claimed = db.execute(update(AICasePushItem).where(AICasePushItem.id == item_id, AICasePushItem.status == "queued")
                                 .values(status="submitting", updated_at=local_now()).execution_options(synchronize_session=False)).rowcount
            db.commit()
            db.expire_all()
            if not claimed:
                continue
            item = db.get(AICasePushItem, item_id)
            draft = db.get(AICaseDraft, item.draft_id)
            if draft.push_item_id != item.id or draft.submit_status != "queued":
                set_outcome(db, item, "failed", error="草稿提交状态已变化，未发送请求")
                continue
            set_outcome(db, item, "submitting")
            # Preflight failures are definite failures: no create request has been sent.
            try:
                get_workspace(db, batch.user_id, row.id)
                ctx = context(db, batch.user_id, row.story_id)
                payload = loads(item.snapshot_json)["payload"]
                if ctx["product_id"] != row.product_id or payload["module"] not in {x["id"] for x in ctx["modules"]}:
                    raise HTTPException(409, "需求产品或模块已变化")
                client = zentao_ai_service._get_client(batch.user_id, db)
                if client is None or client.base_url != row.base_url:
                    raise HTTPException(400, "无法获取禅道凭证")
            except Exception as exc:
                db.rollback()
                set_outcome(db, item, "failed", error=str(getattr(exc, "detail", "禅道提交前校验失败")))
                continue
            created_id = None
            try:
                try:
                    created = client.create_testcase(row.product_id, payload)
                except ZentaoAPIError as exc:
                    if exc.status_code != 401:
                        raise
                    zentao_ai_service.invalidate_token(batch.user_id, db)
                    client = zentao_ai_service._get_client(batch.user_id, db)
                    if client is None or client.base_url != row.base_url:
                        raise ZentaoAPIError(401, "禅道 Token 刷新失败")
                    created = client.create_testcase(row.product_id, payload)
                created_id = created["id"]
                # Save the remote ID before readback/sync. Those failures must never resend the POST.
                set_outcome(db, item, "succeeded", case_id=created["id"], warning="禅道已创建，等待回读校验")
                sync_created(db, row, item, batch.user_id)
            except ZentaoAPIError as exc:
                db.rollback()
                definite_failure = 400 <= exc.status_code < 500 and exc.status_code not in (408, 429)
                set_outcome(db, db.get(AICasePushItem, item_id), "failed" if definite_failure else "unknown",
                            error=exc.message if definite_failure else "创建结果待核实：" + exc.message)
            except Exception:
                db.rollback()
                logger.exception("AI case push outcome uncertain item=%s", item_id)
                if created_id:
                    set_outcome(db, db.get(AICasePushItem, item_id), "succeeded", case_id=created_id,
                                warning="禅道已创建，本地同步待恢复")
                else:
                    set_outcome(db, db.get(AICasePushItem, item_id), "unknown", error="创建结果待核实，请在禅道确认后关联用例编号")


def recover_stale_items(db: Session) -> None:
    """Never resend an interrupted POST. Queued work can explicitly be resumed."""
    cutoff = local_now() - timedelta(minutes=15)
    for item in db.query(AICasePushItem).filter(AICasePushItem.status == "submitting", AICasePushItem.updated_at < cutoff).all():
        set_outcome(db, item, "unknown", error="提交执行已中断，远端创建结果待核实")


def reconcile(db: Session, user_id: int, row: AICaseWorkspace, draft_id: str, case_id: int | None = None) -> dict:
    draft = selected_drafts(db, row.id, [draft_id], editable=False)[0]
    item = db.get(AICasePushItem, draft.push_item_id) if draft.push_item_id else None
    if not item or draft.submit_status not in ("succeeded", "unknown"):
        raise HTTPException(409, "该草稿没有可核实或重新同步的提交结果")
    if draft.submit_status == "unknown":
        if not case_id:
            raise HTTPException(400, "请填写在禅道核实后的用例编号")
        raw = remote_call(db, user_id, lambda client: client.get_testcase(case_id))
        actual = unwrap_case(raw)
        if str(actual.get("id")) != str(case_id) or verify_case(raw, loads(item.snapshot_json)["payload"], row.product_id):
            raise HTTPException(400, "用例内容或归属与本次提交不一致，不能关联")
        set_outcome(db, item, "succeeded", case_id=case_id, warning="已核实远端用例，等待同步")
    sync_created(db, row, item, user_id)
    return workspace_view(db, row)
