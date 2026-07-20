from __future__ import annotations

import json
import logging
from datetime import timedelta
from typing import Any

from fastapi import HTTPException
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import BugTracking, Requirement, SoftwareProduct, User, UserZentaoBinding, Version, VersionType, ZentaoTestCaseMirror
from app.services.sync_lock_service import acquire_sync_lock, release_sync_lock
from app.services.zentao_auth_service import get_valid_token, invalidate_token
from app.services.zentao_client_service import ZentaoAPIError, ZentaoClient
from app.utils.time_utils import local_now, parse_external_datetime_to_local_naive

logger = logging.getLogger(__name__)

_sync_case_cache: dict[int, Any] = {}


def _testcase_recent_cache_ttl() -> timedelta:
    return timedelta(seconds=max(60, settings.zentao_workbench_testcase_recent_ttl_seconds))


def _testcase_detail_refresh_ttl() -> timedelta:
    return timedelta(seconds=max(60, settings.zentao_testcase_detail_refresh_ttl_seconds))


def _coerce_int(value: Any) -> int | None:
    if isinstance(value, dict):
        for key in ("id", "case", "story", "module", "product", "execution"):
            inner = value.get(key)
            try:
                return int(inner) if inner is not None and str(inner).strip() else None
            except (TypeError, ValueError):
                continue
        return None
    try:
        return int(value) if value is not None and str(value).strip() else None
    except (TypeError, ValueError):
        return None


def _extract_name(value: Any) -> str | None:
    if isinstance(value, dict):
        for key in ("realname", "name", "title", "account"):
            text = str(value.get(key) or "").strip()
            if text:
                return text
        return None
    text = str(value or "").strip()
    return text or None


def _extract_reference_name(value: Any) -> str | None:
    """Extract a display name from a Zentao relation without treating its ID as a name."""
    text = _extract_name(value)
    if not text or text.isdecimal():
        return None
    return text


def _extract_account(value: Any) -> str | None:
    if isinstance(value, dict):
        text = str(value.get("account") or "").strip()
        if text:
            return text
    text = str(value or "").strip()
    return text or None


def _extract_case_id(case: dict[str, Any]) -> int | None:
    """
    Pull the numeric Zentao case id from a raw testcase payload.

    v1 returns the canonical numeric id under `caseID`; the top-level `id`
    field is a Zentao record reference like `"case_21555"` and cannot be
    parsed as an int directly. Fall back to stripping that prefix when
    `caseID` is missing for forward-compat with older Zentao versions.
    """
    for key in ("caseID", "case_id"):
        value = case.get(key)
        try:
            iv = int(value) if value is not None and str(value).strip() else None
        except (TypeError, ValueError):
            iv = None
        if iv:
            return iv

    raw_id = case.get("id")
    if isinstance(raw_id, (int, float)):
        return int(raw_id) or None
    if isinstance(raw_id, str):
        text = raw_id.strip()
        if text.lower().startswith("case_"):
            text = text[5:]
        try:
            return int(text) or None
        except (TypeError, ValueError):
            return None
    return None


def _extract_case_payload(data: Any) -> dict[str, Any] | None:
    if isinstance(data, dict):
        for key in ("testcase", "case", "data"):
            bucket = data.get(key)
            if isinstance(bucket, dict) and (bucket.get("id") or bucket.get("caseID")):
                return bucket
        if data.get("id") or data.get("caseID"):
            return data
    return None


class ZentaoTestCaseService:
    def __init__(self, db: Session):
        self.db = db

    def _get_client_ctx(self, user_id: int) -> tuple[ZentaoClient, str] | None:
        binding = self.db.query(UserZentaoBinding).filter(UserZentaoBinding.user_id == user_id).first()
        if not binding or not binding.base_url:
            return None

        token = get_valid_token(user_id, self.db)
        if not token:
            return None

        base_url = binding.base_url.rstrip("/")
        return ZentaoClient(base_url=base_url, token=token), base_url

    def _collect_zentao_product_ids_for_software(self, software_id: int, software: SoftwareProduct) -> list[int]:
        ids: list[int] = []
        seen: set[int] = set()

        def _push(raw: Any) -> None:
            value = _coerce_int(raw)
            if value and value not in seen:
                seen.add(value)
                ids.append(value)

        _push(software.zentao_product_id)

        major_ids = [
            row[0]
            for row in self.db.query(Version.id)
            .filter(Version.version_type == VersionType.MAJOR, Version.software_id == software_id)
            .all()
        ]
        if major_ids:
            for (pid,) in (
                self.db.query(BugTracking.zentao_product_id)
                .filter(BugTracking.major_version_id.in_(major_ids), BugTracking.zentao_product_id.isnot(None))
                .distinct()
                .all()
            ):
                _push(pid)

        return ids

    def _fetch_recent_product_testcases(
        self,
        client: ZentaoClient,
        product_id: int,
        *,
        limit: int = 100,
        max_pages: int = 2,
    ) -> list[dict[str, Any]]:
        """
        Pull only the most recently edited testcase pages for a product.

        Tries `lastEditedDate_desc` first (the column the case-list view
        sorts by), falling back to `id_desc` and finally unsorted if the
        Zentao deployment rejects the orderBy parameter. Mirrors the
        bug-side `_fetch_recent_bug_collection` strategy so newly created
        or re-edited cases bubble to the top within the first page.
        """
        order_candidates = ("lastEditedDate_desc", "id_desc", None)
        last_error: ZentaoAPIError | None = None
        rows: list[dict[str, Any]] = []
        seen_ids: set[int] = set()

        for order_by in order_candidates:
            rows.clear()
            seen_ids.clear()
            try:
                for page in range(1, max_pages + 1):
                    params: dict[str, Any] = {"limit": limit, "page": page, "status": "all"}
                    if order_by:
                        params["orderBy"] = order_by
                    data = client.get(f"products/{product_id}/testcases", params=params)
                    page_rows = self._extract_testcase_rows(data)
                    if not page_rows:
                        break

                    new_count = 0
                    for row in page_rows:
                        case_payload = _extract_case_payload(row) or row
                        case_id = _extract_case_id(case_payload) if isinstance(case_payload, dict) else None
                        if case_id is None:
                            rows.append(row)
                            new_count += 1
                            continue
                        if case_id in seen_ids:
                            continue
                        seen_ids.add(case_id)
                        rows.append(row)
                        new_count += 1

                    if new_count == 0 or len(page_rows) < limit:
                        break
                return rows
            except ZentaoAPIError as exc:
                last_error = exc
                # 400/422 likely from an unrecognized orderBy → try next candidate
                if exc.status_code in (400, 422):
                    continue
                raise

        if last_error:
            raise last_error
        return rows

    def _fetch_product_testcases(self, client: ZentaoClient, product_id: int, *, limit: int = 200, max_pages: int = 50) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        seen_ids: set[int] = set()
        for page in range(1, max_pages + 1):
            data = client.get(
                f"products/{product_id}/testcases",
                # status=all so wait/blocked/done cases come along too — without
                # it Zentao silently filters to status=normal on some 18.x deployments.
                params={"limit": limit, "page": page, "status": "all"},
            )
            page_rows = self._extract_testcase_rows(data)
            if not page_rows:
                break

            new_count = 0
            for row in page_rows:
                case_payload = _extract_case_payload(row) or row
                case_id = _extract_case_id(case_payload) if isinstance(case_payload, dict) else None
                if case_id is None:
                    rows.append(row)
                    new_count += 1
                    continue
                if case_id in seen_ids:
                    continue
                seen_ids.add(case_id)
                rows.append(row)
                new_count += 1

            if new_count == 0 or len(page_rows) < limit:
                break
        return rows

    def _fetch_case_detail_with_retry(self, *, case_numeric_id: int, current_user: User) -> dict[str, Any] | None:
        ctx = self._get_client_ctx(current_user.id)
        if not ctx:
            return None
        client, _ = ctx
        try:
            return client.get_testcase(case_numeric_id)
        except ZentaoAPIError as exc:
            if exc.status_code != 401:
                raise
        invalidate_token(current_user.id, self.db)
        ctx = self._get_client_ctx(current_user.id)
        if not ctx:
            raise HTTPException(status_code=400, detail="禅道 token 刷新失败")
        refreshed_client, _ = ctx
        return refreshed_client.get_testcase(case_numeric_id)

    def _extract_testcase_rows(self, data: Any) -> list[dict[str, Any]]:
        if data is None:
            return []
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        if not isinstance(data, dict):
            return []

        for key in ("testcases", "cases", "data", "items", "rows"):
            bucket = data.get(key)
            if isinstance(bucket, list):
                return [item for item in bucket if isinstance(item, dict)]
            if isinstance(bucket, dict):
                values = [item for item in bucket.values() if isinstance(item, dict)]
                if values:
                    return values

        payload = _extract_case_payload(data)
        if payload:
            return [payload]
        return []

    def _steps_digest(self, steps: Any) -> str | None:
        if isinstance(steps, str):
            text = steps.strip()
            return text[:4000] or None
        if isinstance(steps, list):
            parts: list[str] = []
            for item in steps:
                if isinstance(item, dict):
                    step = str(item.get("desc") or item.get("step") or "").strip()
                    expect = str(item.get("expect") or "").strip()
                    if step:
                        parts.append(step)
                    if expect:
                        parts.append(f"EXPECT: {expect}")
                else:
                    text = str(item or "").strip()
                    if text:
                        parts.append(text)
            joined = "\n".join(parts).strip()
            return joined[:4000] or None
        if isinstance(steps, dict):
            parts = []
            for value in steps.values():
                text = str(value or "").strip()
                if text:
                    parts.append(text)
            joined = "\n".join(parts).strip()
            return joined[:4000] or None
        return None

    def _bugs_count(self, raw: Any) -> int | None:
        if raw is None:
            return None
        if isinstance(raw, int):
            return raw
        if isinstance(raw, (list, tuple, set)):
            return len(raw)
        if isinstance(raw, dict):
            return len(raw)
        try:
            return int(raw)
        except (TypeError, ValueError):
            return None

    def _normalize_testcase(self, raw: dict[str, Any], *, base_url: str) -> dict[str, Any] | None:
        case = _extract_case_payload(raw) or raw
        if not isinstance(case, dict):
            return None

        case_numeric_id = _extract_case_id(case)
        if not case_numeric_id:
            return None

        module_raw = case.get("module")
        module_id = _coerce_int(module_raw)
        module_name = _extract_reference_name(module_raw)

        product_raw = case.get("product")
        product_id = _coerce_int(product_raw)
        product_name = _extract_reference_name(product_raw)

        story_raw = case.get("story")
        execution_raw = case.get("execution") or case.get("task")
        last_runner_raw = case.get("lastRunner")

        case_id = f"u#{case_numeric_id}"
        title = str(case.get("title") or "").strip() or None
        status = str(case.get("status") or "").strip().lower() or None
        stage = str(case.get("stage") or "").strip().lower() or None
        case_type = str(case.get("type") or "").strip().lower() or None

        deleted_raw = case.get("deleted")
        deleted = False
        if isinstance(deleted_raw, bool):
            deleted = deleted_raw
        elif deleted_raw is not None:
            deleted = str(deleted_raw).strip() in {"1", "true", "True", "yes"}

        return {
            "zentao_case_id": case_id,
            "zentao_case_numeric_id": case_numeric_id,
            "zentao_product_id": product_id,
            "zentao_product_name": product_name,
            "zentao_module_id": module_id,
            "zentao_module_name": module_name,
            "zentao_story_id": _coerce_int(story_raw),
            "zentao_execution_id": _coerce_int(execution_raw),
            "title": title,
            "case_type": case_type,
            "stage": stage,
            "status": status,
            "pri": _coerce_int(case.get("pri")),
            "precondition": str(case.get("precondition") or "").strip() or None,
            "steps_digest": self._steps_digest(case.get("steps")),
            "last_runner_account": _extract_account(last_runner_raw),
            "last_runner_name": _extract_name(last_runner_raw),
            "last_run_date": parse_external_datetime_to_local_naive(case.get("lastRunDate")),
            "last_run_result": str(case.get("lastRunResult") or "").strip().lower() or None,
            "bugs_count": self._bugs_count(case.get("bugs")),
            "zentao_case_url": f"{base_url}/testcase-view-{case_numeric_id}.html",
            "deleted": deleted,
            "remote_opened_at": parse_external_datetime_to_local_naive(case.get("openedDate")),
            "remote_updated_at": parse_external_datetime_to_local_naive(case.get("lastEditedDate") or case.get("editedDate")),
            "raw_payload": json.dumps(raw, ensure_ascii=False),
        }

    def _upsert_case(self, normalized: dict[str, Any], *, sync_source: str) -> tuple[ZentaoTestCaseMirror, bool, bool]:
        row = (
            self.db.query(ZentaoTestCaseMirror)
            .filter(ZentaoTestCaseMirror.zentao_case_numeric_id == normalized["zentao_case_numeric_id"])
            .first()
        )
        created = False
        updated = False
        if not row:
            row = ZentaoTestCaseMirror(
                zentao_case_id=normalized["zentao_case_id"],
                zentao_case_numeric_id=normalized["zentao_case_numeric_id"],
            )
            self.db.add(row)
            self.db.flush()
            created = True

        for key, value in normalized.items():
            if getattr(row, key) != value:
                setattr(row, key, value)
                updated = True
        row.last_zentao_synced_at = local_now()
        row.sync_source = sync_source
        return row, created, updated

    def _needs_detail_refresh(self, row: ZentaoTestCaseMirror, normalized: dict[str, Any], created: bool) -> bool:
        if created:
            return True
        if row.remote_updated_at and normalized.get("remote_updated_at") and row.remote_updated_at != normalized["remote_updated_at"]:
            return True
        return not row.steps_digest or not row.precondition

    def _enrich_case_detail(self, row: ZentaoTestCaseMirror, detail_raw: dict[str, Any] | None, *, base_url: str) -> None:
        if not detail_raw:
            return
        detail = self._normalize_testcase(detail_raw, base_url=base_url)
        if not detail:
            return
        for key, value in detail.items():
            if value is not None:
                setattr(row, key, value)

    def sync_software_testcases(
        self,
        *,
        software_id: int,
        current_user: User,
        force: bool = False,
        sync_source: str = "manual_sync",
    ) -> dict[str, Any]:
        software = self.db.query(SoftwareProduct).filter(SoftwareProduct.id == software_id).first()
        if not software:
            raise HTTPException(status_code=404, detail="软件不存在")

        if not force:
            last = _sync_case_cache.get(software_id)
            if last and (local_now() - last) < _testcase_recent_cache_ttl():
                return {
                    "software_id": software_id,
                    "software_name": software.name,
                    "zentao_products": [],
                    "remote_total": 0,
                    "created": 0,
                    "updated": 0,
                    "detail_refreshed": 0,
                    "cached": True,
                    "sync_source": sync_source,
                }

        lock_key = f"testcase_full:{software_id}"
        if not acquire_sync_lock(self.db, lock_key, ttl_seconds=1800):
            raise HTTPException(status_code=409, detail="该软件正在同步禅道用例，请稍后再试")

        try:
            ctx = self._get_client_ctx(current_user.id)
            if not ctx:
                raise HTTPException(status_code=400, detail="当前用户未配置可用的禅道绑定")
            client, base_url = ctx

            product_ids = self._collect_zentao_product_ids_for_software(software_id, software)
            if not product_ids:
                raise HTTPException(status_code=400, detail="该软件下未找到禅道产品映射，请先配置 zentao_product_id")
            remote_total = 0
            created = 0
            updated = 0
            skipped_invalid = 0
            detail_refreshed = 0
            # Cap follow-up detail fetches per sync. The list endpoint already
            # carries everything we display in the case center; deferring
            # detail enrichment to on-demand opens or the next scheduled sync
            # avoids running thousands of HTTP calls during the first
            # full-load (e.g. product 15 has ~4k cases).
            detail_refresh_budget = 30

            for product_id in product_ids:
                try:
                    remote_rows = self._fetch_product_testcases(client, product_id)
                except ZentaoAPIError as exc:
                    if exc.status_code != 401:
                        raise
                    invalidate_token(current_user.id, self.db)
                    ctx = self._get_client_ctx(current_user.id)
                    if not ctx:
                        raise HTTPException(status_code=400, detail="禅道 token 刷新失败")
                    client, base_url = ctx
                    remote_rows = self._fetch_product_testcases(client, product_id)

                remote_total += len(remote_rows)
                product_batch_count = 0
                for raw in remote_rows:
                    normalized = self._normalize_testcase(raw, base_url=base_url)
                    if not normalized:
                        skipped_invalid += 1
                        continue
                    existing = (
                        self.db.query(ZentaoTestCaseMirror)
                        .filter(ZentaoTestCaseMirror.zentao_case_numeric_id == normalized["zentao_case_numeric_id"])
                        .first()
                    )
                    row, created_now, updated_now = self._upsert_case(normalized, sync_source=sync_source)
                    created += int(created_now)
                    updated += int((not created_now) and updated_now)
                    if detail_refresh_budget > 0 and self._needs_detail_refresh(existing or row, normalized, created_now):
                        detail_raw = self._fetch_case_detail_with_retry(case_numeric_id=row.zentao_case_numeric_id, current_user=current_user)
                        self._enrich_case_detail(row, detail_raw, base_url=base_url)
                        row.last_zentao_synced_at = local_now()
                        row.sync_source = sync_source
                        detail_refreshed += 1
                        detail_refresh_budget -= 1

                    # Commit in batches so a 4k-row first-sync doesn't hold
                    # one giant transaction open for minutes.
                    product_batch_count += 1
                    if product_batch_count >= 200:
                        self.db.commit()
                        product_batch_count = 0
                if product_batch_count:
                    self.db.commit()

            self.db.commit()
            _sync_case_cache[software_id] = local_now()
            return {
                "software_id": software_id,
                "software_name": software.name,
                "zentao_products": product_ids,
                "remote_total": remote_total,
                "created": created,
                "updated": updated,
                "skipped_invalid": skipped_invalid,
                "detail_refreshed": detail_refreshed,
                "cached": False,
                "sync_source": sync_source,
            }
        finally:
            release_sync_lock(self.db, lock_key)

    def sync_recent_software_testcases(
        self,
        *,
        software_id: int,
        current_user: User,
        force: bool = True,
        sync_source: str = "manual_incremental",
        limit: int = 100,
        max_pages_per_product: int = 2,
    ) -> dict[str, Any]:
        """
        Lightweight incremental sync: pulls only the most recently edited
        testcase pages per Zentao product instead of walking the entire
        backlog. Suitable for the manual "增量同步" button — newly created
        or re-edited cases land within seconds.

        Does NOT detect long-untouched deletions; rely on the full
        `sync_software_testcases` path for that.
        """
        software = self.db.query(SoftwareProduct).filter(SoftwareProduct.id == software_id).first()
        if not software:
            raise HTTPException(status_code=404, detail="软件不存在")

        lock_key = f"testcase_recent:{software_id}"
        if not acquire_sync_lock(self.db, lock_key, ttl_seconds=600):
            raise HTTPException(status_code=409, detail="该软件正在执行用例增量同步，请稍后再试")

        started = local_now()
        try:
            ctx = self._get_client_ctx(current_user.id)
            if not ctx:
                raise HTTPException(status_code=400, detail="当前用户未配置可用的禅道绑定")
            client, base_url = ctx

            product_ids = self._collect_zentao_product_ids_for_software(software_id, software)
            if not product_ids:
                raise HTTPException(status_code=400, detail="该软件下未找到禅道产品映射，请先配置 zentao_product_id")

            remote_total = 0
            created = 0
            updated = 0
            skipped_invalid = 0
            detail_refreshed = 0
            detail_refresh_budget = 20

            for product_id in product_ids:
                try:
                    remote_rows = self._fetch_recent_product_testcases(
                        client, product_id, limit=limit, max_pages=max_pages_per_product
                    )
                except ZentaoAPIError as exc:
                    if exc.status_code != 401:
                        raise
                    invalidate_token(current_user.id, self.db)
                    ctx = self._get_client_ctx(current_user.id)
                    if not ctx:
                        raise HTTPException(status_code=400, detail="禅道 token 刷新失败")
                    client, base_url = ctx
                    remote_rows = self._fetch_recent_product_testcases(
                        client, product_id, limit=limit, max_pages=max_pages_per_product
                    )

                remote_total += len(remote_rows)
                for raw in remote_rows:
                    normalized = self._normalize_testcase(raw, base_url=base_url)
                    if not normalized:
                        skipped_invalid += 1
                        continue
                    existing = (
                        self.db.query(ZentaoTestCaseMirror)
                        .filter(ZentaoTestCaseMirror.zentao_case_numeric_id == normalized["zentao_case_numeric_id"])
                        .first()
                    )
                    row, created_now, updated_now = self._upsert_case(normalized, sync_source=sync_source)
                    created += int(created_now)
                    updated += int((not created_now) and updated_now)
                    if detail_refresh_budget > 0 and self._needs_detail_refresh(existing or row, normalized, created_now):
                        detail_raw = self._fetch_case_detail_with_retry(case_numeric_id=row.zentao_case_numeric_id, current_user=current_user)
                        self._enrich_case_detail(row, detail_raw, base_url=base_url)
                        row.last_zentao_synced_at = local_now()
                        row.sync_source = sync_source
                        detail_refreshed += 1
                        detail_refresh_budget -= 1

            self.db.commit()
            elapsed = (local_now() - started).total_seconds()
            return {
                "software_id": software_id,
                "software_name": software.name,
                "zentao_products": product_ids,
                "remote_total": remote_total,
                "created": created,
                "updated": updated,
                "skipped_invalid": skipped_invalid,
                "detail_refreshed": detail_refreshed,
                "elapsed_seconds": elapsed,
                "cached": False,
                "sync_mode": "recent",
                "sync_source": sync_source,
            }
        finally:
            release_sync_lock(self.db, lock_key)

    def _linked_requirements(self, story_id: int | None, software_id: int | None = None) -> list[dict[str, Any]]:
        if not story_id:
            return []

        query = (
            self.db.query(Requirement, Version)
            .join(Version, Requirement.major_version_id == Version.id)
            .filter(Requirement.zentao_story_id == story_id)
        )
        if software_id:
            query = query.filter(Version.software_id == software_id)

        rows = query.all()
        return [
            {
                "id": req.id,
                "zentao_req_id": req.zentao_req_id,
                "title": req.title,
                "major_version_id": req.major_version_id,
                "major_version_name": version.version_no if version else None,
            }
            for req, version in rows
        ]

    def _serialize_row(self, row: ZentaoTestCaseMirror, *, software_id: int | None = None) -> dict[str, Any]:
        linked_requirements = self._linked_requirements(row.zentao_story_id, software_id=software_id)
        return {
            "id": row.id,
            "zentao_case_id": row.zentao_case_id,
            "zentao_case_numeric_id": row.zentao_case_numeric_id,
            "zentao_case_url": row.zentao_case_url,
            "title": row.title,
            "status": row.status,
            "stage": row.stage,
            "case_type": row.case_type,
            "pri": row.pri,
            "zentao_product_id": row.zentao_product_id,
            "zentao_product_name": row.zentao_product_name,
            "zentao_module_id": row.zentao_module_id,
            "zentao_module_name": row.zentao_module_name,
            "zentao_story_id": row.zentao_story_id,
            "zentao_execution_id": row.zentao_execution_id,
            "last_runner_name": row.last_runner_name,
            "last_run_date": row.last_run_date.isoformat() if row.last_run_date else None,
            "last_run_result": row.last_run_result,
            "bugs_count": row.bugs_count,
            "deleted": row.deleted,
            "remote_updated_at": row.remote_updated_at.isoformat() if row.remote_updated_at else None,
            "last_zentao_synced_at": row.last_zentao_synced_at.isoformat() if row.last_zentao_synced_at else None,
            "sync_source": row.sync_source,
            "linked_requirements": linked_requirements,
        }

    def list_cases(
        self,
        *,
        software_id: int | None = None,
        product_id: int | None = None,
        module_id: int | None = None,
        requirement_id: int | None = None,
        status: str | None = None,
        keyword: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> dict[str, Any]:
        query = self.db.query(ZentaoTestCaseMirror)

        if software_id:
            software = self.db.query(SoftwareProduct).filter(SoftwareProduct.id == software_id).first()
            if not software:
                raise HTTPException(status_code=404, detail="软件不存在")
            product_ids = self._collect_zentao_product_ids_for_software(software_id, software)
            if product_ids:
                query = query.filter(ZentaoTestCaseMirror.zentao_product_id.in_(product_ids))
            else:
                query = query.filter(ZentaoTestCaseMirror.id == -1)

        if product_id:
            query = query.filter(ZentaoTestCaseMirror.zentao_product_id == product_id)

        if module_id:
            query = query.filter(ZentaoTestCaseMirror.zentao_module_id == module_id)

        if requirement_id:
            req = self.db.query(Requirement).filter(Requirement.id == requirement_id).first()
            if not req:
                raise HTTPException(status_code=404, detail="需求不存在")
            if req.zentao_story_id:
                query = query.filter(ZentaoTestCaseMirror.zentao_story_id == req.zentao_story_id)
            else:
                query = query.filter(ZentaoTestCaseMirror.id == -1)

        if status:
            query = query.filter(func.lower(func.coalesce(ZentaoTestCaseMirror.status, "")) == status.strip().lower())

        kw = str(keyword or "").strip().lower()
        if kw:
            query = query.filter(
                or_(
                    func.lower(func.coalesce(ZentaoTestCaseMirror.zentao_case_id, "")).like(f"%{kw}%"),
                    func.lower(func.coalesce(ZentaoTestCaseMirror.title, "")).like(f"%{kw}%"),
                )
            )

        total = query.count()
        rows = (
            query.order_by(
                ZentaoTestCaseMirror.remote_updated_at.desc(),
                ZentaoTestCaseMirror.zentao_case_numeric_id.desc(),
            )
            .offset((page - 1) * page_size)
            .limit(page_size)
            .all()
        )
        return {
            "items": [self._serialize_row(row, software_id=software_id) for row in rows],
            "total": total,
            "page": page,
            "page_size": page_size,
        }

    def get_case_detail(
        self,
        case_numeric_id: int,
        *,
        software_id: int | None = None,
        current_user: User | None = None,
    ) -> dict[str, Any]:
        row = (
            self.db.query(ZentaoTestCaseMirror)
            .filter(ZentaoTestCaseMirror.zentao_case_numeric_id == case_numeric_id)
            .first()
        )
        if not row:
            raise HTTPException(status_code=404, detail="用例不存在")

        # On-demand detail hydration: when the user explicitly opens a case
        # whose mirrored detail is older than the configured TTL (or has never
        # been hydrated), refresh it once before answering.
        if current_user is not None:
            stale = (
                row.last_zentao_synced_at is None
                or (local_now() - row.last_zentao_synced_at) > _testcase_detail_refresh_ttl()
                or not row.steps_digest
                or not row.precondition
            )
            if stale:
                try:
                    ctx = self._get_client_ctx(current_user.id)
                    if ctx:
                        _, base_url = ctx
                        detail_raw = self._fetch_case_detail_with_retry(
                            case_numeric_id=row.zentao_case_numeric_id,
                            current_user=current_user,
                        )
                        if detail_raw:
                            self._enrich_case_detail(row, detail_raw, base_url=base_url)
                            row.last_zentao_synced_at = local_now()
                            row.sync_source = "on_enter_refresh"
                            self.db.commit()
                except Exception:
                    self.db.rollback()
                    logger.warning(
                        "on-demand testcase detail refresh failed | case_numeric_id=%s",
                        case_numeric_id,
                        exc_info=True,
                    )

        data = self._serialize_row(row, software_id=software_id)
        data.update(
            {
                "precondition": row.precondition,
                "steps_digest": row.steps_digest,
                "steps_structured": self._extract_structured_steps(row.raw_payload),
                "linked_bugs": self._collect_linked_bugs(row),
            }
        )
        return data

    def _extract_structured_steps(self, raw_payload: str | None) -> list[dict[str, Any]]:
        if not raw_payload:
            return []
        try:
            data = json.loads(raw_payload)
        except (TypeError, ValueError):
            return []
        case = _extract_case_payload(data) or data
        if not isinstance(case, dict):
            return []
        steps = case.get("steps")
        if isinstance(steps, dict):
            steps = list(steps.values())
        if not isinstance(steps, list):
            return []

        result: list[dict[str, Any]] = []

        def _walk(items: list[Any], parent_no: str = "") -> None:
            for idx, item in enumerate(items, start=1):
                if not isinstance(item, dict):
                    text = str(item or "").strip()
                    if text:
                        no = f"{parent_no}{idx}" if parent_no else str(idx)
                        result.append({"no": no, "is_group": False, "step": text, "expect": ""})
                    continue
                step_text = str(item.get("desc") or item.get("step") or "").strip()
                expect_text = str(item.get("expect") or "").strip()
                step_type = str(item.get("type") or "").strip().lower()
                children = item.get("children") or item.get("steps") or []
                no_val = str(item.get("id") or item.get("order") or "").strip()
                no = no_val or (f"{parent_no}{idx}" if parent_no else str(idx))
                if step_type == "group" or (children and not step_text and not expect_text):
                    result.append({"no": no, "is_group": True, "step": step_text or "分组", "expect": ""})
                    if isinstance(children, list):
                        _walk(children, parent_no=f"{no}.")
                    continue
                if step_text or expect_text:
                    result.append({"no": no, "is_group": False, "step": step_text, "expect": expect_text})
                if isinstance(children, list) and children:
                    _walk(children, parent_no=f"{no}.")

        _walk(steps)
        return result

    def _collect_linked_bugs(self, row: ZentaoTestCaseMirror) -> list[dict[str, Any]]:
        case_key = str(row.zentao_case_id or "").strip().lower()
        numeric = row.zentao_case_numeric_id
        keys: set[str] = set()
        if case_key:
            keys.add(case_key)
        if numeric:
            keys.add(str(numeric))
            keys.add(f"u#{numeric}")
        if not keys:
            return []
        rows = (
            self.db.query(BugTracking)
            .filter(
                func.lower(func.coalesce(BugTracking.zentao_linked_case_id, "")).in_([k.lower() for k in keys]),
                BugTracking.zentao_deleted.isnot(True),
            )
            .order_by(BugTracking.zentao_opened_at.desc(), BugTracking.id.desc())
            .all()
        )
        result: list[dict[str, Any]] = []
        for bug in rows:
            result.append(
                {
                    "id": bug.id,
                    "bug_id": bug.bug_id,
                    "zentao_bug_url": bug.zentao_bug_url,
                    "zentao_bug_title": bug.zentao_bug_title,
                    "zentao_live_status": bug.zentao_live_status,
                    "closed": bool(bug.closed),
                    "zentao_opened_at": bug.zentao_opened_at.isoformat() if bug.zentao_opened_at else None,
                    "zentao_opened_by_name": bug.zentao_opened_by_name,
                    "zentao_assigned_to_name": bug.zentao_assigned_to_name,
                }
            )
        return result

    def list_modules(self, *, software_id: int | None = None) -> list[dict[str, Any]]:
        """
        Return distinct (module_id, module_name) tuples present in the mirror,
        scoped to the given software when supplied. Used by the testcase
        center module dropdown.
        """
        query = self.db.query(
            ZentaoTestCaseMirror.zentao_module_id,
            ZentaoTestCaseMirror.zentao_module_name,
        ).filter(ZentaoTestCaseMirror.zentao_module_id.isnot(None))

        if software_id:
            software = self.db.query(SoftwareProduct).filter(SoftwareProduct.id == software_id).first()
            if software:
                product_ids = self._collect_zentao_product_ids_for_software(software_id, software)
                if product_ids:
                    query = query.filter(ZentaoTestCaseMirror.zentao_product_id.in_(product_ids))
                else:
                    return []

        seen: dict[int, str | None] = {}
        for module_id, module_name in query.distinct().all():
            if module_id is None:
                continue
            if module_id not in seen:
                seen[module_id] = module_name
        return [
            {"id": mid, "name": name or f"模块 {mid}"}
            for mid, name in sorted(seen.items())
        ]
