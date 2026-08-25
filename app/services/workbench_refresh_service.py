from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.models import User
from app.services.overall_test_service import OverallTestService
from app.services.zentao_testcase_service import ZentaoTestCaseService
from app.utils.time_utils import local_now

# 勾「用例完成」触发的轻量用例增量同步（sync_recent 无 TTL、每次真拉禅道），
# 用进程内时间戳做最小间隔保护，防止连续勾选打穿频率（前端另有去抖+冷却）。
_testcase_recent_last_run: dict[int, datetime] = {}
_TESTCASE_RECENT_MIN_INTERVAL = timedelta(seconds=60)


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
        testcases_recent: bool = False,
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
                if testcases_recent:
                    result["testcases"] = self._sync_testcases_recent_throttled(
                        software_id=software_id, current_user=current_user
                    )
                else:
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

    def _sync_testcases_recent_throttled(self, *, software_id: int, current_user: User) -> dict[str, Any]:
        """勾「用例完成」的用例快速同步：走 sync_recent（只拉最近编辑页，秒级、
        无 5 分钟 TTL），60s 最小间隔兜底防打穿。命中间隔时返回 cached 标记。"""
        last = _testcase_recent_last_run.get(software_id)
        now = local_now()
        if last and (now - last) < _TESTCASE_RECENT_MIN_INTERVAL:
            return {
                "software_id": software_id,
                "cached": True,
                "sync_mode": "recent",
                "sync_source": "case_done_refresh",
                "created": 0,
                "updated": 0,
            }
        _testcase_recent_last_run[software_id] = now
        try:
            return self.testcase_service.sync_recent_software_testcases(
                software_id=software_id,
                current_user=current_user,
                sync_source="case_done_refresh",
            )
        except Exception:
            # 失败不占用间隔窗口：下次勾选可立即重试
            _testcase_recent_last_run.pop(software_id, None)
            raise
