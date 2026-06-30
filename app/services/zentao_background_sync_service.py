from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import SoftwareProduct, User, UserZentaoBinding
from app.services.overall_test_service import OverallTestService
from app.services.workbench_refresh_service import WorkbenchRefreshService
from app.services.zentao_testcase_service import ZentaoTestCaseService

logger = logging.getLogger(__name__)


class ZentaoBackgroundSyncService:
    def __init__(self, db: Session):
        self.db = db
        self.workbench_refresh_service = WorkbenchRefreshService(db)
        self.overall_test_service = OverallTestService(db)
        self.testcase_service = ZentaoTestCaseService(db)

    def run_recent_sync_for_all_software(self) -> dict[str, Any]:
        actor = self._resolve_sync_actor()
        software_rows = self.db.query(SoftwareProduct).order_by(SoftwareProduct.id.asc()).all()

        items: list[dict[str, Any]] = []
        success = 0
        failed = 0
        for software in software_rows:
            try:
                # Background scheduled job — pass force=True because we want
                # the sync to run regardless of the in-memory TTL cache, and
                # tag the rows with `scheduled_pull` so the workbench can
                # tell scheduled refreshes apart from on-enter refreshes.
                result = self.overall_test_service.sync_recent_zentao_bugs_by_software(
                    software_id=software.id,
                    current_user=actor,
                    force=True,
                    sync_source="scheduled_pull",
                )
                testcase_result = self.testcase_service.sync_software_testcases(
                    software_id=software.id,
                    current_user=actor,
                    force=True,
                    sync_source="scheduled_pull",
                )
                result = {"bugs": result, "testcases": testcase_result}
                items.append({
                    "software_id": software.id,
                    "software_name": software.name,
                    "ok": True,
                    "result": result,
                })
                success += 1
            except Exception as exc:
                self.db.rollback()
                logger.warning("background recent sync failed | software_id=%s err=%s", software.id, exc)
                items.append({
                    "software_id": software.id,
                    "software_name": software.name,
                    "ok": False,
                    "error": str(getattr(exc, "detail", None) or exc),
                })
                failed += 1

        # 顺带刷新禅道任务镜像（任务看板用），失败不影响主流程
        task_mirror = None
        try:
            from app.services.zentao_task_mirror_service import ZentaoTaskMirrorService
            task_mirror = ZentaoTaskMirrorService(self.db).sync_all()
        except Exception as exc:
            self.db.rollback()
            logger.warning("background task-mirror sync failed: %s", exc)
            task_mirror = {"ok": False, "error": str(exc)}

        return {
            "mode": "recent",
            "actor_id": actor.id,
            "actor_username": actor.username,
            "total": len(software_rows),
            "success": success,
            "failed": failed,
            "items": items,
            "task_mirror": task_mirror,
        }

    def run_nightly_full_sync_for_all_software(self) -> dict[str, Any]:
        actor = self._resolve_sync_actor()
        software_rows = self.db.query(SoftwareProduct).order_by(SoftwareProduct.id.asc()).all()

        items: list[dict[str, Any]] = []
        success = 0
        failed = 0
        for software in software_rows:
            try:
                bug_result = self.overall_test_service.sync_all_zentao_bugs_by_software(
                    software_id=software.id,
                    current_user=actor,
                    force=True,
                )
                testcase_result = self.testcase_service.sync_software_testcases(
                    software_id=software.id,
                    current_user=actor,
                    force=True,
                    sync_source="nightly_reconcile",
                )
                items.append({
                    "software_id": software.id,
                    "software_name": software.name,
                    "ok": True,
                    "bugs": bug_result,
                    "testcases": testcase_result,
                })
                success += 1
            except Exception as exc:
                self.db.rollback()
                logger.warning("background nightly sync failed | software_id=%s err=%s", software.id, exc)
                items.append({
                    "software_id": software.id,
                    "software_name": software.name,
                    "ok": False,
                    "error": str(getattr(exc, "detail", None) or exc),
                })
                failed += 1

        return {
            "mode": "nightly_full",
            "actor_id": actor.id,
            "actor_username": actor.username,
            "total": len(software_rows),
            "success": success,
            "failed": failed,
            "items": items,
        }

    def _resolve_sync_actor(self) -> User:
        preferred = (settings.zentao_background_sync_username or "").strip()
        if preferred:
            row = (
                self.db.query(User)
                .join(UserZentaoBinding, UserZentaoBinding.user_id == User.id)
                .filter(User.username == preferred)
                .order_by(User.id.asc())
                .first()
            )
            if row:
                return row

        fallback = (
            self.db.query(User)
            .join(UserZentaoBinding, UserZentaoBinding.user_id == User.id)
            .filter(UserZentaoBinding.base_url.isnot(None))
            .order_by(User.id.asc())
            .first()
        )
        if fallback:
            return fallback

        raise RuntimeError("No Zentao-bound user is available for background sync")
