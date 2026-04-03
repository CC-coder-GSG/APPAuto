from __future__ import annotations

import json
import logging
from datetime import datetime
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models import (
    BugSourceType,
    BugTracking,
    BrowserSyncEvent,
    Requirement,
    RequirementStatus,
    SoftwareProduct,
    TestCase,
    User,
    UserRole,
    Version,
    VersionType,
)
from app.repositories.browser_sync_repository import BrowserSyncRepository
from app.schemas.zentao_sync import ZentaoBrowserSyncPayload
from app.services.audit_service import audit
from app.services.bug_service import BugService
from app.services.requirement_service import RequirementService
from app.services.sse_service import sse_publish
from app.services.zentao_matcher import (
    convert_s_token_to_major_version,
    extract_requirement_numeric_id,
    extract_major_version_token,
    normalize_execution_name,
    normalize_requirement_title,
    parse_affected_version,
    pick_best_title_match,
)

logger = logging.getLogger(__name__)


@dataclass
class BugRouteDecision:
    route_source: str
    route_target: str
    reason: str | None
    mapped_source_type: str | None
    display_bucket: str
    requirement_id: int | None
    test_case_id: int | None
    major_version_id: int | None
    minor_version_id: int | None
    creator_user_id: int | None
    auto_apply_allowed: bool


class ZentaoSyncService:
    def __init__(self, db: Session):
        self.db = db
        self.repo = BrowserSyncRepository(db)
        self.requirement_service = RequirementService(db)
        self.bug_service = BugService(db)

    # -------- receive --------
    def receive_event(self, payload: ZentaoBrowserSyncPayload) -> dict[str, Any]:
        client_record_id = self._required_text(payload.clientRecordId, "clientRecordId 不能为空")
        existing = self.repo.get_by_client_record_id(client_record_id)
        if existing:
            logger.info("禅道同步命中接口幂等 | clientRecordId=%s eventId=%s", client_record_id, existing.id)
            return {
                "ok": True,
                "event_id": existing.id,
                "status": existing.status,
                "message": "duplicate clientRecordId",
                "duplicate_of": existing.id,
            }

        zentao_bug_id = payload.result.zentaoBugId if payload.entityType == "bug" else None
        zentao_case_id = payload.result.zentaoCaseId if payload.entityType == "testcase" else None

        duplicate_event = self.repo.find_duplicate_entity_event(
            entity_type=payload.entityType,
            zentao_bug_id=zentao_bug_id,
            zentao_case_id=zentao_case_id,
        )

        initial_status = "duplicate" if duplicate_event else "received"
        failure_reason = "命中同实体事件，标记为重复" if duplicate_event else None

        event = self.repo.create_event(
            client_record_id=client_record_id,
            entity_type=payload.entityType,
            action=payload.action,
            source=payload.source,
            captured_at=payload.capturedAt,
            received_at=datetime.utcnow(),
            top_href=payload.topHref,
            page_url=payload.pageUrl,
            page_type=payload.pageType,
            script_version=payload.scriptVersion,
            draft_json=json.dumps(payload.draft.model_dump(mode="json"), ensure_ascii=False),
            result_json=json.dumps(payload.result.model_dump(mode="json"), ensure_ascii=False),
            raw_payload_json=json.dumps(payload.model_dump(mode="json"), ensure_ascii=False),
            push_message=payload.pushMessage,
            creator_name=payload.draft.creatorName,
            operator_name=payload.operatorName,
            zentao_bug_id=zentao_bug_id,
            zentao_case_id=zentao_case_id,
            zentao_req_id=self._normalize_requirement_id(payload.draft.requirementId),
            zentao_requirement_name=payload.draft.requirementName,
            zentao_product_id=payload.draft.productId,
            zentao_product_name=payload.draft.productName,
            zentao_project_id=payload.draft.projectId,
            zentao_project_name=payload.draft.projectName,
            zentao_execution_name=payload.draft.executionName or payload.draft.executionId,
            zentao_affected_version=payload.draft.affectedVersion,
            zentao_case_title=payload.draft.caseTitle,
            zentao_bug_title=payload.draft.bugTitle,
            source_type=(payload.draft.sourceType or "").strip().lower() or None,
            linked_case_id=(payload.draft.linkedCaseId or "").strip() or None,
            linked_case_label=(payload.draft.linkedCaseLabel or "").strip() or None,
            linked_case_href=(payload.draft.linkedCaseHref or "").strip() or None,
            display_bucket="requirement" if payload.draft.requirementId else "overall",
            status=initial_status,
            failure_reason=failure_reason,
        )
        audit(
            self.db,
            action="zentao_sync.receive",
            target_type="browser_sync_event",
            target_id=str(event.id),
            detail=f"entity={event.entity_type}",
        )

        try:
            self._auto_map(event)
        except Exception:
            logger.exception("禅道同步自动映射失败 | eventId=%s", event.id)
            event.status = "failed"
            event.failure_reason = "自动映射异常，请人工处理"
            self.repo.save(event)

        logger.info(
            "收到禅道浏览器同步事件 | eventId=%s entity=%s status=%s zentaoBugId=%s zentaoCaseId=%s",
            event.id,
            event.entity_type,
            event.status,
            event.zentao_bug_id,
            event.zentao_case_id,
        )
        self._emit_zentao_row_event("zentao_sync_created", event)
        return {
            "ok": True,
            "event_id": event.id,
            "status": event.status,
            "message": "ok",
            "duplicate_of": duplicate_event.id if duplicate_event else None,
        }

    # -------- list / detail --------
    def list_events(
        self,
        *,
        entity_type: str | None = None,
        status: str | None = None,
        display_bucket: str | None = None,
        source_type: str | None = None,
        keyword: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        only_unapplied: bool = False,
        software_id: int | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> dict[str, Any]:
        date_from_dt = self._parse_date(date_from, end_of_day=False)
        date_to_dt = self._parse_date(date_to, end_of_day=True)
        product_name_filter: str | None = None
        if software_id:
            sw = self.db.query(SoftwareProduct).filter(SoftwareProduct.id == software_id).first()
            if sw and sw.name:
                product_name_filter = sw.name
        result = self.repo.list_events(
            entity_type=entity_type,
            status=status,
            display_bucket=display_bucket,
            source_type=source_type,
            keyword=keyword,
            date_from=date_from_dt,
            date_to=date_to_dt,
            only_unapplied=only_unapplied,
            product_name_filter=product_name_filter,
            page=page,
            page_size=page_size,
        )
        return {
            "items": [self._serialize_event_summary(row) for row in result["items"]],
            "total": result["total"],
            "page": result["page"],
            "page_size": result["page_size"],
        }

    def get_event_detail(self, event_id: int) -> dict[str, Any]:
        row = self.repo.get_event(event_id)
        if not row:
            raise HTTPException(status_code=404, detail="同步事件不存在")
        detail = self._serialize_event_detail(row)
        sw_ids = self._guess_software_ids(row.zentao_product_name, row.zentao_project_name)
        detail["recommended_software_id"] = sw_ids[0] if sw_ids else None
        return detail

    def get_software_products(self) -> list[dict]:
        rows = self.db.query(SoftwareProduct).order_by(SoftwareProduct.id).all()
        return [{"id": r.id, "name": r.name} for r in rows]

    # -------- mapping --------
    def map_event(
        self,
        event_id: int,
        *,
        requirement_id: int | None,
        minor_version_id: int | None,
        source_type: str | None,
        source_ref: str | None,
        note: str | None,
        actor_id: int | None,
        display_bucket: str | None = None,
        linked_case_id: str | None = None,
    ) -> dict[str, Any]:
        row = self.repo.get_event(event_id)
        if not row:
            raise HTTPException(status_code=404, detail="同步事件不存在")

        if requirement_id:
            req = self.db.query(Requirement).filter(Requirement.id == requirement_id).first()
            if not req:
                raise HTTPException(status_code=400, detail="requirement_id 不存在")
            row.mapped_requirement_id = req.id
            row.mapped_major_version_id = req.major_version_id

        if minor_version_id:
            mv = (
                self.db.query(Version)
                .filter(Version.id == minor_version_id, Version.version_type == VersionType.MINOR)
                .first()
            )
            if not mv:
                raise HTTPException(status_code=400, detail="minor_version_id 不存在或不是小版本")
            row.mapped_minor_version_id = mv.id
            if row.mapped_major_version_id and mv.parent_id != row.mapped_major_version_id:
                raise HTTPException(status_code=400, detail="小版本与已映射需求的大版本不匹配")
            if not row.mapped_major_version_id and mv.parent_id:
                row.mapped_major_version_id = mv.parent_id

        if source_type:
            source_type = source_type.strip().lower()
            if source_type not in {e.value for e in BugSourceType}:
                raise HTTPException(status_code=400, detail="source_type 非法")
            row.mapped_source_type = source_type

        if source_ref is not None:
            row.mapped_source_ref = source_ref.strip() or None
        if linked_case_id is not None:
            row.linked_case_id = linked_case_id.strip() or None
            row.mapped_test_case_id = self._find_test_case_id(row.linked_case_id)
        if display_bucket is not None:
            bucket = display_bucket.strip().lower()
            if bucket not in {"requirement", "overall"}:
                raise HTTPException(status_code=400, detail="display_bucket 仅支持 requirement/overall")
            row.display_bucket = bucket

        if note:
            row.push_message = self._append_message(row.push_message, f"note={note.strip()}")

        self._mark_mapped_status(row)
        self.repo.save(row)
        self._emit_zentao_row_event("zentao_sync_updated", row)
        audit(
            self.db,
            action="zentao_sync.map",
            target_type="browser_sync_event",
            actor_id=actor_id,
            target_id=str(row.id),
            detail=f"status={row.status}",
        )
        return {"ok": True, "event_id": row.id, "status": row.status, "message": "映射保存成功"}

    # -------- apply --------
    def apply_event(self, event_id: int, actor_id: int | None) -> dict[str, Any]:
        row = self.repo.get_event(event_id)
        if not row:
            raise HTTPException(status_code=404, detail="同步事件不存在")
        if row.status in {"ignored", "failed"}:
            raise HTTPException(status_code=400, detail=f"当前状态 {row.status} 不允许应用")
        if row.status == "applied":
            return {"ok": True, "event_id": row.id, "status": row.status, "message": "已应用，无需重复处理"}

        try:
            if row.entity_type == "testcase":
                self._apply_testcase(row, actor_id)
            elif row.entity_type == "bug":
                self._apply_bug(row, actor_id)
            else:
                raise HTTPException(status_code=400, detail=f"不支持的 entity_type: {row.entity_type}")
            row.status = "applied"
            row.failure_reason = None
            self.repo.save(row)
            audit(
                self.db,
                action="zentao_sync.apply",
                target_type="browser_sync_event",
                actor_id=actor_id,
                target_id=str(row.id),
                detail=f"entity={row.entity_type}",
            )
            self._emit_zentao_row_event("zentao_sync_updated", row)
            return {"ok": True, "event_id": row.id, "status": row.status, "message": "应用成功"}
        except HTTPException as exc:
            row.status = "failed"
            row.failure_reason = str(exc.detail)
            self.repo.save(row)
            self._emit_zentao_row_event("zentao_sync_auto_apply_failed", row)
            logger.warning("同步事件应用失败 | eventId=%s detail=%s", row.id, exc.detail)
            raise
        except Exception as exc:
            row.status = "failed"
            row.failure_reason = str(exc)
            self.repo.save(row)
            self._emit_zentao_row_event("zentao_sync_auto_apply_failed", row)
            logger.exception("同步事件应用异常 | eventId=%s", row.id)
            raise HTTPException(status_code=500, detail="应用同步事件失败") from exc

    def apply_batch(self, *, actor_id: int | None, limit: int = 100) -> dict[str, Any]:
        events = (
            self.db.query(BrowserSyncEvent)
            .filter(BrowserSyncEvent.status == "ready_to_apply")
            .order_by(BrowserSyncEvent.created_at.asc(), BrowserSyncEvent.id.asc())
            .limit(limit)
            .all()
        )
        total = len(events)
        success = 0
        failed = 0
        failed_items: list[dict[str, Any]] = []
        for row in events:
            try:
                self.apply_event(row.id, actor_id=actor_id)
                success += 1
            except HTTPException as exc:
                failed += 1
                failed_items.append({"event_id": row.id, "reason": str(exc.detail)})
        return {"ok": True, "total": total, "success": success, "failed": failed, "failed_items": failed_items}

    def delete_event(self, event_id: int, *, actor_id: int | None) -> dict[str, Any]:
        row = self.repo.get_event(event_id)
        if not row:
            raise HTTPException(status_code=404, detail="同步事件不存在或已被删除")

        entity_type = row.entity_type
        status = row.status
        self.db.delete(row)
        self.db.commit()
        sse_publish(
            "zentao_sync_deleted",
            {"id": event_id, "entity_type": entity_type, "status": status},
            channels=["global"],
        )
        audit(
            self.db,
            action="zentao_sync.delete",
            target_type="browser_sync_event",
            actor_id=actor_id,
            target_id=str(event_id),
            detail=f"entity={entity_type},status={status}",
        )
        logger.info("删除禅道同步事件成功 | eventId=%s entity=%s status=%s", event_id, entity_type, status)
        return {"ok": True, "event_id": event_id, "message": "删除成功"}

    # -------- legacy compatibility --------
    def legacy_sync(self, payload: ZentaoBrowserSyncPayload) -> dict[str, Any]:
        result = self.receive_event(payload)
        mode = "insert"
        if result.get("status") == "duplicate":
            mode = "duplicate"
        elif result.get("status") in {"pending_mapping", "ready_to_apply", "received"}:
            mode = "update"
        return {
            "success": bool(result.get("ok")),
            "message": result.get("message", "ok"),
            "serverId": result.get("event_id"),
            "entityType": payload.entityType,
            "mode": mode,
        }

    # -------- internal helpers --------
    def _auto_map(self, row: BrowserSyncEvent) -> None:
        if row.status == "duplicate":
            self.repo.save(row)
            return
        if row.entity_type == "testcase":
            self._auto_map_testcase(row)
        elif row.entity_type == "bug":
            self._auto_map_bug(row)
        else:
            row.status = "failed"
            row.failure_reason = f"未知 entity_type: {row.entity_type}"
        self.repo.save(row)

    def _auto_map_testcase(self, row: BrowserSyncEvent) -> None:
        requirement_candidates = self._recommend_requirements(row)
        if len(requirement_candidates) == 1:
            req = requirement_candidates[0]
            row.mapped_requirement_id = req.id
            row.mapped_major_version_id = req.major_version_id
            row.status = "ready_to_apply"
            row.failure_reason = None
            if self._auto_apply_testcase_enabled():
                self._apply_testcase(row, actor_id=None)
                row.status = "applied"
        elif len(requirement_candidates) > 1:
            row.status = "pending_mapping"
            row.failure_reason = "自动匹配到多个需求，请人工确认"
        else:
            row.status = "pending_mapping"
            row.failure_reason = "未匹配到需求，请人工映射"

    def _auto_map_bug(self, row: BrowserSyncEvent) -> None:
        payload = self._event_payload(row)
        draft = payload.get("draft", {})
        row.linked_case_id = self._extract_linked_case_id(draft, row) or row.linked_case_id
        decision = self._build_bug_route_decision(row, draft=draft)
        row.mapped_source_type = decision.mapped_source_type
        row.display_bucket = decision.display_bucket
        row.mapped_requirement_id = decision.requirement_id
        row.mapped_test_case_id = decision.test_case_id
        row.mapped_major_version_id = decision.major_version_id
        row.mapped_minor_version_id = decision.minor_version_id

        if decision.route_target == "pending_decision":
            row.status = "pending_mapping"
            row.failure_reason = decision.reason or "待人工决策"
        else:
            self._mark_mapped_status(row)
            if decision.reason and row.status != "ready_to_apply":
                row.failure_reason = decision.reason

        self._maybe_auto_apply_bug(row, draft=draft, decision=decision)

    def _recommend_requirements(self, row: BrowserSyncEvent) -> list[Requirement]:
        payload = self._event_payload(row)
        draft = payload.get("draft", {})
        req_number = extract_requirement_numeric_id(str(draft.get("requirementId") or row.zentao_req_id or ""))
        req_id = f"r#{req_number}" if req_number else None
        req_name = self._clean_text(draft.get("requirementName"))
        product_name = self._clean_text(draft.get("productName"))
        project_name = self._clean_text(draft.get("projectName"))
        execution_raw = self._clean_text(draft.get("executionName") or draft.get("executionId") or row.zentao_execution_name)
        affected_version = self._clean_text(draft.get("affectedVersion") or row.zentao_affected_version)

        major = self._match_major_by_execution(execution_raw, product_name, project_name) or self._match_major_by_affected_version(
            affected_version=affected_version,
            product_name=product_name,
            project_name=project_name,
        )

        # Priority 1: requirementId exact lookup (normalize to r#<digits> first).
        if req_id:
            req_query = self.db.query(Requirement).filter(Requirement.zentao_req_id == req_id)
            req_rows = req_query.order_by(Requirement.id.desc()).all()
            if req_rows:
                if major:
                    major_rows = [r for r in req_rows if r.major_version_id == major.id]
                    if len(major_rows) == 1:
                        return major_rows
                    if len(major_rows) > 1 and req_name:
                        narrowed = self._pick_requirements_by_title(req_name, major_rows)
                        if narrowed:
                            return narrowed
                    if major_rows:
                        return major_rows
                if len(req_rows) == 1:
                    return req_rows
                if req_name:
                    narrowed = self._pick_requirements_by_title(req_name, req_rows)
                    if narrowed:
                        return narrowed
                return req_rows

        # Priority 2: requirementName normalized fuzzy lookup.
        if req_name:
            base_query = self.db.query(Requirement)
            if major:
                base_query = base_query.filter(Requirement.major_version_id == major.id)
            title_rows = base_query.order_by(Requirement.id.desc()).all()
            title_matches = self._pick_requirements_by_title(req_name, title_rows)
            if title_matches:
                return title_matches

            # Priority 3 fallback: id extracted but id exact miss => same-major fuzzy title pick.
            if req_id and not major:
                all_rows = self.db.query(Requirement).order_by(Requirement.id.desc()).all()
                title_matches = self._pick_requirements_by_title(req_name, all_rows)
                if title_matches:
                    return title_matches
        return []

    def _apply_testcase(self, row: BrowserSyncEvent, actor_id: int | None) -> None:
        if not row.mapped_requirement_id:
            raise HTTPException(status_code=400, detail="用例事件尚未映射 requirement_id")

        payload = self._event_payload(row)
        case_id_raw = payload.get("result", {}).get("zentaoCaseId") or row.zentao_case_id
        if not case_id_raw:
            raise HTTPException(status_code=400, detail="缺少 zentaoCaseId")

        case_no = self._normalize_prefixed_id("u#", case_id_raw)
        existing = (
            self.db.query(TestCase)
            .filter(TestCase.requirement_id == row.mapped_requirement_id, TestCase.zentao_case_id == case_no)
            .first()
        )
        if existing:
            row.applied_case_id = existing.id
            return

        # Browser sync testcase should not be blocked by requirement assignment/case-completed guard.
        actor = self._resolve_actor_for_apply(payload, actor_id)
        new_case = TestCase(requirement_id=row.mapped_requirement_id, zentao_case_id=case_no, creator_id=actor.id)
        self.db.add(new_case)
        self.db.commit()
        self.db.refresh(new_case)

        draft = payload.get("draft", {})
        new_case.zentao_case_url = payload.get("result", {}).get("zentaoCaseUrl")
        new_case.zentao_client_record_id = row.client_record_id
        new_case.zentao_source = row.source
        new_case.zentao_captured_at = row.captured_at
        new_case.zentao_top_href = row.top_href
        new_case.zentao_product_id = draft.get("productId")
        new_case.zentao_product_name = draft.get("productName")
        new_case.zentao_case_title = draft.get("caseTitle")
        new_case.zentao_requirement_id = draft.get("requirementId")
        new_case.zentao_requirement_name = draft.get("requirementName")
        new_case.zentao_creator_name = draft.get("creatorName")
        new_case.zentao_sync_status = "synced"
        new_case.zentao_sync_message = "通过禅道同步事件应用"
        new_case.zentao_raw_payload = row.raw_payload_json
        self.db.commit()

        row.applied_case_id = new_case.id

    def _apply_bug(self, row: BrowserSyncEvent, actor_id: int | None) -> None:
        if row.display_bucket != "overall" and not row.mapped_requirement_id:
            raise HTTPException(status_code=400, detail="Bug 事件尚未映射 requirement_id")
        if row.display_bucket == "overall" and not row.mapped_major_version_id:
            raise HTTPException(status_code=400, detail="overall Bug 事件尚未映射 major_version_id")

        payload = self._event_payload(row)
        draft = payload.get("draft", {})
        decision = self._build_bug_route_decision(row, draft=draft)
        if decision.route_target == "pending_decision":
            raise HTTPException(status_code=400, detail=decision.reason or "当前事件仍需人工决策，暂不可应用")

        bug_id_raw = payload.get("result", {}).get("zentaoBugId") or row.zentao_bug_id
        if not bug_id_raw:
            raise HTTPException(status_code=400, detail="缺少 zentaoBugId")

        bug_no = self._normalize_prefixed_id("b#", bug_id_raw)
        existing = self.db.query(BugTracking).filter(BugTracking.bug_id == bug_no).first()
        if existing:
            row.applied_bug_tracking_id = existing.id
            return

        actor = self._resolve_actor_for_apply(payload, actor_id)
        source_type = self._parse_bug_source_type(decision.mapped_source_type or row.mapped_source_type or draft.get("sourceType"))
        if decision.route_target.startswith("retest_"):
            source_type = BugSourceType.RETEST

        use_overall_bucket = decision.display_bucket == "overall" or not row.mapped_minor_version_id
        if use_overall_bucket:
            bug_row = BugTracking(
                major_version_id=decision.major_version_id or row.mapped_major_version_id,  # type: ignore[arg-type]
                requirement_id=None,
                source_type=source_type,
                source_ref=row.mapped_source_ref or draft.get("sourceRef"),
                bug_id=bug_no,
                found_minor_version_id=row.mapped_minor_version_id,
                created_by_id=actor.id,
            )
            self.db.add(bug_row)
            self.db.commit()
            self.db.refresh(bug_row)
            sse_publish(
                "overall_bug_created",
                {
                    "id": bug_row.id,
                    "bug_id": bug_row.bug_id,
                    "source_type": bug_row.source_type.value if hasattr(bug_row.source_type, "value") else str(bug_row.source_type),
                    "source_ref": bug_row.source_ref,
                    "requirement_id": bug_row.requirement_id,
                    "major_version_id": bug_row.major_version_id,
                    "minor_version_id": bug_row.found_minor_version_id,
                },
                channels=["global"],
            )
            audit(
                self.db,
                action="bug.create",
                target_type="bug",
                actor_id=actor.id,
                target_id=str(bug_row.id),
                detail=f"{bug_row.bug_id}(overall)",
            )
        else:
            created = self.bug_service.create_execution_bug(
                bug_id=bug_no,
                minor_version_id=row.mapped_minor_version_id,
                requirement_id=decision.requirement_id or row.mapped_requirement_id,  # type: ignore[arg-type]
                source_type=source_type,
                source_ref=row.mapped_source_ref or draft.get("sourceRef") or (str(row.mapped_test_case_id) if row.mapped_test_case_id and source_type == BugSourceType.CASE else None),
                actor=actor,
            )

            bug_row = self.db.query(BugTracking).filter(BugTracking.id == created["id"]).first()
            if not bug_row:
                raise HTTPException(status_code=500, detail="Bug 创建后读取失败")

        bug_row.zentao_bug_id = row.zentao_bug_id
        bug_row.zentao_bug_url = payload.get("result", {}).get("zentaoBugUrl")
        bug_row.zentao_client_record_id = row.client_record_id
        bug_row.zentao_source = row.source
        bug_row.zentao_captured_at = row.captured_at
        bug_row.zentao_top_href = row.top_href
        bug_row.zentao_product_id = draft.get("productId")
        bug_row.zentao_product_name = draft.get("productName")
        bug_row.zentao_project_id = draft.get("projectId")
        bug_row.zentao_project_name = draft.get("projectName")
        bug_row.zentao_opened_build_ids = json.dumps(draft.get("openedBuildIds") or [], ensure_ascii=False)
        bug_row.zentao_affected_version = draft.get("affectedVersion")
        bug_row.zentao_bug_title = draft.get("bugTitle")
        bug_row.zentao_source_type = (draft.get("sourceType") or row.source_type or "").strip().lower() or None
        bug_row.zentao_linked_case_id = draft.get("linkedCaseId") or row.linked_case_id
        bug_row.zentao_linked_case_label = draft.get("linkedCaseLabel") or row.linked_case_label
        bug_row.zentao_linked_case_href = draft.get("linkedCaseHref") or row.linked_case_href
        bug_row.zentao_display_bucket = row.display_bucket
        bug_row.zentao_execution_id = draft.get("executionId")
        bug_row.zentao_execution_name = draft.get("executionName")
        bug_row.zentao_requirement_id = draft.get("requirementId")
        bug_row.zentao_requirement_name = draft.get("requirementName")
        bug_row.zentao_creator_name = draft.get("creatorName")
        bug_row.zentao_sync_status = "synced"
        bug_row.zentao_sync_message = "通过禅道同步事件应用"
        bug_row.zentao_raw_payload = row.raw_payload_json
        self.db.commit()

        row.applied_bug_tracking_id = bug_row.id

    def _maybe_auto_apply_bug(self, row: BrowserSyncEvent, *, draft: dict[str, Any], decision: BugRouteDecision) -> None:
        if row.entity_type != "bug":
            return
        if not self._auto_apply_bug_enabled():
            return
        if row.status == "applied" or row.applied_bug_tracking_id:
            return
        if not decision.auto_apply_allowed:
            return
        if not row.mapped_major_version_id:
            return
        bug_title = self._clean_text(row.zentao_bug_title or draft.get("bugTitle"))
        if not bug_title:
            return
        if decision.route_target == "pending_decision":
            return
        if not row.mapped_source_type:
            row.mapped_source_type = decision.mapped_source_type or BugSourceType.MANUAL.value
        try:
            self._apply_bug(row, actor_id=None)
            row.status = "applied"
            row.failure_reason = None
            audit(
                self.db,
                action="zentao_sync.auto_apply_bug",
                target_type="browser_sync_event",
                target_id=str(row.id),
                detail=f"major={row.mapped_major_version_id}",
            )
        except Exception as exc:
            logger.exception("禅道 Bug 自动应用失败 | eventId=%s", row.id)
            if row.status == "ready_to_apply":
                row.failure_reason = f"自动应用失败：{exc}"
            elif row.failure_reason:
                row.failure_reason = f"{row.failure_reason}；自动应用失败：{exc}"
            else:
                row.failure_reason = f"自动应用失败：{exc}"

    def _resolve_actor_for_apply(self, payload: dict[str, Any], actor_id: int | None) -> User:
        if actor_id:
            actor = self.db.query(User).filter(User.id == actor_id).first()
            if actor:
                return actor

        creator_name = self._clean_text(payload.get("draft", {}).get("creatorName"))
        if creator_name:
            actor = (
                self.db.query(User)
                .filter(or_(User.display_name == creator_name, User.username == creator_name))
                .order_by(User.id.desc())
                .first()
            )
            if actor:
                return actor

        admin = self.db.query(User).filter(User.role == UserRole.ADMIN).order_by(User.id.asc()).first()
        if admin:
            return admin

        any_user = self.db.query(User).order_by(User.id.asc()).first()
        if any_user:
            return any_user

        raise HTTPException(status_code=400, detail="系统中不存在可用于应用事件的用户")

    def _match_major_by_execution(self, execution_id: str | None, product_name: str | None, project_name: str | None) -> Version | None:
        raw = self._clean_text(execution_id)
        if not raw:
            return None

        normalized_exec = normalize_execution_name(raw)
        mapped_major = convert_s_token_to_major_version(raw)
        embedded_major = extract_major_version_token(raw)
        software_ids = self._guess_software_ids(product_name, project_name)

        def _apply_soft_filter(query):
            if software_ids:
                return query.filter(or_(Version.software_id.in_(software_ids), Version.software_id.is_(None)))
            return query

        exact_candidates: list[Version] = []
        exact_keys = [k for k in [normalized_exec, mapped_major, embedded_major] if k]
        for key in exact_keys:
            rows = _apply_soft_filter(
                self.db.query(Version).filter(Version.version_type == VersionType.MAJOR, Version.version_no == key)
            ).all()
            if rows:
                exact_candidates.extend(rows)

        if exact_candidates:
            return sorted(exact_candidates, key=self._version_order_key, reverse=True)[0]

        like_keys = [k for k in [mapped_major, embedded_major] if k]
        for key in like_keys:
            rows = _apply_soft_filter(
                self.db.query(Version).filter(Version.version_type == VersionType.MAJOR, Version.version_no.like(f"{key}%"))
            ).all()
            if rows:
                return sorted(rows, key=self._version_order_key, reverse=True)[0]

        # Trim-fallback：处理 3 段大版本（如 V2.0.0）被解析为 V2.0.0.0 的情况
        for key in like_keys:
            if key.count(".") >= 3:
                trimmed = key.rsplit(".", 1)[0]  # V2.0.0.0 → V2.0.0
                rows = _apply_soft_filter(
                    self.db.query(Version).filter(Version.version_type == VersionType.MAJOR, Version.version_no == trimmed)
                ).all()
                if rows:
                    return sorted(rows, key=self._version_order_key, reverse=True)[0]
        return None

    def _match_major_by_affected_version(
        self,
        *,
        affected_version: str | None,
        product_name: str | None,
        project_name: str | None,
    ) -> Version | None:
        parsed = parse_affected_version(affected_version)
        if not parsed.version_prefix:
            return None
        guessed = f"V{parsed.version_prefix}"
        software_ids = self._guess_software_ids(product_name, project_name)
        query = self.db.query(Version).filter(Version.version_type == VersionType.MAJOR)
        if software_ids:
            query = query.filter(or_(Version.software_id.in_(software_ids), Version.software_id.is_(None)))

        exact = query.filter(Version.version_no == guessed).order_by(Version.id.desc()).first()
        if exact:
            return exact
        like_match = query.filter(Version.version_no.like(f"{guessed}%")).order_by(Version.id.desc()).first()
        if like_match:
            return like_match

        # Fallback：通过 build_no 找到小版本，再从 parent_id 反推大版本
        # 解决 VERSION_PREFIX_RE 多捕获一段（如 2.0.0.260401）导致猜测值偏长的问题
        if parsed.build_no:
            minor = (
                self.db.query(Version)
                .filter(Version.version_type == VersionType.MINOR, Version.version_no.like(f"%({parsed.build_no})%"))
                .order_by(Version.id.desc())
                .first()
            )
            if minor and minor.parent_id:
                parent = (
                    self.db.query(Version)
                    .filter(Version.id == minor.parent_id, Version.version_type == VersionType.MAJOR)
                    .first()
                )
                if parent and (not software_ids or parent.software_id in software_ids):
                    return parent
        return None

    def _guess_software_ids(self, product_name: str | None, project_name: str | None) -> list[int]:
        tokens = [token for token in [product_name, project_name] if token]
        if not tokens:
            return []

        rows = self.db.query(SoftwareProduct).all()
        matched: list[int] = []
        for row in rows:
            for token in tokens:
                if row.name and row.name.lower() in token.lower():
                    matched.append(row.id)
                    break
        return matched

    @staticmethod
    def _version_order_key(row: Version) -> tuple[int, int]:
        exact_rank = 1 if row.software_id is not None else 0
        return (exact_rank, row.id)

    def _pick_requirements_by_title(self, req_name: str, candidates: list[Requirement]) -> list[Requirement]:
        """
        requirementName matching strategy:
        1) normalize titles to absorb SR/id prefixes and punctuation variants.
        2) only auto-pick when there is exactly one best-scored candidate.
        """
        normalized = normalize_requirement_title(req_name)
        if not normalized or not candidates:
            return []
        by_id = {r.id: r for r in candidates}
        hits = pick_best_title_match(
            target=normalized,
            candidates=[(r.id, r.title) for r in candidates],
        )
        return [by_id[i] for i in hits if i in by_id]

    def _match_minor_by_affected_version(self, major_id: int, affected_version: str | None) -> Version | None:
        parsed = parse_affected_version(affected_version)
        query = self.db.query(Version).filter(
            Version.version_type == VersionType.MINOR,
            Version.parent_id == major_id,
        )
        if parsed.build_no:
            token = f"({parsed.build_no})"
            by_build = query.filter(Version.version_no.like(f"%{token}%")).order_by(Version.id.desc()).first()
            if by_build:
                return by_build
        if parsed.version_prefix:
            by_prefix = query.filter(Version.version_no.like(f"{parsed.version_prefix}%")).order_by(Version.id.desc()).first()
            if by_prefix:
                return by_prefix
        return None

    def _mark_mapped_status(self, row: BrowserSyncEvent) -> None:
        if row.entity_type == "testcase":
            if row.mapped_requirement_id:
                row.status = "ready_to_apply"
                row.failure_reason = None
            else:
                row.status = "pending_mapping"
                row.failure_reason = "需要 requirement_id"
            return

        if row.entity_type == "bug":
            if row.display_bucket not in {"requirement", "overall"}:
                row.display_bucket = "requirement" if row.mapped_requirement_id else "overall"
            if row.display_bucket == "overall":
                if row.mapped_major_version_id and row.mapped_minor_version_id:
                    row.status = "ready_to_apply"
                    row.failure_reason = None
                else:
                    row.status = "pending_mapping"
                    row.failure_reason = "需要 major/minor 版本映射"
                return
            if row.mapped_requirement_id and row.mapped_minor_version_id:
                row.status = "ready_to_apply"
                row.failure_reason = None
            else:
                row.status = "pending_mapping"
                if not row.mapped_requirement_id:
                    row.failure_reason = "需要 requirement_id"
                elif not row.mapped_minor_version_id:
                    row.failure_reason = "需要 minor_version_id"

    @staticmethod
    def _parse_date(raw: str | None, *, end_of_day: bool) -> datetime | None:
        if not raw:
            return None
        try:
            if end_of_day:
                return datetime.fromisoformat(raw + "T23:59:59")
            return datetime.fromisoformat(raw + "T00:00:00")
        except Exception:
            raise HTTPException(status_code=400, detail="日期参数格式错误，应为 YYYY-MM-DD")

    @staticmethod
    def _clean_text(value: Any) -> str | None:
        text = str(value or "").strip()
        return text or None

    @staticmethod
    def _required_text(value: Any, message: str) -> str:
        text = str(value or "").strip()
        if not text:
            raise HTTPException(status_code=400, detail=message)
        return text

    @staticmethod
    def _normalize_prefixed_id(prefix: str, raw_value: Any) -> str:
        text = str(raw_value or "").strip()
        lower_prefix = prefix.lower()
        if text.lower().startswith(lower_prefix):
            suffix = text[len(prefix) :].strip()
            return f"{prefix}{suffix}"
        return f"{prefix}{text}"

    @staticmethod
    def _normalize_requirement_id(raw_value: Any) -> str | None:
        number = extract_requirement_numeric_id(str(raw_value or ""))
        if not number:
            return None
        return f"r#{number}"

    def _find_test_case_id(self, linked_case_id: str | None) -> int | None:
        if not linked_case_id:
            return None
        case_no = self._normalize_prefixed_id("u#", linked_case_id)
        row = (
            self.db.query(TestCase)
            .filter(TestCase.zentao_case_id == case_no)
            .order_by(TestCase.id.desc())
            .first()
        )
        return row.id if row else None

    def _build_bug_route_decision(self, row: BrowserSyncEvent, *, draft: dict[str, Any]) -> BugRouteDecision:
        route_source = self._detect_bug_route_source(row, draft)
        creator = self._resolve_creator_user(draft)

        major_id = row.mapped_major_version_id
        if not major_id:
            execution = self._clean_text(draft.get("executionName") or draft.get("executionId") or row.zentao_execution_name)
            major = self._match_major_by_execution(
                execution_id=execution,
                product_name=self._clean_text(draft.get("productName") or row.zentao_product_name),
                project_name=self._clean_text(draft.get("projectName") or row.zentao_project_name),
            )
            if not major:
                major = self._match_major_by_affected_version(
                    affected_version=self._clean_text(draft.get("affectedVersion") or row.zentao_affected_version),
                    product_name=self._clean_text(draft.get("productName") or row.zentao_product_name),
                    project_name=self._clean_text(draft.get("projectName") or row.zentao_project_name),
                )
            major_id = major.id if major else None

        minor_id = row.mapped_minor_version_id
        if major_id and not minor_id:
            minor = self._match_minor_by_affected_version(
                major_id=major_id,
                affected_version=self._clean_text(draft.get("affectedVersion") or row.zentao_affected_version),
            )
            if minor:
                minor_id = minor.id

        mapped_source_type = BugSourceType.MANUAL.value
        display_bucket = "overall"
        requirement_id: int | None = None
        test_case_id: int | None = None
        route_target = "overall"
        reason: str | None = None
        auto_apply_allowed = False

        if route_source == "test":
            mapped_source_type = BugSourceType.MANUAL.value
            display_bucket = "overall"
            route_target = "overall"
            if not major_id:
                reason = "测试来源缺少可识别大版本，请补充 execution/affectedVersion"
            elif not minor_id:
                reason = "测试来源已识别大版本，缺少小版本；可人工确认后再应用"
            auto_apply_allowed = bool(major_id)
            return BugRouteDecision(
                route_source=route_source,
                route_target=route_target,
                reason=reason,
                mapped_source_type=mapped_source_type,
                display_bucket=display_bucket,
                requirement_id=None,
                test_case_id=None,
                major_version_id=major_id,
                minor_version_id=minor_id,
                creator_user_id=creator.id if creator else None,
                auto_apply_allowed=auto_apply_allowed,
            )

        requirement_candidates = self._recommend_requirements(row)
        linked_case_id = self._extract_linked_case_id(draft, row)
        test_case = self._find_test_case_by_zentao_id(linked_case_id, major_id=major_id) if route_source == "case" else None
        if test_case:
            test_case_id = test_case.id
            requirement_id = test_case.requirement_id
            req_row = self.db.query(Requirement).filter(Requirement.id == requirement_id).first()
        else:
            req_row = requirement_candidates[0] if len(requirement_candidates) == 1 else None
            if req_row:
                requirement_id = req_row.id
                if not major_id:
                    major_id = req_row.major_version_id

        if route_source == "case":
            mapped_source_type = BugSourceType.CASE.value
            if len(requirement_candidates) > 1 and not req_row and not test_case:
                return BugRouteDecision(
                    route_source=route_source,
                    route_target="pending_decision",
                    reason="用例来源匹配到多个需求，需人工确认",
                    mapped_source_type=mapped_source_type,
                    display_bucket="requirement",
                    requirement_id=None,
                    test_case_id=None,
                    major_version_id=major_id,
                    minor_version_id=minor_id,
                    creator_user_id=creator.id if creator else None,
                    auto_apply_allowed=False,
                )
            if not req_row:
                return BugRouteDecision(
                    route_source=route_source,
                    route_target="pending_mapping",
                    reason="用例来源缺少可匹配需求，请人工补充 requirement",
                    mapped_source_type=mapped_source_type,
                    display_bucket="requirement",
                    requirement_id=None,
                    test_case_id=None,
                    major_version_id=major_id,
                    minor_version_id=minor_id,
                    creator_user_id=creator.id if creator else None,
                    auto_apply_allowed=False,
                )
            display_bucket = "requirement"
            if req_row.test_completed:
                if self._is_retest_context(row, draft, req_row, creator):
                    route_target = "retest_requirement_case_bug" if test_case_id else "retest_requirement_free_bug"
                    mapped_source_type = BugSourceType.RETEST.value
                    reason = None if test_case_id else "需求已封版且处于复测阶段，但未找到 testcase，建议先创建 testcase 再挂载"
                    return BugRouteDecision(
                        route_source=route_source,
                        route_target=route_target if test_case_id else "pending_decision",
                        reason=reason if reason else None,
                        mapped_source_type=mapped_source_type,
                        display_bucket=display_bucket,
                        requirement_id=requirement_id,
                        test_case_id=test_case_id,
                        major_version_id=major_id,
                        minor_version_id=minor_id,
                        creator_user_id=creator.id if creator else None,
                        auto_apply_allowed=bool(test_case_id and major_id),
                    )
                return BugRouteDecision(
                    route_source=route_source,
                    route_target="pending_decision",
                    reason="需求已测试完成/封版，且创建者非复测责任人；请确认转复测或转整体测试",
                    mapped_source_type=mapped_source_type,
                    display_bucket=display_bucket,
                    requirement_id=requirement_id,
                    test_case_id=test_case_id,
                    major_version_id=major_id,
                    minor_version_id=minor_id,
                    creator_user_id=creator.id if creator else None,
                    auto_apply_allowed=False,
                )
            if not test_case_id:
                return BugRouteDecision(
                    route_source=route_source,
                    route_target="pending_decision",
                    reason="用例来源未找到对应 testcase：可选择创建 testcase 后挂载，或暂缓处理",
                    mapped_source_type=mapped_source_type,
                    display_bucket=display_bucket,
                    requirement_id=requirement_id,
                    test_case_id=None,
                    major_version_id=major_id,
                    minor_version_id=minor_id,
                    creator_user_id=creator.id if creator else None,
                    auto_apply_allowed=False,
                )
            return BugRouteDecision(
                route_source=route_source,
                route_target="requirement_case_bug",
                reason=None,
                mapped_source_type=mapped_source_type,
                display_bucket=display_bucket,
                requirement_id=requirement_id,
                test_case_id=test_case_id,
                major_version_id=major_id,
                minor_version_id=minor_id,
                creator_user_id=creator.id if creator else None,
                auto_apply_allowed=bool(major_id and minor_id),
            )

        # requirement source
        mapped_source_type = BugSourceType.REQUIREMENT.value
        display_bucket = "requirement"
        if len(requirement_candidates) > 1 and not req_row:
            return BugRouteDecision(
                route_source=route_source,
                route_target="pending_mapping",
                reason="需求来源匹配到多个需求，请人工确认",
                mapped_source_type=mapped_source_type,
                display_bucket=display_bucket,
                requirement_id=None,
                test_case_id=None,
                major_version_id=major_id,
                minor_version_id=minor_id,
                creator_user_id=creator.id if creator else None,
                auto_apply_allowed=False,
            )
        if not req_row:
            return BugRouteDecision(
                route_source=route_source,
                route_target="pending_mapping",
                reason="需求来源未匹配到 requirement，请人工确认",
                mapped_source_type=mapped_source_type,
                display_bucket=display_bucket,
                requirement_id=None,
                test_case_id=None,
                major_version_id=major_id,
                minor_version_id=minor_id,
                creator_user_id=creator.id if creator else None,
                auto_apply_allowed=False,
            )
        if req_row.test_completed:
            if self._is_retest_context(row, draft, req_row, creator):
                return BugRouteDecision(
                    route_source=route_source,
                    route_target="retest_requirement_free_bug",
                    reason=None,
                    mapped_source_type=BugSourceType.RETEST.value,
                    display_bucket=display_bucket,
                    requirement_id=req_row.id,
                    test_case_id=None,
                    major_version_id=major_id,
                    minor_version_id=minor_id,
                    creator_user_id=creator.id if creator else None,
                    auto_apply_allowed=bool(major_id and minor_id),
                )
            return BugRouteDecision(
                route_source=route_source,
                route_target="pending_decision",
                reason="需求已测试完成/封版，请确认转整体测试或复测后再应用",
                mapped_source_type=mapped_source_type,
                display_bucket=display_bucket,
                requirement_id=req_row.id,
                test_case_id=None,
                major_version_id=major_id,
                minor_version_id=minor_id,
                creator_user_id=creator.id if creator else None,
                auto_apply_allowed=False,
            )
        return BugRouteDecision(
            route_source=route_source,
            route_target="requirement_free_bug",
            reason=None,
            mapped_source_type=mapped_source_type,
            display_bucket=display_bucket,
            requirement_id=req_row.id,
            test_case_id=None,
            major_version_id=major_id,
            minor_version_id=minor_id,
            creator_user_id=creator.id if creator else None,
            auto_apply_allowed=bool(major_id and minor_id),
        )

    def _detect_bug_route_source(self, row: BrowserSyncEvent, draft: dict[str, Any]) -> str:
        mapped = str(row.mapped_source_type or "").strip().lower()
        if mapped == BugSourceType.CASE.value:
            return "case"
        if mapped in {BugSourceType.REQUIREMENT.value, BugSourceType.RETEST.value}:
            return "requirement"
        if mapped in {BugSourceType.MANUAL.value, BugSourceType.LEGACY_BUG.value, BugSourceType.FIELD_TEST.value}:
            return "test"
        linked_case = self._extract_linked_case_id(draft, row)
        if linked_case:
            return "case"
        requirement_id = self._clean_text(draft.get("requirementId") or row.zentao_req_id)
        requirement_name = self._clean_text(draft.get("requirementName") or row.zentao_requirement_name)
        if requirement_id or requirement_name:
            return "requirement"
        return "test"

    def _extract_linked_case_id(self, draft: dict[str, Any], row: BrowserSyncEvent) -> str | None:
        for key in ("linkedCaseId", "sourceCaseId", "source_case_id", "caseId"):
            value = self._clean_text(draft.get(key))
            if value:
                return value
        return self._clean_text(row.linked_case_id)

    def _find_test_case_by_zentao_id(self, linked_case_id: str | None, *, major_id: int | None) -> TestCase | None:
        if not linked_case_id:
            return None
        case_no = self._normalize_prefixed_id("u#", linked_case_id)
        query = self.db.query(TestCase).filter(TestCase.zentao_case_id == case_no)
        if major_id:
            query = query.join(Requirement, Requirement.id == TestCase.requirement_id).filter(Requirement.major_version_id == major_id)
        return query.order_by(TestCase.id.desc()).first()

    def _resolve_creator_user(self, draft: dict[str, Any]) -> User | None:
        creator_name = self._clean_text(draft.get("creatorName"))
        if not creator_name:
            return None
        return (
            self.db.query(User)
            .filter(or_(User.display_name == creator_name, User.username == creator_name))
            .order_by(User.id.desc())
            .first()
        )

    def _is_retest_context(self, row: BrowserSyncEvent, draft: dict[str, Any], requirement: Requirement, creator: User | None) -> bool:
        source_type_text = str(row.mapped_source_type or draft.get("sourceType") or row.source_type or "").strip().lower()
        if source_type_text == BugSourceType.RETEST.value:
            return True
        if creator and requirement.retested_by_id and creator.id == requirement.retested_by_id:
            return True
        status_value = requirement.status.value if hasattr(requirement.status, "value") else str(requirement.status)
        return status_value in {RequirementStatus.RETEST_PENDING.value, RequirementStatus.RETEST_DONE.value}

    @staticmethod
    def _append_message(origin: str | None, patch: str) -> str:
        if not origin:
            return patch
        return f"{origin}\n{patch}"

    @staticmethod
    def _parse_bug_source_type(raw_value: Any) -> BugSourceType:
        text = str(raw_value or "").strip().lower()
        if not text:
            return BugSourceType.MANUAL
        for e in BugSourceType:
            if e.value == text:
                return e
        return BugSourceType.MANUAL

    @staticmethod
    def _event_payload(row: BrowserSyncEvent) -> dict[str, Any]:
        try:
            return json.loads(row.raw_payload_json or "{}")
        except Exception:
            return {}

    @staticmethod
    def _serialize_event_summary(row: BrowserSyncEvent) -> dict[str, Any]:
        payload = ZentaoSyncService._event_payload(row)
        draft = payload.get("draft", {})
        route_source = ZentaoSyncService._infer_route_source(row, draft)
        route_target = ZentaoSyncService._infer_route_target(row)
        return {
            "id": row.id,
            "client_record_id": row.client_record_id,
            "entity_type": row.entity_type,
            "status": row.status,
            "creator_name": row.creator_name,
            "operator_name": row.operator_name,
            "zentao_bug_id": row.zentao_bug_id,
            "zentao_case_id": row.zentao_case_id,
            "zentao_req_id": row.zentao_req_id,
            "zentao_product_id": row.zentao_product_id,
            "zentao_product_name": row.zentao_product_name,
            "title": row.zentao_bug_title or row.zentao_case_title or draft.get("bugTitle") or draft.get("caseTitle"),
            "requirement_name": draft.get("requirementName") or row.zentao_req_id,
            "mapped_requirement_id": row.mapped_requirement_id,
            "mapped_major_version_id": row.mapped_major_version_id,
            "mapped_minor_version_id": row.mapped_minor_version_id,
            "mapped_source_type": row.mapped_source_type,
            "mapped_source_ref": row.mapped_source_ref,
            "applied_case_id": row.applied_case_id,
            "applied_bug_tracking_id": row.applied_bug_tracking_id,
            "route_source": route_source,
            "route_target": route_target,
            "failure_reason": row.failure_reason,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        }

    @staticmethod
    def _serialize_event_detail(row: BrowserSyncEvent) -> dict[str, Any]:
        payload = ZentaoSyncService._event_payload(row)
        draft = payload.get("draft", {})
        route_source = ZentaoSyncService._infer_route_source(row, draft)
        route_target = ZentaoSyncService._infer_route_target(row)
        return {
            "id": row.id,
            "client_record_id": row.client_record_id,
            "entity_type": row.entity_type,
            "action": row.action,
            "source": row.source,
            "captured_at": row.captured_at,
            "received_at": row.received_at.isoformat() if row.received_at else None,
            "top_href": row.top_href,
            "page_url": row.page_url,
            "script_version": row.script_version,
            "status": row.status,
            "failure_reason": row.failure_reason,
            "push_message": row.push_message,
            "creator_name": row.creator_name,
            "operator_name": row.operator_name,
            "zentao_bug_id": row.zentao_bug_id,
            "zentao_case_id": row.zentao_case_id,
            "zentao_req_id": row.zentao_req_id,
            "zentao_requirement_name": row.zentao_requirement_name,
            "zentao_bug_title": row.zentao_bug_title,
            "zentao_case_title": row.zentao_case_title,
            "zentao_execution_name": row.zentao_execution_name,
            "zentao_affected_version": row.zentao_affected_version,
            "linked_case_id": row.linked_case_id,
            "linked_case_label": row.linked_case_label,
            "linked_case_href": row.linked_case_href,
            "zentao_product_id": row.zentao_product_id,
            "zentao_product_name": row.zentao_product_name,
            "zentao_project_id": row.zentao_project_id,
            "zentao_project_name": row.zentao_project_name,
            "mapped_requirement_id": row.mapped_requirement_id,
            "mapped_major_version_id": row.mapped_major_version_id,
            "mapped_minor_version_id": row.mapped_minor_version_id,
            "mapped_source_type": row.mapped_source_type,
            "mapped_source_ref": row.mapped_source_ref,
            "mapped_test_case_id": row.mapped_test_case_id,
            "applied_case_id": row.applied_case_id,
            "applied_bug_tracking_id": row.applied_bug_tracking_id,
            "title": row.zentao_bug_title or row.zentao_case_title,
            "draft": draft,
            "result": payload.get("result"),
            "raw_payload": payload,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
            "recommended_requirement_id": row.mapped_requirement_id,
            "recommended_major_version_id": row.mapped_major_version_id,
            "recommended_minor_version_id": row.mapped_minor_version_id,
            "recommended_source_type": row.mapped_source_type or ("manual" if row.entity_type == "bug" else None),
            "recommended_display_bucket": row.display_bucket,
            "recommended_test_case_id": row.mapped_test_case_id,
            "recommended_route_source": route_source,
            "recommended_route_target": route_target,
            "recommended_decision_reason": row.failure_reason,
            "recommended_hint": (
                "需求池：挂到具体需求下；总览池：仅归属版本整体"
                if row.entity_type == "bug"
                else "用例事件建议映射到具体需求"
            ),
        }

    @staticmethod
    def _infer_route_source(row: BrowserSyncEvent, draft: dict[str, Any]) -> str:
        mapped = str(row.mapped_source_type or "").strip().lower()
        if mapped == BugSourceType.CASE.value:
            return "case"
        if mapped in {BugSourceType.REQUIREMENT.value, BugSourceType.RETEST.value}:
            return "requirement"
        linked_case = (
            str(row.linked_case_id or "").strip()
            or str(draft.get("linkedCaseId") or "").strip()
            or str(draft.get("sourceCaseId") or "").strip()
        )
        if linked_case:
            return "case"
        requirement_marker = (
            str(row.zentao_req_id or "").strip()
            or str(row.zentao_requirement_name or "").strip()
            or str(draft.get("requirementId") or "").strip()
            or str(draft.get("requirementName") or "").strip()
        )
        if requirement_marker:
            return "requirement"
        return "test"

    @staticmethod
    def _infer_route_target(row: BrowserSyncEvent) -> str:
        if row.status == "pending_mapping" and row.failure_reason:
            text = str(row.failure_reason)
            if any(token in text for token in ("待人工", "待决策", "请确认", "可选择")):
                return "pending_decision"
        if row.display_bucket == "overall":
            return "overall"
        if row.mapped_source_type == BugSourceType.RETEST.value and row.mapped_test_case_id:
            return "retest_requirement_case_bug"
        if row.mapped_source_type == BugSourceType.RETEST.value:
            return "retest_requirement_free_bug"
        if row.mapped_test_case_id:
            return "requirement_case_bug"
        return "requirement_free_bug"

    @staticmethod
    def _auto_apply_testcase_enabled() -> bool:
        from app.core.config import settings

        return bool(settings.zentao_sync_auto_apply_testcase)

    @staticmethod
    def _auto_apply_bug_enabled() -> bool:
        from app.core.config import settings

        return bool(settings.zentao_sync_auto_apply_bug)

    def _emit_zentao_row_event(self, event_type: str, row: BrowserSyncEvent) -> None:
        try:
            sse_publish(event_type, {"item": self._serialize_event_summary(row)}, channels=["global"])
        except Exception:
            logger.debug("emit zentao sse failed | event_type=%s id=%s", event_type, row.id)
