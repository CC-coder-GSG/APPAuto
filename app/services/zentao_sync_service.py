from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

from fastapi import HTTPException
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models import (
    BugSourceType,
    BugTracking,
    BrowserSyncEvent,
    Requirement,
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
from app.services.zentao_matcher import (
    extract_requirement_numeric_id,
    normalize_execution_name,
    normalize_requirement_title,
    parse_affected_version,
    pick_best_title_match,
)

logger = logging.getLogger(__name__)


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
        page: int = 1,
        page_size: int = 20,
    ) -> dict[str, Any]:
        date_from_dt = self._parse_date(date_from, end_of_day=False)
        date_to_dt = self._parse_date(date_to, end_of_day=True)
        result = self.repo.list_events(
            entity_type=entity_type,
            status=status,
            display_bucket=display_bucket,
            source_type=source_type,
            keyword=keyword,
            date_from=date_from_dt,
            date_to=date_to_dt,
            only_unapplied=only_unapplied,
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
        return self._serialize_event_detail(row)

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
            return {"ok": True, "event_id": row.id, "status": row.status, "message": "应用成功"}
        except HTTPException as exc:
            row.status = "failed"
            row.failure_reason = str(exc.detail)
            self.repo.save(row)
            logger.warning("同步事件应用失败 | eventId=%s detail=%s", row.id, exc.detail)
            raise
        except Exception as exc:
            row.status = "failed"
            row.failure_reason = str(exc)
            self.repo.save(row)
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
        requirement_candidates = self._recommend_requirements(row)
        if len(requirement_candidates) == 1:
            req = requirement_candidates[0]
            row.mapped_requirement_id = req.id
            row.mapped_major_version_id = req.major_version_id
            row.display_bucket = "requirement"
        elif len(requirement_candidates) > 1:
            row.status = "pending_mapping"
            row.failure_reason = "自动匹配到多个需求，请人工确认"
            return
        else:
            row.display_bucket = "overall"

        if row.linked_case_id and not row.mapped_test_case_id:
            row.mapped_test_case_id = self._find_test_case_id(row.linked_case_id)

        if not row.mapped_major_version_id:
            execution = normalize_execution_name(
                self._clean_text(draft.get("executionName") or draft.get("executionId") or row.zentao_execution_name)
            )
            major = self._match_major_by_execution(
                execution_id=execution,
                product_name=self._clean_text(draft.get("productName") or row.zentao_product_name),
                project_name=self._clean_text(draft.get("projectName") or row.zentao_project_name),
            )
            if major:
                row.mapped_major_version_id = major.id

        if row.mapped_major_version_id and not row.mapped_minor_version_id:
            minor = self._match_minor_by_affected_version(
                major_id=row.mapped_major_version_id,
                affected_version=self._clean_text(draft.get("affectedVersion") or row.zentao_affected_version),
            )
            if minor:
                row.mapped_minor_version_id = minor.id

        self._mark_mapped_status(row)

    def _recommend_requirements(self, row: BrowserSyncEvent) -> list[Requirement]:
        payload = self._event_payload(row)
        draft = payload.get("draft", {})
        req_id = self._normalize_prefixed_id("r#", draft.get("requirementId")) if draft.get("requirementId") else row.zentao_req_id
        req_name = self._clean_text(draft.get("requirementName"))
        product_name = self._clean_text(draft.get("productName"))
        project_name = self._clean_text(draft.get("projectName"))
        execution_id = self._clean_text(draft.get("executionId"))

        query = self.db.query(Requirement)
        if req_id:
            query = query.filter(Requirement.zentao_req_id == req_id)
            rows = query.order_by(Requirement.id.desc()).all()
            if rows:
                if execution_id:
                    major = self._match_major_by_execution(execution_id, product_name, project_name)
                    if major:
                        exact = [r for r in rows if r.major_version_id == major.id]
                        if exact:
                            return exact
                return rows

        if req_name:
            query = self.db.query(Requirement).filter(Requirement.title == req_name)
            major = self._match_major_by_execution(execution_id, product_name, project_name)
            if major:
                query = query.filter(Requirement.major_version_id == major.id)
            rows = query.order_by(Requirement.id.desc()).all()
            if rows:
                return rows

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

        created = self.requirement_service.add_case_to_requirement(row.mapped_requirement_id, case_no, actor_id=actor_id)
        new_case = self.db.query(TestCase).filter(TestCase.id == created["id"]).first()
        if not new_case:
            raise HTTPException(status_code=500, detail="用例创建后读取失败")

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
        if not row.mapped_minor_version_id:
            raise HTTPException(status_code=400, detail="Bug 事件尚未映射 minor_version_id")
        if row.display_bucket != "overall" and not row.mapped_requirement_id:
            raise HTTPException(status_code=400, detail="Bug 事件尚未映射 requirement_id")
        if row.display_bucket == "overall" and not row.mapped_major_version_id:
            raise HTTPException(status_code=400, detail="overall Bug 事件尚未映射 major_version_id")

        payload = self._event_payload(row)
        bug_id_raw = payload.get("result", {}).get("zentaoBugId") or row.zentao_bug_id
        if not bug_id_raw:
            raise HTTPException(status_code=400, detail="缺少 zentaoBugId")

        bug_no = self._normalize_prefixed_id("b#", bug_id_raw)
        existing = self.db.query(BugTracking).filter(BugTracking.bug_id == bug_no).first()
        if existing:
            row.applied_bug_tracking_id = existing.id
            return

        actor = self._resolve_actor_for_apply(payload, actor_id)
        draft = payload.get("draft", {})
        source_type = self._parse_bug_source_type(row.mapped_source_type or draft.get("sourceType"))

        if row.display_bucket == "overall" and not row.mapped_requirement_id:
            bug_row = BugTracking(
                major_version_id=row.mapped_major_version_id,  # type: ignore[arg-type]
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
                requirement_id=row.mapped_requirement_id,  # type: ignore[arg-type]
                source_type=source_type,
                source_ref=row.mapped_source_ref or draft.get("sourceRef"),
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
        if not execution_id:
            return None

        query = self.db.query(Version).filter(
            Version.version_type == VersionType.MAJOR,
            Version.version_no == execution_id,
        )

        software_ids = self._guess_software_ids(product_name, project_name)
        if software_ids:
            query = query.filter(Version.software_id.in_(software_ids))

        return query.order_by(Version.id.desc()).first()

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
            "title": draft.get("bugTitle") or draft.get("caseTitle"),
            "requirement_name": draft.get("requirementName") or row.zentao_req_id,
            "mapped_requirement_id": row.mapped_requirement_id,
            "mapped_major_version_id": row.mapped_major_version_id,
            "mapped_minor_version_id": row.mapped_minor_version_id,
            "mapped_source_type": row.mapped_source_type,
            "mapped_source_ref": row.mapped_source_ref,
            "applied_case_id": row.applied_case_id,
            "applied_bug_tracking_id": row.applied_bug_tracking_id,
            "failure_reason": row.failure_reason,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        }

    @staticmethod
    def _serialize_event_detail(row: BrowserSyncEvent) -> dict[str, Any]:
        payload = ZentaoSyncService._event_payload(row)
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
            "zentao_product_id": row.zentao_product_id,
            "zentao_project_id": row.zentao_project_id,
            "mapped_requirement_id": row.mapped_requirement_id,
            "mapped_major_version_id": row.mapped_major_version_id,
            "mapped_minor_version_id": row.mapped_minor_version_id,
            "mapped_source_type": row.mapped_source_type,
            "mapped_source_ref": row.mapped_source_ref,
            "applied_case_id": row.applied_case_id,
            "applied_bug_tracking_id": row.applied_bug_tracking_id,
            "draft": payload.get("draft"),
            "result": payload.get("result"),
            "raw_payload": payload,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
            "recommended_requirement_id": row.mapped_requirement_id,
            "recommended_major_version_id": row.mapped_major_version_id,
            "recommended_minor_version_id": row.mapped_minor_version_id,
            "recommended_source_type": row.mapped_source_type or ("manual" if row.entity_type == "bug" else None),
            "recommended_display_bucket": row.display_bucket,
            "recommended_hint": (
                "需求池：挂到具体需求下；总览池：仅归属版本整体"
                if row.entity_type == "bug"
                else "用例事件建议映射到具体需求"
            ),
        }

    @staticmethod
    def _auto_apply_testcase_enabled() -> bool:
        from app.core.config import settings

        return bool(settings.zentao_sync_auto_apply_testcase)
