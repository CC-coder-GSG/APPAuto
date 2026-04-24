"""
Overall-Test service (formerly known as Stage5).

The historical "Stage5" name is preserved in the DB schema
(`bug_stage5_records`, `BugStage5Record`) and in audit-log action strings
(`stage5.submit_result`, `stage5.add_issue`) to avoid destructive
migrations. Everything else has been renamed to `overall_test` /
`OverallTestService`. A thin shim at `app.services.stage5_service`
re-exports `Stage5Service = OverallTestService` for backward
compatibility.
"""
from __future__ import annotations

import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta

from fastapi import HTTPException
from sqlalchemy.orm import Session, joinedload

from app.models import BugSourceType, BugStage5Record, BugTracking, Requirement, TestCase, User, Version, VersionType
from app.models.user_zentao_binding import UserZentaoBinding
from app.services.audit_service import audit
from app.services.sse_service import sse_publish
from app.services.zentao_auth_service import get_valid_token, invalidate_token
from app.services.zentao_client_service import ZentaoAPIError, ZentaoClient
from app.utils.time_utils import local_now, parse_external_datetime_to_local_naive

logger = logging.getLogger(__name__)

_MAJOR_V_RE = re.compile(r"^V(\d+)\.(\d+)\.(\d+)(?:\.(\d+))?$", re.IGNORECASE)

# In-memory sync cache: major_version_id → datetime of last sync
_sync_cache: dict[int, datetime] = {}
_SYNC_CACHE_TTL = timedelta(minutes=5)
_BUILD_FETCH_CONCURRENCY = 6

# Module-level in-flight guard for per-software full sync jobs so a double
# click does not kick off two concurrent full pulls against Zentao.
_sync_all_in_progress: set[int] = set()
_sync_all_cache: dict[int, datetime] = {}
_SYNC_ALL_CACHE_TTL = timedelta(minutes=15)
_PRODUCT_FETCH_CONCURRENCY = 6


def _coerce_str_id(value) -> str | None:
    if value is None:
        return None
    try:
        return str(int(value))
    except (TypeError, ValueError):
        text = str(value).strip()
        return text or None

# Allowed status tokens for the overview `statuses` filter
_ALLOWED_STATUSES = {"active", "closed", "resolved", "local"}


def _normalize_statuses(raw: str | list[str] | None) -> list[str]:
    """Parse a comma-separated string or list into a deduped lowercase list."""
    if raw is None:
        return []
    if isinstance(raw, str):
        parts = [p.strip().lower() for p in raw.split(",")]
    else:
        parts = [str(p or "").strip().lower() for p in raw]
    return [p for p in dict.fromkeys(parts) if p in _ALLOWED_STATUSES]


def _normalize_keyword(raw: str | None) -> str:
    """
    Standardize a search keyword:
    - strip whitespace
    - strip `b#` prefix variants (case-insensitive)
    - lowercase for case-insensitive comparison
    """
    text = str(raw or "").strip()
    if not text:
        return ""
    if text.lower().startswith("b#"):
        text = text[2:]
    return text.strip().lower()


def _bug_effective_status(bug: BugTracking) -> str:
    """
    Unified status classification used by the status-filter.

    Returns one of: 'closed', 'resolved', 'active', 'local'.
    Mirrors the frontend's `isS5BugEffectivelyClosed` heuristic so both
    sides agree.
    """
    if not bug.zentao_bug_id:
        return "local"
    live = str(bug.zentao_live_status or "").strip().lower()
    assigned = str(bug.zentao_assigned_to_name or "").strip().lower()
    if (
        live == "closed"
        or bool(bug.zentao_close_date)
        or bool(bug.zentao_closed_by_name)
        or assigned == "closed"
    ):
        return "closed"
    if live == "resolved":
        return "resolved"
    return "active"


def _bug_matches_keyword(bug: BugTracking, keyword: str) -> bool:
    if not keyword:
        return True
    haystacks = [
        str(bug.bug_id or "").lower(),
        str(bug.zentao_bug_id or "").lower(),
    ]
    return any(keyword in h for h in haystacks if h)


class OverallTestService:
    def __init__(self, db: Session):
        self.db = db

    def overview(
        self,
        major_version_id: int,
        current_user: User,
        software_id: int | None = None,
        statuses: str | list[str] | None = None,
        keyword: str | None = None,
    ) -> dict:
        """
        Build the overall-test overview.

        Optional filters:
        - `statuses`: comma-separated list of {'active', 'closed',
          'resolved', 'local'}. Applied after local fetch.
        - `keyword`: fuzzy match against `bug_id` and `zentao_bug_id`
          (b# prefix and whitespace are stripped before compare).

        Stats (total / closed / pending / ready_rate) are computed from
        the post-filter result so the frontend can render them directly.
        """
        all_versions_mode = (major_version_id == 0 and software_id)
        status_filter = _normalize_statuses(statuses)
        norm_keyword = _normalize_keyword(keyword)

        if all_versions_mode:
            majors = (
                self.db.query(Version)
                .filter(
                    Version.version_type == VersionType.MAJOR,
                    Version.software_id == software_id,
                )
                .all()
            )
            major_id_to_no: dict[int, str] = {v.id: v.version_no for v in majors}
            major_ids = list(major_id_to_no.keys())

            reqs = (
                self.db.query(Requirement)
                .options(joinedload(Requirement.test_cases), joinedload(Requirement.test_executions))
                .filter(Requirement.major_version_id.in_(major_ids))
                .all()
            ) if major_ids else []

            bugs = (
                self.db.query(BugTracking)
                .options(
                    joinedload(BugTracking.stage5_records).joinedload(BugStage5Record.user),
                    joinedload(BugTracking.stage5_records).joinedload(BugStage5Record.minor_version),
                    joinedload(BugTracking.dispatched_to),
                )
                .filter(
                    BugTracking.major_version_id.in_(major_ids),
                    BugTracking.zentao_deleted.isnot(True),
                )
                .order_by(BugTracking.created_at.desc(), BugTracking.id.desc())
                .all()
            ) if major_ids else []
        else:
            major_id_to_no = {}
            reqs = (
                self.db.query(Requirement)
                .options(joinedload(Requirement.test_cases), joinedload(Requirement.test_executions))
                .filter(Requirement.major_version_id == major_version_id)
                .all()
            )
            bugs = (
                self.db.query(BugTracking)
                .options(
                    joinedload(BugTracking.stage5_records).joinedload(BugStage5Record.user),
                    joinedload(BugTracking.stage5_records).joinedload(BugStage5Record.minor_version),
                    joinedload(BugTracking.dispatched_to),
                )
                .filter(
                    BugTracking.major_version_id == major_version_id,
                    BugTracking.zentao_deleted.isnot(True),
                )
                .order_by(BugTracking.created_at.desc(), BugTracking.id.desc())
                .all()
            )

        base_total = len(bugs)
        base_closed_count = sum(1 for bug in bugs if bug.closed)
        base_pending_count = base_total - base_closed_count
        base_ready_rate = 100 if base_total == 0 else round(base_closed_count * 100 / base_total)

        # Apply server-side filters (status + keyword) before building the payload
        if status_filter:
            bugs = [b for b in bugs if _bug_effective_status(b) in status_filter]
        if norm_keyword:
            bugs = [b for b in bugs if _bug_matches_keyword(b, norm_keyword)]

        bug_pool = []
        for bug in bugs:
            other_records = []
            my_record = None
            for record in bug.stage5_records:
                if record.user_id != current_user.id:
                    other_records.append(
                        {
                            "username": record.user.shown_name,
                            "minor_version_no": record.minor_version.version_no if record.minor_version else "未知",
                            "test_done": record.test_done,
                            "resolution": record.resolution,
                            "source": record.source or "manual",
                        }
                    )
                else:
                    my_record = record

            entry: dict = {
                "id": bug.id,
                "bug_id": bug.bug_id,
                "zentao_bug_id": bug.zentao_bug_id,
                "zentao_bug_url": bug.zentao_bug_url,
                "zentao_bug_title": bug.zentao_bug_title,
                "zentao_live_status": bug.zentao_live_status or "",
                "zentao_closed_by_name": bug.zentao_closed_by_name or "",
                "zentao_close_comment": bug.zentao_close_comment or "",
                "zentao_close_date": bug.zentao_close_date.strftime("%Y-%m-%d %H:%M") if bug.zentao_close_date else "",
                "zentao_assigned_to_name": bug.zentao_assigned_to_name or "",
                "last_zentao_synced_at": bug.last_zentao_synced_at.strftime("%Y-%m-%d %H:%M") if bug.last_zentao_synced_at else "",
                "source_type": bug.source_type.value,
                "source_ref": bug.source_ref,
                "requirement_id": bug.requirement_id,
                "found_minor_version_id": bug.found_minor_version_id,
                "fixed_minor_version_id": bug.fixed_minor_version_id,
                "closed": bug.closed,
                "my_test_done": my_record.test_done if my_record else False,
                "my_resolution": my_record.resolution if my_record else "fixed",
                "my_source": my_record.source if my_record else "",
                "my_comment": my_record.comment if my_record else "",
                "other_records": other_records,
                "dispatched_to_name": bug.dispatched_to.shown_name if bug.dispatched_to else None,
                "is_retest_failed": getattr(bug, "is_retest_failed", False),
                "effective_status": _bug_effective_status(bug),
            }
            if all_versions_mode:
                entry["major_version_no"] = major_id_to_no.get(bug.major_version_id, "")

            bug_pool.append(entry)

        filtered_total = len(bug_pool)
        filtered_closed_count = sum(1 for e in bug_pool if e["closed"])
        filtered_pending_count = filtered_total - filtered_closed_count
        filtered_ready_rate = 100 if filtered_total == 0 else round(filtered_closed_count * 100 / filtered_total)

        return {
            "major_version_id": major_version_id,
            "all_versions_mode": bool(all_versions_mode),
            "requirements": [
                {
                    "id": r.id,
                    "zentao_req_id": r.zentao_req_id,
                    "title": r.title,
                    "case_ids": [c.zentao_case_id for c in r.test_cases],
                    "history_bug_ids": [e.bug_id for e in r.test_executions if e.bug_id],
                }
                for r in reqs
            ],
            "bug_pool": bug_pool,
            "stats": {
                "total": base_total,
                "closed": base_closed_count,
                "pending": base_pending_count,
                "ready_rate": base_ready_rate,
            },
            "filtered_stats": {
                "total": filtered_total,
                "closed": filtered_closed_count,
                "pending": filtered_pending_count,
                "ready_rate": filtered_ready_rate,
            },
            "filters": {
                "statuses": status_filter,
                "keyword": norm_keyword,
            },
        }

    def search_options(self, major_version_id: int) -> dict:
        reqs = self.db.query(Requirement).filter(Requirement.major_version_id == major_version_id).all()
        cases = self.db.query(TestCase).join(Requirement).filter(Requirement.major_version_id == major_version_id).all()
        legacy_bugs = self.db.query(BugTracking).filter(BugTracking.major_version_id == major_version_id).all()
        return {
            "reqs": [{"id": r.id, "label": f"{r.zentao_req_id} {r.title}"} for r in reqs],
            "cases": [{"id": c.id, "req_id": c.requirement_id, "label": c.zentao_case_id, "zentao_case_url": c.zentao_case_url} for c in cases],
            "bugs": [{"id": b.id, "req_id": b.requirement_id, "label": b.bug_id} for b in legacy_bugs],
        }

    def submit_result(self, bug_track_id: int, *, minor_version_id: int, test_done: bool, newly_found_bug_id: str | None, resolution: str, current_user: User) -> dict:
        bug = self.db.query(BugTracking).filter(BugTracking.id == bug_track_id).first()
        if not bug:
            raise HTTPException(status_code=404, detail="Bug tracking item not found")

        old_resolution = bug.resolution
        record = self.db.query(BugStage5Record).filter(BugStage5Record.bug_tracking_id == bug_track_id, BugStage5Record.user_id == current_user.id).first()
        if not record:
            record = BugStage5Record(bug_tracking_id=bug_track_id, user_id=current_user.id)
            self.db.add(record)
        record.minor_version_id = minor_version_id
        record.test_done = test_done
        record.newly_found_bug_id = newly_found_bug_id
        record.resolution = resolution
        record.updated_at = local_now()
        self.db.commit()

        all_records = self.db.query(BugStage5Record).filter(BugStage5Record.bug_tracking_id == bug_track_id).all()
        any_closed = any(r.test_done for r in all_records)
        bug.resolution = resolution
        bug.fixed_minor_version_id = minor_version_id if any_closed else None
        bug.closed = any_closed
        bug.closed_by_id = current_user.id if any_closed else None

        notice = None
        if test_done and old_resolution != resolution:
            res_zh_map = {"fixed": "✅修复通过", "false_alarm": "⚠️误报", "rejected": "⛔拒绝修复"}
            if resolution in ["false_alarm", "rejected"] or old_resolution in ["false_alarm", "rejected"]:
                notice = f"📢 **Bug 状态流转通知**\n> 缺陷 **{bug.bug_id}** 的处理状态被 @{current_user.shown_name} 更新为：**{res_zh_map.get(resolution, resolution)}** (位于发包: 🏷️{minor_version_id})"

        created_bug_ids: list[str] = []
        if newly_found_bug_id:
            new_bugs = [b.strip() for b in newly_found_bug_id.split(',') if b.strip()]
            for nb in new_bugs:
                if not self.db.query(BugTracking).filter(BugTracking.bug_id == nb).first():
                    self.db.add(
                        BugTracking(
                            major_version_id=bug.major_version_id,
                            requirement_id=bug.requirement_id,
                            source_type=BugSourceType.LEGACY_BUG,
                            source_ref=bug.bug_id,
                            bug_id=nb,
                            found_minor_version_id=minor_version_id,
                            created_by_id=current_user.id,
                            dispatched_to_id=current_user.id,
                        )
                    )
                    created_bug_ids.append(nb)

        self.db.commit()
        # Audit action strings keep the historical "stage5.*" prefix to preserve
        # activity-log history. Do not rename them without a migration.
        audit(self.db, action="stage5.submit_result", target_type="bug", actor_id=current_user.id, target_id=str(bug.id), detail=f"closed={bug.closed},resolution={resolution},new={','.join(created_bug_ids)}")
        return {"message": "整体测试结果已更新", "notice": notice, "created_bug_ids": created_bug_ids}

    def add_issue(self, *, major_version_id: int, requirement_id: int | None, source_type: BugSourceType, source_ref: str | None, bug_id: str, minor_version_id: int, current_user: User) -> dict:
        if self.db.query(BugTracking).filter(BugTracking.bug_id == bug_id).first():
            raise HTTPException(status_code=400, detail=f"添加失败：Bug 编号 {bug_id} 已经存在！")
        item = BugTracking(
            major_version_id=major_version_id,
            requirement_id=requirement_id,
            source_type=source_type,
            source_ref=source_ref,
            bug_id=bug_id,
            found_minor_version_id=minor_version_id,
            created_by_id=current_user.id,
        )
        self.db.add(item)
        self.db.commit()
        self.db.refresh(item)
        sse_publish(
            "overall_bug_created",
            {
                "id": item.id,
                "bug_id": item.bug_id,
                "source_type": item.source_type.value if hasattr(item.source_type, "value") else str(item.source_type),
                "source_ref": item.source_ref,
                "requirement_id": item.requirement_id,
                "major_version_id": item.major_version_id,
                "minor_version_id": item.found_minor_version_id,
            },
            channels=["global"],
        )
        audit(self.db, action="stage5.add_issue", target_type="bug", actor_id=current_user.id, target_id=str(item.id), detail=item.bug_id)
        return {"id": item.id, "message": "Issue added"}

    def sync_zentao_major_bugs(
        self,
        *,
        major_version_id: int,
        current_user: User,
        force: bool = False,
    ) -> dict:
        major = (
            self.db.query(Version)
            .filter(Version.id == major_version_id, Version.version_type == VersionType.MAJOR)
            .first()
        )
        if not major:
            raise HTTPException(status_code=404, detail="大版本不存在")

        if not force:
            last_sync = _sync_cache.get(major_version_id)
            if last_sync and (local_now() - last_sync) < _SYNC_CACHE_TTL:
                return {
                    "major_version_id": major.id,
                    "major_version_no": major.version_no,
                    "execution_id": None,
                    "remote_total": 0,
                    "created": 0,
                    "updated": 0,
                    "matched_minor": 0,
                    "cached": True,
                }

        ctx = self._get_zentao_client_ctx(current_user.id)
        if not ctx:
            raise HTTPException(status_code=400, detail="当前用户未配置可用的禅道绑定")
        client, base_url = ctx

        try:
            execution_id, remote_bugs = self._resolve_and_fetch_remote_bugs_with_retry(
                client=client,
                major=major,
                user_id=current_user.id,
            )
        except ZentaoAPIError as exc:
            raise HTTPException(status_code=502, detail=f"禅道同步失败: {exc.message}") from exc
        if not execution_id:
            raise HTTPException(status_code=400, detail="当前大版本未识别到对应的禅道执行版本，请先同步版本或检查版本命名")
        if not remote_bugs:
            _sync_cache[major_version_id] = local_now()
            return {
                "major_version_id": major.id,
                "major_version_no": major.version_no,
                "execution_id": execution_id,
                "remote_total": 0,
                "created": 0,
                "updated": 0,
                "matched_minor": 0,
                "cached": False,
            }

        zentao_ids = [n["zentao_bug_id"] for n in
                      [self._normalize_zentao_bug_summary(r, base_url) for r in remote_bugs] if n]
        bug_ids = [f"b#{zid}" for zid in zentao_ids if zid]
        existing_by_zentao_id: dict[str, BugTracking] = {}
        existing_by_bug_id: dict[str, BugTracking] = {}
        if zentao_ids or bug_ids:
            existing_rows = (
                self.db.query(BugTracking)
                .filter(
                    (BugTracking.zentao_bug_id.in_(zentao_ids)) |
                    (BugTracking.bug_id.in_(bug_ids))
                )
                .all()
            )
            for row in existing_rows:
                if row.zentao_bug_id:
                    existing_by_zentao_id[row.zentao_bug_id] = row
                existing_by_bug_id[row.bug_id] = row

        created = 0
        updated = 0
        matched_minor = 0
        bugs_to_auto_close: list[tuple[BugTracking, dict]] = []

        for raw in remote_bugs:
            normalized = self._normalize_zentao_bug_summary(raw, base_url)
            if not normalized:
                continue
            bug_row, created_now, updated_now = self._upsert_zentao_bug_summary(
                major=major,
                normalized=normalized,
                actor=current_user,
                execution_id=execution_id,
                existing_by_zentao_id=existing_by_zentao_id,
                existing_by_bug_id=existing_by_bug_id,
            )
            created += int(created_now)
            updated += int(updated_now)
            if bug_row.found_minor_version_id:
                matched_minor += 1

            if normalized.get("status") == "closed" and normalized.get("closed_by_account"):
                bugs_to_auto_close.append((bug_row, normalized))

        if bugs_to_auto_close:
            self.db.flush()
            for bug_row, normalized in bugs_to_auto_close:
                self._auto_create_zentao_close_record(bug_row, normalized)

        self.db.commit()
        _sync_cache[major_version_id] = local_now()
        return {
            "major_version_id": major.id,
            "major_version_no": major.version_no,
            "execution_id": execution_id,
            "remote_total": len(remote_bugs),
            "created": created,
            "updated": updated,
            "matched_minor": matched_minor,
            "cached": False,
        }

    def sync_all_zentao_bugs_by_software(
        self,
        *,
        software_id: int,
        current_user: User,
        force: bool = False,
    ) -> dict:
        """
        Pull **all** bugs (including closed/deleted) from every Zentao product
        mapped to the given software, and upsert them into the local DB.

        Design notes:
        - Uses /products/{id}/bugs?status=all which returns the real total
          (~5000 on Survey Master) instead of the 186 that the execution/build
          walks would reveal with default filters.
        - Resolves each bug's local `major_version_id` independently so the
          caller does not need to enumerate majors first.
        - Bugs that cannot be mapped to any local major are parked under a
          per-software "unclassified" major so they are never lost.
        - Per-software in-flight lock prevents accidental double-click from
          running two concurrent pulls against Zentao.
        """
        from app.models import SoftwareProduct

        software = self.db.query(SoftwareProduct).filter(SoftwareProduct.id == software_id).first()
        if not software:
            raise HTTPException(status_code=404, detail="软件产品不存在")

        if not force:
            last = _sync_all_cache.get(software_id)
            if last and (local_now() - last) < _SYNC_ALL_CACHE_TTL:
                return {
                    "software_id": software_id,
                    "software_name": software.name,
                    "zentao_products": [],
                    "remote_total": 0,
                    "created": 0,
                    "updated": 0,
                    "unclassified": 0,
                    "matched_minor": 0,
                    "elapsed_seconds": 0.0,
                    "cached": True,
                }

        if software_id in _sync_all_in_progress:
            raise HTTPException(status_code=409, detail="该软件正在同步禅道全量 Bug，请稍后再试")

        _sync_all_in_progress.add(software_id)
        started = local_now()
        try:
            ctx = self._get_zentao_client_ctx(current_user.id)
            if not ctx:
                raise HTTPException(status_code=400, detail="当前用户未配置可用的禅道绑定")
            client, base_url = ctx

            product_ids = self._collect_zentao_product_ids_for_software(software_id, software)
            if not product_ids:
                raise HTTPException(
                    status_code=400,
                    detail="该软件下未找到禅道产品映射，请先在软件/版本配置中补充 zentao_product_id",
                )

            # Fetch bugs from every product (parallel).
            remote_bugs_by_product: dict[int, list[dict]] = {}
            with ThreadPoolExecutor(max_workers=_PRODUCT_FETCH_CONCURRENCY) as executor:
                future_to_pid = {
                    executor.submit(self._fetch_product_bugs_with_retry, client, pid, current_user.id): pid
                    for pid in product_ids
                }
                for future in as_completed(future_to_pid):
                    pid = future_to_pid[future]
                    try:
                        remote_bugs_by_product[pid] = future.result() or []
                    except Exception as exc:
                        logger.warning("sync_all_zentao_bugs: product %s failed: %s", pid, exc)
                        remote_bugs_by_product[pid] = []

            total_raw = sum(len(v) for v in remote_bugs_by_product.values())

            # Preload existing BugTracking rows for efficient upsert lookup.
            all_zentao_ids: list[str] = []
            normalized_by_pid: dict[int, list[dict]] = {}
            for pid, raw_list in remote_bugs_by_product.items():
                nlist: list[dict] = []
                for raw in raw_list:
                    nrow = self._normalize_zentao_bug_summary(raw, base_url)
                    if nrow:
                        nlist.append(nrow)
                        all_zentao_ids.append(nrow["zentao_bug_id"])
                normalized_by_pid[pid] = nlist

            existing_by_zentao_id: dict[str, BugTracking] = {}
            if all_zentao_ids:
                for row in (
                    self.db.query(BugTracking)
                    .filter(BugTracking.zentao_bug_id.in_(all_zentao_ids))
                    .all()
                ):
                    if row.zentao_bug_id:
                        existing_by_zentao_id[row.zentao_bug_id] = row

            unclassified_major = self._get_or_create_unclassified_major(software_id)

            created = 0
            updated = 0
            unclassified = 0
            matched_minor = 0
            bugs_to_auto_close: list[tuple[BugTracking, dict]] = []

            batch_commit_every = 500
            batch_count = 0

            for pid, nlist in normalized_by_pid.items():
                for normalized in nlist:
                    major_id, minor_id = self._resolve_major_minor_for_bug(
                        normalized, unclassified_major
                    )
                    is_unclassified = major_id == unclassified_major.id
                    if is_unclassified:
                        unclassified += 1
                    if minor_id:
                        matched_minor += 1

                    bug_row, created_now, updated_now = self._upsert_bug_by_product(
                        normalized=normalized,
                        major_version_id=major_id,
                        minor_version_id=minor_id,
                        fallback_actor=current_user,
                        existing_by_zentao_id=existing_by_zentao_id,
                    )
                    created += int(created_now)
                    updated += int(updated_now)

                    if normalized.get("status") == "closed" and normalized.get("closed_by_account"):
                        bugs_to_auto_close.append((bug_row, normalized))

                    batch_count += 1
                    if batch_count >= batch_commit_every:
                        self.db.commit()
                        batch_count = 0

            if batch_count:
                self.db.commit()

            if bugs_to_auto_close:
                self.db.flush()
                for bug_row, normalized in bugs_to_auto_close:
                    self._auto_create_zentao_close_record(bug_row, normalized)
                self.db.commit()

            _sync_all_cache[software_id] = local_now()
            elapsed = (local_now() - started).total_seconds()
            return {
                "software_id": software_id,
                "software_name": software.name,
                "zentao_products": product_ids,
                "remote_total": total_raw,
                "created": created,
                "updated": updated,
                "unclassified": unclassified,
                "matched_minor": matched_minor,
                "elapsed_seconds": round(elapsed, 1),
                "cached": False,
            }
        finally:
            _sync_all_in_progress.discard(software_id)

    def _collect_zentao_product_ids_for_software(
        self, software_id: int, software
    ) -> list[int]:
        """
        Collect every zentao_product_id that might belong to this software.

        Sources, in priority order:
        1. SoftwareProduct.zentao_product_id
        2. Distinct zentao_product_id already on existing BugTracking rows
           whose major_version_id belongs to this software (historical data)
        3. Distinct zentao_product_id on Version rows in this software
        """
        ids: list[int] = []
        seen: set[int] = set()

        def _push(v):
            try:
                x = int(v)
            except (TypeError, ValueError):
                return
            if x and x not in seen:
                seen.add(x)
                ids.append(x)

        if software and software.zentao_product_id:
            _push(software.zentao_product_id)

        # From BugTracking
        major_id_rows = self.db.query(Version.id).filter(
            Version.version_type == VersionType.MAJOR, Version.software_id == software_id
        ).all()
        major_ids = [r[0] for r in major_id_rows]
        if major_ids:
            bug_prod_rows = (
                self.db.query(BugTracking.zentao_product_id)
                .filter(
                    BugTracking.major_version_id.in_(major_ids),
                    BugTracking.zentao_product_id.isnot(None),
                )
                .distinct()
                .all()
            )
            for (pid,) in bug_prod_rows:
                _push(pid)

        return ids

    def _fetch_product_bugs_with_retry(
        self, client: ZentaoClient, product_id: int, user_id: int
    ) -> list[dict]:
        try:
            return self._fetch_bug_collection(
                client, f"products/{product_id}/bugs", limit=500, max_pages=50
            )
        except ZentaoAPIError as exc:
            if exc.status_code != 401:
                raise
        invalidate_token(user_id, self.db)
        refreshed_ctx = self._get_zentao_client_ctx(user_id)
        if not refreshed_ctx:
            raise HTTPException(status_code=400, detail="禅道 token 刷新失败")
        refreshed_client, _ = refreshed_ctx
        return self._fetch_bug_collection(
            refreshed_client, f"products/{product_id}/bugs", limit=500, max_pages=50
        )

    def _resolve_major_minor_for_bug(
        self, normalized: dict, unclassified_major: Version
    ) -> tuple[int, int | None]:
        """
        Resolve local (major_version_id, minor_version_id) for a Zentao bug
        using, in order:
        1. openedBuild → Version.zentao_build_id (MINOR) → parent_id (MAJOR)
        2. execution → Version.zentao_execution_id (MAJOR)
        3. affectedVersion → Version.version_no fuzzy match
        4. unclassified fallback major for this software
        """
        build_ids = normalized.get("opened_build_ids") or []
        for bid in build_ids:
            minor = (
                self.db.query(Version)
                .filter(
                    Version.version_type == VersionType.MINOR,
                    Version.zentao_build_id == int(bid),
                )
                .first()
            )
            if minor and minor.parent_id:
                major = self.db.query(Version).filter(Version.id == minor.parent_id).first()
                if major:
                    return major.id, minor.id

        exec_ref = normalized.get("execution_ref_id")
        if exec_ref:
            try:
                exec_int = int(exec_ref)
            except (TypeError, ValueError):
                exec_int = None
            if exec_int:
                major = (
                    self.db.query(Version)
                    .filter(
                        Version.version_type == VersionType.MAJOR,
                        Version.zentao_execution_id == exec_int,
                    )
                    .first()
                )
                if major:
                    return major.id, None

        affected = normalized.get("affected_version")
        if affected:
            # Try exact match first
            candidate = (
                self.db.query(Version)
                .filter(
                    Version.version_type == VersionType.MAJOR,
                    Version.software_id == unclassified_major.software_id,
                    Version.version_no == affected,
                )
                .first()
            )
            if candidate:
                return candidate.id, None

        return unclassified_major.id, None

    def _get_or_create_unclassified_major(self, software_id: int) -> Version:
        """
        Per-software bucket for bugs that cannot be mapped to any real major.
        Keeps the bug visible in the全部版本 view instead of silently dropping.
        """
        from app.db.seed import UNCLASSIFIED_MAJOR_VERSION_NO

        row = (
            self.db.query(Version)
            .filter(
                Version.software_id == software_id,
                Version.version_type == VersionType.MAJOR,
                Version.version_no == UNCLASSIFIED_MAJOR_VERSION_NO,
            )
            .first()
        )
        if row:
            return row
        row = Version(
            version_no=UNCLASSIFIED_MAJOR_VERSION_NO,
            version_type=VersionType.MAJOR,
            software_id=software_id,
            parent_id=None,
        )
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        return row

    def _upsert_bug_by_product(
        self,
        *,
        normalized: dict,
        major_version_id: int,
        minor_version_id: int | None,
        fallback_actor: User,
        existing_by_zentao_id: dict[str, BugTracking],
    ) -> tuple[BugTracking, bool, bool]:
        bug_row = existing_by_zentao_id.get(normalized["zentao_bug_id"])
        if bug_row is None:
            # Also check by bug_id (b#<id>) to absorb any historical duplicate
            bug_row = (
                self.db.query(BugTracking)
                .filter(BugTracking.bug_id == normalized["bug_id"])
                .first()
            )

        resolved_actor = self._resolve_actor_from_opened_by(
            normalized.get("opened_by_account"), fallback_actor
        )

        created = bug_row is None
        if bug_row is None:
            bug_row = BugTracking(
                major_version_id=major_version_id,
                requirement_id=None,
                source_type=BugSourceType.MANUAL,
                source_ref=None,
                bug_id=normalized["bug_id"],
                found_minor_version_id=minor_version_id,
                created_by_id=resolved_actor.id,
            )
            self.db.add(bug_row)
            existing_by_zentao_id[normalized["zentao_bug_id"]] = bug_row
        else:
            bug_row.major_version_id = major_version_id
            if minor_version_id and not bug_row.found_minor_version_id:
                bug_row.found_minor_version_id = minor_version_id
            if bug_row.requirement_id is None:
                bug_row.source_type = BugSourceType.MANUAL
                bug_row.zentao_display_bucket = "overall"

        updated = self._write_bug_fields_from_normalized(
            bug_row,
            normalized,
            execution_id=None,
            execution_name=None,
            sync_message="通过禅道全量同步",
            touched_if_existing=not created,
        )
        return bug_row, created, updated and not created

    def _resolve_and_fetch_remote_bugs_with_retry(
        self,
        *,
        client: ZentaoClient,
        major: Version,
        user_id: int,
    ) -> tuple[int | None, list[dict]]:
        try:
            execution_id = self._resolve_execution_id_for_major(client, major)
            if not execution_id:
                return None, []
            return execution_id, self._fetch_zentao_bugs_for_major(client, major, execution_id)
        except ZentaoAPIError as exc:
            if exc.status_code != 401:
                raise

        invalidate_token(user_id, self.db)
        refreshed_ctx = self._get_zentao_client_ctx(user_id)
        if not refreshed_ctx:
            raise HTTPException(status_code=400, detail="禅道 token 刷新失败，请重新绑定或稍后再试")
        refreshed_client, _ = refreshed_ctx
        execution_id = self._resolve_execution_id_for_major(refreshed_client, major)
        if not execution_id:
            return None, []
        return execution_id, self._fetch_zentao_bugs_for_major(refreshed_client, major, execution_id)

    def build_overall_test_push_message(self, major_version_id: int, minor_version_id: int) -> tuple[str, int]:
        bugs = self.db.query(BugTracking).filter(BugTracking.major_version_id == major_version_id).all()
        remaining = len([b for b in bugs if not (b.closed and b.fixed_minor_version_id == minor_version_id)])
        return f"整体测试进度推送：剩余未闭环 {remaining}", remaining

    # Legacy method name for backward compatibility with existing callers
    build_stage5_push_message = build_overall_test_push_message

    def _get_zentao_client_ctx(self, user_id: int) -> tuple[ZentaoClient, str] | None:
        binding = (
            self.db.query(UserZentaoBinding)
            .filter(UserZentaoBinding.user_id == user_id)
            .first()
        )
        if not binding or not binding.base_url:
            return None
        token = get_valid_token(user_id, self.db)
        if not token:
            return None
        base_url = binding.base_url.rstrip("/")
        return ZentaoClient(base_url=base_url, token=token), base_url

    def _resolve_execution_id_for_major(self, client: ZentaoClient, major: Version) -> int | None:
        if major.zentao_execution_id:
            return int(major.zentao_execution_id)

        candidate_names = {
            (major.version_no or "").strip(),
            (major.zentao_execution_name_cache or "").strip(),
            self._major_version_to_execution_token(major.version_no),
        }
        candidate_names = {name for name in candidate_names if name}
        if not candidate_names:
            return None

        project_ids: list[int] = []
        if major.zentao_project_id:
            project_ids.append(int(major.zentao_project_id))
        else:
            projects_data = client.get("projects", params={"limit": 100}) or {}
            project_ids.extend(int(p["id"]) for p in projects_data.get("projects", []) if p.get("id"))

        for project_id in project_ids:
            data = client.get(f"projects/{project_id}/executions", params={"limit": 200}) or {}
            for exc_row in data.get("executions", []):
                exec_id = exc_row.get("id")
                exec_name = str(exc_row.get("name") or "").strip()
                if not exec_id or not exec_name:
                    continue
                human_name = self._execution_name_to_major_version(exec_name)
                if exec_name in candidate_names or human_name in candidate_names:
                    major.zentao_project_id = project_id
                    major.zentao_execution_id = int(exec_id)
                    major.zentao_execution_name_cache = exec_name
                    self.db.commit()
                    return int(exec_id)
        return None

    def _fetch_zentao_bugs_for_major(self, client: ZentaoClient, major: Version, execution_id: int) -> list[dict]:
        rows: list[dict] = []
        seen_ids: set[str] = set()

        self._extend_unique_bug_rows(
            rows,
            seen_ids,
            self._fetch_bug_collection(client, f"executions/{execution_id}/bugs"),
        )

        build_ids: list[int] = []
        remote_builds = client.get(f"executions/{execution_id}/builds", params={"limit": 200}) or {}
        for build in self._extract_build_rows(remote_builds):
            build_id = build.get("id")
            if build_id:
                build_ids.append(int(build_id))

        local_build_ids = (
            self.db.query(Version.zentao_build_id)
            .filter(
                Version.parent_id == major.id,
                Version.version_type == VersionType.MINOR,
                Version.zentao_build_id.isnot(None),
            )
            .all()
        )
        for (build_id,) in local_build_ids:
            if build_id:
                build_ids.append(int(build_id))

        unique_build_ids: list[int] = list(dict.fromkeys(int(b) for b in build_ids if b))

        build_bugs_map: dict[int, list[dict]] = {}
        with ThreadPoolExecutor(max_workers=_BUILD_FETCH_CONCURRENCY) as executor:
            future_to_build = {
                executor.submit(self._fetch_bug_collection, client, f"builds/{build_id}/bugs"): build_id
                for build_id in unique_build_ids
            }
            for future in as_completed(future_to_build):
                build_id = future_to_build[future]
                try:
                    build_bugs_map[build_id] = future.result()
                except Exception as exc:
                    logger.warning("_fetch_zentao_bugs_for_major: build_id=%s err=%s", build_id, exc)
                    build_bugs_map[build_id] = []

        for build_id in unique_build_ids:
            self._extend_unique_bug_rows(rows, seen_ids, build_bugs_map.get(build_id, []))

        return rows

    def _fetch_bug_collection(self, client: ZentaoClient, path: str, *, extra_params: dict | None = None, limit: int = 100, max_pages: int = 50) -> list[dict]:
        """
        Fetch a paginated bug collection from Zentao.

        IMPORTANT: `status=all` is passed by default so closed bugs are
        included. Without it Zentao's product/execution/build bug endpoints
        return only active+resolved rows, which was a silent defect in the
        previous sync path — closed bugs would never make it into the local
        DB on first sync, only the ones that were later re-activated.
        """
        rows: list[dict] = []
        seen_ids: set[str] = set()

        base_params: dict[str, object] = {"status": "all"}
        if extra_params:
            base_params.update(extra_params)

        for page in range(1, max_pages + 1):
            params = dict(base_params)
            params["limit"] = limit
            params["page"] = page
            data = client.get(path, params=params)
            page_rows = self._extract_bug_rows(data)
            if not page_rows:
                break

            new_count = 0
            for row in page_rows:
                bug_id = str((row or {}).get("id") or "")
                if bug_id and bug_id not in seen_ids:
                    seen_ids.add(bug_id)
                    rows.append(row)
                    new_count += 1
                elif not bug_id:
                    rows.append(row)
                    new_count += 1

            if new_count == 0 or len(page_rows) < limit:
                break

        return rows

    def _extract_bug_rows(self, data: object) -> list[dict]:
        if data is None:
            return []
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        if not isinstance(data, dict):
            return []

        direct = data.get("bugs")
        if isinstance(direct, list):
            return [item for item in direct if isinstance(item, dict)]
        if isinstance(direct, dict):
            return [item for item in direct.values() if isinstance(item, dict)]

        nested = data.get("data")
        if isinstance(nested, dict):
            return self._extract_bug_rows(nested)
        if isinstance(nested, list):
            return [item for item in nested if isinstance(item, dict)]

        items = data.get("items")
        if isinstance(items, list):
            return [item for item in items if isinstance(item, dict)]

        rows = data.get("rows")
        if isinstance(rows, list):
            return [item for item in rows if isinstance(item, dict)]

        if data.get("id") and any(key in data for key in ("title", "status", "openedBuild", "openedBuilds")):
            return [data]

        return []

    def _extract_build_rows(self, data: object) -> list[dict]:
        if data is None:
            return []
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        if not isinstance(data, dict):
            return []

        direct = data.get("builds")
        if isinstance(direct, list):
            return [item for item in direct if isinstance(item, dict)]
        if isinstance(direct, dict):
            return [item for item in direct.values() if isinstance(item, dict)]

        nested = data.get("data")
        if isinstance(nested, dict):
            return self._extract_build_rows(nested)
        if isinstance(nested, list):
            return [item for item in nested if isinstance(item, dict)]

        if data.get("id") and any(key in data for key in ("name", "date", "builder")):
            return [data]

        return []

    def _normalize_zentao_bug_summary(self, raw: dict, base_url: str) -> dict | None:
        bug = raw.get("bug") if isinstance(raw.get("bug"), dict) else raw
        if not isinstance(bug, dict):
            return None
        bug_id = bug.get("id")
        if not bug_id:
            return None
        bug_id_str = str(bug_id)
        opened_build_ids = self._extract_build_ids(
            bug.get("openedBuilds") or bug.get("openedBuild") or bug.get("foundBuild")
        )
        closed_by_raw = bug.get("closedBy") or {}
        if isinstance(closed_by_raw, dict):
            closed_by_account = str(closed_by_raw.get("account") or "").strip()
            closed_by_name = str(closed_by_raw.get("realname") or closed_by_raw.get("account") or "").strip()
        else:
            closed_by_account = str(closed_by_raw).strip()
            closed_by_name = closed_by_account

        assigned_raw = bug.get("assignedTo") or {}
        if isinstance(assigned_raw, dict):
            assigned_account = str(assigned_raw.get("account") or "").strip()
            assigned_name = str(assigned_raw.get("realname") or assigned_raw.get("account") or "").strip()
        else:
            assigned_account = str(assigned_raw).strip()
            assigned_name = assigned_account

        close_date_raw = bug.get("closedDate") or ""
        close_date = self._parse_zentao_datetime(close_date_raw)

        opened_date_raw = bug.get("openedDate") or ""
        opened_at = self._parse_zentao_datetime(opened_date_raw)
        opened_by_raw = bug.get("openedBy") or {}
        if isinstance(opened_by_raw, dict):
            opened_by_account = str(opened_by_raw.get("account") or "").strip()
            opened_by_name = str(opened_by_raw.get("realname") or opened_by_raw.get("account") or "").strip()
        else:
            opened_by_account = str(opened_by_raw).strip()
            opened_by_name = opened_by_account

        remote_updated_raw = bug.get("lastEditedDate") or bug.get("editedDate") or ""
        remote_updated_at = self._parse_zentao_datetime(remote_updated_raw)
        status = str(bug.get("status") or "").strip().lower()
        assigned_account_lower = assigned_account.lower()
        if (
            status in {"delay", "resolved"}
            and (
                close_date is not None
                or bool(closed_by_account)
                or assigned_account_lower == "closed"
            )
        ):
            status = "closed"

        deleted_raw = bug.get("deleted")
        deleted_flag = False
        if isinstance(deleted_raw, bool):
            deleted_flag = deleted_raw
        elif deleted_raw is not None:
            deleted_flag = str(deleted_raw).strip() in {"1", "true", "True", "yes"}

        # Extract referenced product / project / execution / story so the
        # per-product full sync path can map bugs without prior context.
        product_id: str | None = None
        prod_raw = bug.get("product")
        if isinstance(prod_raw, dict):
            product_id = _coerce_str_id(prod_raw.get("id"))
        else:
            product_id = _coerce_str_id(prod_raw)
        execution_ref_id: str | None = None
        exec_raw = bug.get("execution")
        if isinstance(exec_raw, dict):
            execution_ref_id = _coerce_str_id(exec_raw.get("id"))
        else:
            execution_ref_id = _coerce_str_id(exec_raw)
        project_ref_id: str | None = None
        proj_raw = bug.get("project")
        if isinstance(proj_raw, dict):
            project_ref_id = _coerce_str_id(proj_raw.get("id"))
        else:
            project_ref_id = _coerce_str_id(proj_raw)
        story_ref_id: int | None = None
        story_raw = bug.get("story")
        if isinstance(story_raw, dict):
            try:
                story_ref_id = int(story_raw.get("id")) if story_raw.get("id") else None
            except (TypeError, ValueError):
                story_ref_id = None
        else:
            try:
                story_ref_id = int(story_raw) if story_raw else None
            except (TypeError, ValueError):
                story_ref_id = None

        return {
            "zentao_bug_id": bug_id_str,
            "bug_id": f"b#{bug_id_str}",
            "title": str(bug.get("title") or "").strip(),
            "status": status,
            "opened_build_ids": opened_build_ids,
            "url": f"{base_url}/bug-view-{bug_id_str}.html",
            "closed_by_account": closed_by_account,
            "closed_by_name": closed_by_name,
            "close_date": close_date,
            "close_comment": str(bug.get("comment") or "").strip(),
            "assigned_to_account": assigned_account,
            "assigned_to_name": assigned_name,
            "remote_updated_at": remote_updated_at,
            "opened_at": opened_at,
            "opened_by_account": opened_by_account,
            "opened_by_name": opened_by_name,
            "deleted": deleted_flag,
            "product_id": product_id,
            "execution_ref_id": execution_ref_id,
            "project_ref_id": project_ref_id,
            "story_ref_id": story_ref_id,
            "affected_version": str(bug.get("v1") or bug.get("v2") or "").strip() or None,
        }

    @staticmethod
    def _parse_zentao_datetime(raw: str | None) -> datetime | None:
        """Parse Zentao datetime and normalize timezone-aware values to local naive time."""
        return parse_external_datetime_to_local_naive(raw)

    def _resolve_actor_from_opened_by(self, opened_by_account: str | None, fallback_actor: User | None) -> User:
        """
        Map a Zentao `openedBy.account` to a local User via UserZentaoBinding.
        If no binding exists, fall back to the `zentao_sync_bot` system user
        rather than to `fallback_actor` (e.g. the clicking user) — that keeps
        admin's personal stats from being polluted by thousands of historical
        bugs during a bulk sync.
        """
        account = (opened_by_account or "").strip()
        if account:
            binding = (
                self.db.query(UserZentaoBinding)
                .filter(UserZentaoBinding.zentao_account == account)
                .first()
            )
            if binding:
                user = self.db.query(User).filter(User.id == binding.user_id).first()
                if user:
                    return user
        # Fallback to the dedicated bot user, NOT to the current actor
        from app.db.seed import ZENTAO_SYNC_BOT_USERNAME
        bot = self.db.query(User).filter(User.username == ZENTAO_SYNC_BOT_USERNAME).first()
        if bot:
            return bot
        if fallback_actor:
            return fallback_actor
        # Absolute last-resort: any user (defensive, should never hit in prod)
        any_user = self.db.query(User).order_by(User.id.asc()).first()
        if any_user:
            return any_user
        raise HTTPException(status_code=500, detail="系统中不存在可用的用户账号")

    def _resolve_dispatched_to_id(self, assigned_to_account: str | None) -> int | None:
        account = (assigned_to_account or "").strip()
        if not account or account.lower() == "closed":
            return None
        binding = (
            self.db.query(UserZentaoBinding)
            .filter(UserZentaoBinding.zentao_account == account)
            .first()
        )
        return binding.user_id if binding else None

    def _upsert_zentao_bug_summary(
        self,
        *,
        major: Version,
        normalized: dict,
        actor: User,
        execution_id: int,
        existing_by_zentao_id: dict[str, BugTracking] | None = None,
        existing_by_bug_id: dict[str, BugTracking] | None = None,
    ) -> tuple[BugTracking, bool, bool]:
        """
        Upsert a bug within a known major version's execution context.

        Used by the single-major-version sync path. Also routes through the
        common writer `_write_bug_fields_from_normalized` so the new
        `zentao_opened_at / zentao_opened_by_*` fields are populated.
        """
        bug_row: BugTracking | None = None
        if existing_by_zentao_id is not None:
            bug_row = existing_by_zentao_id.get(normalized["zentao_bug_id"])
        if bug_row is None and existing_by_bug_id is not None:
            bug_row = existing_by_bug_id.get(normalized["bug_id"])
        if bug_row is None and existing_by_zentao_id is None:
            bug_row = (
                self.db.query(BugTracking)
                .filter(
                    (BugTracking.zentao_bug_id == normalized["zentao_bug_id"])
                    | (BugTracking.bug_id == normalized["bug_id"])
                )
                .first()
            )

        matched_minor_id = self._match_minor_by_build_ids(major.id, normalized.get("opened_build_ids") or [])
        # Pin the actor to the real Zentao opener when possible
        resolved_actor = self._resolve_actor_from_opened_by(normalized.get("opened_by_account"), actor)

        created = bug_row is None
        if bug_row is None:
            bug_row = BugTracking(
                major_version_id=major.id,
                requirement_id=None,
                source_type=BugSourceType.MANUAL,
                source_ref=None,
                bug_id=normalized["bug_id"],
                found_minor_version_id=matched_minor_id,
                created_by_id=resolved_actor.id,
            )
            self.db.add(bug_row)
        else:
            bug_row.major_version_id = major.id
            if matched_minor_id:
                bug_row.found_minor_version_id = matched_minor_id
            bug_row.bug_id = normalized["bug_id"]
            if bug_row.requirement_id is None:
                bug_row.source_type = BugSourceType.MANUAL
                bug_row.zentao_display_bucket = "overall"

        updated = self._write_bug_fields_from_normalized(
            bug_row,
            normalized,
            execution_id=execution_id,
            execution_name=major.zentao_execution_name_cache or major.version_no,
            sync_message="通过整体测试版本全量同步",
            touched_if_existing=not created,
        )

        return bug_row, created, updated and not created

    def _write_bug_fields_from_normalized(
        self,
        bug_row: BugTracking,
        normalized: dict,
        *,
        execution_id: int | None,
        execution_name: str | None,
        sync_message: str,
        touched_if_existing: bool,
    ) -> bool:
        """
        Apply normalized bug fields onto a BugTracking row. Returns True if
        any meaningful field changed (only for existing rows).
        """
        original_snapshot = None
        if touched_if_existing:
            original_snapshot = (
                bug_row.zentao_bug_title,
                bug_row.zentao_bug_url,
                bug_row.zentao_bug_id,
                bug_row.zentao_opened_build_ids,
                bug_row.zentao_execution_id,
                bug_row.zentao_live_status,
                bug_row.zentao_deleted,
                bug_row.zentao_opened_at,
                bug_row.zentao_assigned_to_account,
            )

        bug_row.zentao_bug_title = normalized["title"] or bug_row.zentao_bug_title
        bug_row.zentao_bug_url = normalized["url"] or bug_row.zentao_bug_url
        bug_row.zentao_bug_id = normalized["zentao_bug_id"]
        bug_row.zentao_opened_build_ids = json.dumps(normalized.get("opened_build_ids") or [], ensure_ascii=False)
        if execution_id is not None:
            bug_row.zentao_execution_id = str(execution_id)
        elif normalized.get("execution_ref_id"):
            bug_row.zentao_execution_id = normalized["execution_ref_id"]
        if execution_name:
            bug_row.zentao_execution_name = execution_name
        if normalized.get("product_id"):
            bug_row.zentao_product_id = normalized["product_id"]
        if normalized.get("project_ref_id"):
            bug_row.zentao_project_id = normalized["project_ref_id"]
        if normalized.get("affected_version"):
            bug_row.zentao_affected_version = normalized["affected_version"]
        if normalized.get("story_ref_id"):
            bug_row.zentao_story_id = normalized["story_ref_id"]
        bug_row.zentao_sync_status = "synced"
        bug_row.zentao_sync_message = sync_message
        bug_row.last_zentao_synced_at = local_now()
        if bug_row.zentao_display_bucket is None:
            bug_row.zentao_display_bucket = "overall"

        if normalized.get("status"):
            bug_row.zentao_live_status = normalized["status"]

        # Remote opened timestamp + opener — P0/P1 fix: time statistics and
        # creator attribution now come from real Zentao data.
        if normalized.get("opened_at"):
            bug_row.zentao_opened_at = normalized["opened_at"]
        if normalized.get("opened_by_account"):
            bug_row.zentao_opened_by_account = normalized["opened_by_account"]
            bug_row.zentao_opened_by_name = normalized.get("opened_by_name") or normalized["opened_by_account"]

        # Deleted flag so reports can exclude tombstones
        bug_row.zentao_deleted = bool(normalized.get("deleted"))

        # Closed / resolved metadata
        if normalized.get("status") == "closed":
            bug_row.zentao_closed_by_account = normalized.get("closed_by_account") or None
            bug_row.zentao_closed_by_name = normalized.get("closed_by_name") or normalized.get("closed_by_account") or ""
            bug_row.zentao_close_date = normalized.get("close_date")
            bug_row.zentao_close_comment = normalized.get("close_comment") or ""
            # Keep `closed` flag aligned with remote status. Stage5 local
            # verification (`verified`) is a separate concept, handled in
            # submit_result / report layer.
            bug_row.closed = True
        else:
            bug_row.zentao_closed_by_account = None
            bug_row.zentao_closed_by_name = ""
            bug_row.zentao_close_date = None
            bug_row.zentao_close_comment = ""

        # Assigned-to: cache name + map to a local dispatched_to_id so the
        # governance "unassigned" KPI is not a false positive any more.
        if normalized.get("assigned_to_account"):
            bug_row.zentao_assigned_to_account = normalized["assigned_to_account"]
            bug_row.zentao_assigned_to_name = normalized["assigned_to_name"] or normalized["assigned_to_account"]
            mapped_uid = self._resolve_dispatched_to_id(normalized["assigned_to_account"])
            if mapped_uid:
                bug_row.dispatched_to_id = mapped_uid
        if normalized.get("remote_updated_at"):
            bug_row.zentao_remote_updated_at = normalized["remote_updated_at"]

        if original_snapshot is None:
            return False
        new_snapshot = (
            bug_row.zentao_bug_title,
            bug_row.zentao_bug_url,
            bug_row.zentao_bug_id,
            bug_row.zentao_opened_build_ids,
            bug_row.zentao_execution_id,
            bug_row.zentao_live_status,
            bug_row.zentao_deleted,
            bug_row.zentao_opened_at,
            bug_row.zentao_assigned_to_account,
        )
        return original_snapshot != new_snapshot

    def _auto_create_zentao_close_record(self, bug_row: BugTracking, normalized: dict) -> None:
        """
        Auto-create / update a BugStage5Record for the Zentao closer.

        Requires bug_row.id to be non-None (flush the session before calling).
        Only runs when the bug has a `closed_by_account` and a matching
        UserZentaoBinding entry exists.
        """
        closed_by_account = (normalized.get("closed_by_account") or "").strip()
        if not closed_by_account or not bug_row.id:
            return

        binding = (
            self.db.query(UserZentaoBinding)
            .filter(UserZentaoBinding.zentao_account == closed_by_account)
            .first()
        )
        if not binding:
            return

        user_id = binding.user_id
        close_comment = (normalized.get("close_comment") or "").strip()

        existing_sync = (
            self.db.query(BugStage5Record)
            .filter(
                BugStage5Record.bug_tracking_id == bug_row.id,
                BugStage5Record.user_id == user_id,
                BugStage5Record.source == "zentao_sync",
            )
            .first()
        )
        if existing_sync:
            if existing_sync.comment != close_comment:
                existing_sync.comment = close_comment
                existing_sync.updated_at = local_now()
            return

        manual_record = (
            self.db.query(BugStage5Record)
            .filter(
                BugStage5Record.bug_tracking_id == bug_row.id,
                BugStage5Record.user_id == user_id,
            )
            .first()
        )
        if manual_record:
            if not manual_record.test_done:
                manual_record.test_done = True
                manual_record.source = "zentao_sync"
                manual_record.comment = close_comment
                manual_record.resolution = "fixed"
                manual_record.minor_version_id = manual_record.minor_version_id or bug_row.found_minor_version_id
                manual_record.updated_at = local_now()
            return

        self.db.add(BugStage5Record(
            bug_tracking_id=bug_row.id,
            user_id=user_id,
            minor_version_id=bug_row.found_minor_version_id,
            test_done=True,
            resolution="fixed",
            source="zentao_sync",
            comment=close_comment,
        ))

    def _match_minor_by_build_ids(self, major_id: int, build_ids: list[int]) -> int | None:
        if not build_ids:
            return None
        minor = (
            self.db.query(Version)
            .filter(
                Version.parent_id == major_id,
                Version.version_type == VersionType.MINOR,
                Version.zentao_build_id.in_(build_ids),
            )
            .order_by(Version.id.desc())
            .first()
        )
        return minor.id if minor else None

    def _extract_build_ids(self, raw_value: object) -> list[int]:
        ids: list[int] = []

        def _push(value: object) -> None:
            text = str(value or "").strip()
            if text.isdigit():
                ids.append(int(text))

        if isinstance(raw_value, list):
            for item in raw_value:
                if isinstance(item, dict):
                    _push(item.get("id"))
                else:
                    _push(item)
        elif isinstance(raw_value, dict):
            for key, value in raw_value.items():
                _push(key)
                if isinstance(value, dict):
                    _push(value.get("id"))
        else:
            text = str(raw_value or "").strip()
            if text:
                for part in re.split(r"[,\s]+", text):
                    _push(part)

        seen: set[int] = set()
        deduped: list[int] = []
        for item in ids:
            if item not in seen:
                seen.add(item)
                deduped.append(item)
        return deduped

    @staticmethod
    def _extend_unique_bug_rows(target: list[dict], seen_ids: set[str], rows: list[dict]) -> None:
        for row in rows:
            bug_id = str((row or {}).get("id") or "")
            if bug_id and bug_id in seen_ids:
                continue
            if bug_id:
                seen_ids.add(bug_id)
            target.append(row)

    @staticmethod
    def _major_version_to_execution_token(version_no: str | None) -> str | None:
        text = (version_no or "").strip()
        m = _MAJOR_V_RE.match(text)
        if not m:
            return None
        a, b, c, rest = m.groups()
        tail = rest or ""
        return f"s{a}{b}{c}{tail}"

    def _execution_name_to_major_version(self, execution_name: str | None) -> str | None:
        token = self._extract_pure_execution_token(execution_name)
        if not token:
            return None
        digits = token[1:]
        if len(digits) < 3:
            return None
        tail = digits[3:]
        if tail:
            return f"V{digits[0]}.{digits[1]}.{digits[2]}.{tail}"
        return f"V{digits[0]}.{digits[1]}.{digits[2]}"

    @staticmethod
    def _extract_pure_execution_token(execution_name: str | None) -> str | None:
        text = str(execution_name or "").strip()
        m = re.match(r"^(s\d{4,})", text, re.IGNORECASE)
        return m.group(1).lower() if m else None


# Backward-compatibility alias. Existing callers import Stage5Service from
# app.services.stage5_service; that module re-exports this class.
Stage5Service = OverallTestService
