from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.models import User
from app.services.overall_test_service import OverallTestService
from app.services.zentao_testcase_service import ZentaoTestCaseService


class WorkbenchRefreshService:
    def __init__(self, db: Session):
        self.db = db
        self.overall_test_service = OverallTestService(db)
        self.testcase_service = ZentaoTestCaseService(db)

    def preflight_refresh(
        self,
        *,
        software_id: int,
        current_user: User,
        include_bugs: bool = True,
        include_testcases: bool = True,
        force: bool = False,
    ) -> dict[str, Any]:
        result: dict[str, Any] = {
            "software_id": software_id,
            "include_bugs": include_bugs,
            "include_testcases": include_testcases,
            "force": force,
            "bugs": None,
            "testcases": None,
        }

        if include_bugs:
            try:
                result["bugs"] = self.overall_test_service.sync_recent_zentao_bugs_by_software(
                    software_id=software_id,
                    current_user=current_user,
                    force=force,
                    sync_source="on_enter_refresh",
                )
            except Exception as exc:
                self.db.rollback()
                result["bugs"] = {
                    "ok": False,
                    "error": str(getattr(exc, "detail", None) or exc),
                    "sync_mode": "recent",
                }

        if include_testcases:
            try:
                result["testcases"] = self.testcase_service.sync_software_testcases(
                    software_id=software_id,
                    current_user=current_user,
                    force=force,
                    sync_source="on_enter_refresh",
                )
            except Exception as exc:
                self.db.rollback()
                result["testcases"] = {
                    "ok": False,
                    "error": str(getattr(exc, "detail", None) or exc),
                }

        return result
