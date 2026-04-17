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
from app.services.zentao_auth_service import get_valid_token
from app.services.zentao_client_service import ZentaoClient
from app.utils.time_utils import local_now

logger = logging.getLogger(__name__)

_MAJOR_V_RE = re.compile(r"^V(\d+)\.(\d+)\.(\d+)(?:\.(\d+))?$", re.IGNORECASE)

# In-memory sync cache: major_version_id → datetime of last sync
# Avoids redundant full re-syncs when loadStage5 is clicked within TTL
_sync_cache: dict[int, datetime] = {}
_SYNC_CACHE_TTL = timedelta(minutes=5)
_BUILD_FETCH_CONCURRENCY = 6


class Stage5Service:
    def __init__(self, db: Session):
        self.db = db

    def overview(
        self,
        major_version_id: int,
        current_user: User,
        software_id: int | None = None,
    ) -> dict:
        """
        Build the stage5 overview.

        When major_version_id == 0 and software_id is provided, returns
        ALL bugs across every major version for that software ("全部版本"
        mode).  Each bug entry includes a `major_version_no` field so the
        frontend can group / display the version column.

        When major_version_id > 0 the behaviour is unchanged (single
        major-version view).
        """
        all_versions_mode = (major_version_id == 0 and software_id)

        if all_versions_mode:
            # Build a version_id → version_no lookup for the entire software
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
            }
            # In all-versions mode, include the major version label
            if all_versions_mode:
                entry["major_version_no"] = major_id_to_no.get(bug.major_version_id, "")

            bug_pool.append(entry)

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
        audit(self.db, action="stage5.submit_result", target_type="bug", actor_id=current_user.id, target_id=str(bug.id), detail=f"closed={bug.closed},resolution={resolution},new={','.join(created_bug_ids)}")
        return {"message": "Stage5 result updated", "notice": notice, "created_bug_ids": created_bug_ids}

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

        # --- Sync cache check ---
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

        execution_id = self._resolve_execution_id_for_major(client, major)
        if not execution_id:
            raise HTTPException(status_code=400, detail="当前大版本未识别到对应的禅道执行版本，请先同步版本或检查版本命名")

        remote_bugs = self._fetch_zentao_bugs_for_major(client, major, execution_id)
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

        # --- Batch pre-fetch existing BugTracking rows ---
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

            # Collect Zentao-closed bugs for auto-record creation
            if normalized.get("status") == "closed" and normalized.get("closed_by_account"):
                bugs_to_auto_close.append((bug_row, normalized))

        # Flush to ensure IDs are assigned for newly created rows
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

    def build_stage5_push_message(self, major_version_id: int, minor_version_id: int) -> tuple[str, int]:
        bugs = self.db.query(BugTracking).filter(BugTracking.major_version_id == major_version_id).all()
        remaining = len([b for b in bugs if not (b.closed and b.fixed_minor_version_id == minor_version_id)])
        return f"整体测试进度推送：剩余未闭环 {remaining}", remaining

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

        # Deduplicate build IDs
        unique_build_ids: list[int] = list(dict.fromkeys(int(b) for b in build_ids if b))

        # Fetch bugs for all builds concurrently
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

        # Merge in build ID order to keep a consistent, deterministic result
        for build_id in unique_build_ids:
            self._extend_unique_bug_rows(rows, seen_ids, build_bugs_map.get(build_id, []))

        return rows

    def _fetch_bug_collection(self, client: ZentaoClient, path: str) -> list[dict]:
        rows: list[dict] = []
        seen_ids: set[str] = set()
        limit = 100
        max_pages = 20

        for page in range(1, max_pages + 1):
            data = client.get(path, params={"limit": limit, "page": page})
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
        # Extract closer info (closedBy may be dict or string)
        closed_by_raw = bug.get("closedBy") or {}
        if isinstance(closed_by_raw, dict):
            closed_by_account = str(closed_by_raw.get("account") or "").strip()
            closed_by_name = str(closed_by_raw.get("realname") or closed_by_raw.get("account") or "").strip()
        else:
            closed_by_account = str(closed_by_raw).strip()
            closed_by_name = closed_by_account

        # Extract assignee info
        assigned_raw = bug.get("assignedTo") or {}
        if isinstance(assigned_raw, dict):
            assigned_account = str(assigned_raw.get("account") or "").strip()
            assigned_name = str(assigned_raw.get("realname") or assigned_raw.get("account") or "").strip()
        else:
            assigned_account = str(assigned_raw).strip()
            assigned_name = assigned_account

        # Parse close date
        close_date_raw = bug.get("closedDate") or ""
        close_date = self._parse_zentao_datetime(close_date_raw)

        # Parse remote updated-at
        remote_updated_raw = bug.get("lastEditedDate") or bug.get("editedDate") or ""
        remote_updated_at = self._parse_zentao_datetime(remote_updated_raw)

        return {
            "zentao_bug_id": bug_id_str,
            "bug_id": f"b#{bug_id_str}",
            "title": str(bug.get("title") or "").strip(),
            "status": str(bug.get("status") or "").strip(),
            "opened_build_ids": opened_build_ids,
            "url": f"{base_url}/bug-view-{bug_id_str}.html",
            "closed_by_account": closed_by_account,
            "closed_by_name": closed_by_name,
            "close_date": close_date,
            "close_comment": str(bug.get("comment") or "").strip(),
            "assigned_to_account": assigned_account,
            "assigned_to_name": assigned_name,
            "remote_updated_at": remote_updated_at,
        }

    @staticmethod
    def _parse_zentao_datetime(raw: str | None) -> datetime | None:
        """Parse Zentao datetime strings like '2024-01-15 10:30:00' or ISO format."""
        if not raw:
            return None
        text = str(raw).strip()
        if not text or text == "0000-00-00 00:00:00":
            return None
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
            try:
                dt = datetime.strptime(text[:19], fmt[:len(text[:19])])
                return dt
            except ValueError:
                continue
        return None

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
        # Use pre-fetched maps when available (batch mode) to avoid per-row DB queries
        bug_row: BugTracking | None = None
        if existing_by_zentao_id is not None:
            bug_row = existing_by_zentao_id.get(normalized["zentao_bug_id"])
        if bug_row is None and existing_by_bug_id is not None:
            bug_row = existing_by_bug_id.get(normalized["bug_id"])
        if bug_row is None and existing_by_zentao_id is None:
            # Fallback to single DB query (non-batch callers)
            bug_row = (
                self.db.query(BugTracking)
                .filter(
                    (BugTracking.zentao_bug_id == normalized["zentao_bug_id"])
                    | (BugTracking.bug_id == normalized["bug_id"])
                )
                .first()
            )

        created = False
        updated = False
        matched_minor_id = self._match_minor_by_build_ids(major.id, normalized.get("opened_build_ids") or [])

        if bug_row is None:
            bug_row = BugTracking(
                major_version_id=major.id,
                requirement_id=None,
                source_type=BugSourceType.MANUAL,
                source_ref=None,
                bug_id=normalized["bug_id"],
                found_minor_version_id=matched_minor_id,
                created_by_id=actor.id,
            )
            self.db.add(bug_row)
            created = True
        else:
            original_snapshot = (
                bug_row.major_version_id,
                bug_row.found_minor_version_id,
                bug_row.zentao_bug_title,
                bug_row.zentao_bug_url,
                bug_row.zentao_bug_id,
                bug_row.zentao_opened_build_ids,
                bug_row.zentao_execution_id,
            )
            bug_row.major_version_id = major.id
            if matched_minor_id:
                bug_row.found_minor_version_id = matched_minor_id
            bug_row.bug_id = normalized["bug_id"]
            bug_row.zentao_bug_title = normalized["title"] or bug_row.zentao_bug_title
            bug_row.zentao_bug_url = normalized["url"] or bug_row.zentao_bug_url
            bug_row.zentao_bug_id = normalized["zentao_bug_id"]
            bug_row.zentao_opened_build_ids = json.dumps(normalized.get("opened_build_ids") or [], ensure_ascii=False)
            bug_row.zentao_execution_id = str(execution_id)
            bug_row.zentao_execution_name = major.zentao_execution_name_cache or major.version_no
            bug_row.zentao_sync_status = "synced"
            bug_row.zentao_sync_message = "通过 Stage5 版本全量同步"
            bug_row.last_zentao_synced_at = local_now()
            if normalized.get("status"):
                bug_row.zentao_live_status = normalized["status"]
            if bug_row.requirement_id is None:
                bug_row.source_type = BugSourceType.MANUAL
                bug_row.zentao_display_bucket = "overall"
            updated = original_snapshot != (
                bug_row.major_version_id,
                bug_row.found_minor_version_id,
                bug_row.zentao_bug_title,
                bug_row.zentao_bug_url,
                bug_row.zentao_bug_id,
                bug_row.zentao_opened_build_ids,
                bug_row.zentao_execution_id,
            )

        if created:
            bug_row.zentao_bug_title = normalized["title"]
            bug_row.zentao_bug_url = normalized["url"]
            bug_row.zentao_bug_id = normalized["zentao_bug_id"]
            bug_row.zentao_opened_build_ids = json.dumps(normalized.get("opened_build_ids") or [], ensure_ascii=False)
            bug_row.zentao_execution_id = str(execution_id)
            bug_row.zentao_execution_name = major.zentao_execution_name_cache or major.version_no
            bug_row.zentao_sync_status = "synced"
            bug_row.zentao_sync_message = "通过 Stage5 版本全量同步"
            bug_row.zentao_display_bucket = "overall"
            bug_row.last_zentao_synced_at = local_now()
            if normalized.get("status"):
                bug_row.zentao_live_status = normalized["status"]
            updated = False

        # Write back close/assignee/remote-update metadata regardless of create/update
        if normalized.get("closed_by_account"):
            bug_row.zentao_closed_by_account = normalized["closed_by_account"]
            bug_row.zentao_closed_by_name = normalized["closed_by_name"] or normalized["closed_by_account"]
        if normalized.get("close_date"):
            bug_row.zentao_close_date = normalized["close_date"]
        if normalized.get("close_comment"):
            bug_row.zentao_close_comment = normalized["close_comment"]
        if normalized.get("assigned_to_account"):
            bug_row.zentao_assigned_to_account = normalized["assigned_to_account"]
            bug_row.zentao_assigned_to_name = normalized["assigned_to_name"] or normalized["assigned_to_account"]
        if normalized.get("remote_updated_at"):
            bug_row.zentao_remote_updated_at = normalized["remote_updated_at"]

        # Mark Zentao-closed bugs as closed so they count in closed stats
        if normalized.get("status") == "closed":
            bug_row.closed = True

        return bug_row, created, updated

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

        # Check for an existing zentao_sync record first
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

        # Check if the user already manually closed this bug — don't overwrite manual records
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
                # Promote the manual (unclosed) record to closed by Zentao sync
                manual_record.test_done = True
                manual_record.source = "zentao_sync"
                manual_record.comment = close_comment
                manual_record.resolution = "fixed"
                manual_record.minor_version_id = manual_record.minor_version_id or bug_row.found_minor_version_id
                manual_record.updated_at = local_now()
            return

        # Create a new zentao_sync record
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
