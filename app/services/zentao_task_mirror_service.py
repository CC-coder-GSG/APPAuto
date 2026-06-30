"""禅道任务 → 本地镜像缓存（任务看板接入禅道，能力 D）。

后台 job 周期性遍历已绑定执行、拉任务、upsert 到 zentao_task_mirror；
任务看板从镜像读取，避免每次开看板都实时全量拉禅道。
"""
from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.models import User, Version, VersionType
from app.models.zentao_task_mirror import ZentaoTaskMirror
from app.services.zentao_system_client import get_system_zentao_client
from app.utils.time_utils import local_now, parse_external_datetime_to_local_naive

logger = logging.getLogger(__name__)


def _coerce_int(v) -> Optional[int]:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _as_float(v) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _parse_date(v) -> Optional[date]:
    if not v or str(v).startswith("0000"):
        return None
    dt = parse_external_datetime_to_local_naive(str(v))
    return dt.date() if dt else None


def _parse_dt(v) -> Optional[datetime]:
    if not v or str(v).startswith("0000"):
        return None
    return parse_external_datetime_to_local_naive(str(v))


def _account_of(assigned_to) -> tuple[Optional[str], Optional[str]]:
    """禅道 assignedTo 兼容对象/字符串 → (account, realname)。"""
    if isinstance(assigned_to, dict):
        return assigned_to.get("account"), assigned_to.get("realname")
    if assigned_to:
        return str(assigned_to), None
    return None, None


class ZentaoTaskMirrorService:
    def __init__(self, db: Session):
        self.db = db

    def _build_user_maps(self) -> tuple[dict[str, int], dict[str, int]]:
        """返回 (禅道账号→user_id, 真实姓名→user_id)。多数用户没设 zentao_account，
        故同时按真实姓名兜底映射，保证任务看板 scope=mine 能匹配到本人。"""
        rows = self.db.query(User).all()
        by_account: dict[str, int] = {}
        by_name: dict[str, int] = {}
        for u in rows:
            if u.zentao_account:
                by_account[u.zentao_account.strip().lower()] = u.id
            nm = (u.shown_name or "").strip()
            if nm:
                by_name.setdefault(nm, u.id)
        return by_account, by_name

    def sync_all(self) -> dict[str, Any]:
        """遍历所有绑定了禅道执行的大版本，刷新其任务镜像。"""
        client = get_system_zentao_client(self.db)
        if not client:
            return {"ok": False, "error": "找不到可用的禅道账号绑定", "synced": 0}
        majors = (
            self.db.query(Version)
            .filter(Version.version_type == VersionType.MAJOR, Version.zentao_execution_id.isnot(None))
            .all()
        )
        by_account, by_name = self._build_user_maps()
        synced = 0
        errors: list[str] = []
        for major in majors:
            try:
                synced += self._sync_execution(client, major, by_account, by_name)
            except Exception as exc:  # noqa: BLE001
                logger.warning("mirror sync exec %s failed: %s", major.zentao_execution_id, exc)
                errors.append(f"执行 {major.zentao_execution_id}: {exc}")
        self.db.commit()
        return {"ok": not errors, "synced": synced, "executions": len(majors), "errors": errors}

    def sync_one_major(self, major_version_id: int) -> dict[str, Any]:
        client = get_system_zentao_client(self.db)
        if not client:
            return {"ok": False, "error": "找不到可用的禅道账号绑定", "synced": 0}
        major = self.db.query(Version).filter(Version.id == major_version_id).first()
        if not major or not major.zentao_execution_id:
            return {"ok": False, "error": "大版本未绑定禅道执行", "synced": 0}
        by_account, by_name = self._build_user_maps()
        try:
            n = self._sync_execution(client, major, by_account, by_name)
            self.db.commit()
            return {"ok": True, "synced": n}
        except Exception as exc:  # noqa: BLE001
            self.db.rollback()
            return {"ok": False, "error": str(exc), "synced": 0}

    def _sync_execution(self, client, major: Version, by_account: dict[str, int], by_name: dict[str, int]) -> int:
        exec_id = int(major.zentao_execution_id)
        tasks = client.list_execution_tasks(exec_id) or []
        n = 0
        for t in tasks:
            if not isinstance(t, dict):
                continue
            tid = _coerce_int(t.get("id"))
            if not tid:
                continue
            account, realname = _account_of(t.get("assignedTo"))
            row = self.db.query(ZentaoTaskMirror).filter(ZentaoTaskMirror.task_id == tid).first()
            if row is None:
                row = ZentaoTaskMirror(task_id=tid)
                self.db.add(row)
            row.execution_id = exec_id
            row.execution_name_cache = major.zentao_execution_name_cache or major.version_no
            row.project_id = _coerce_int(t.get("project"))
            row.parent = _coerce_int(t.get("parent")) or 0
            row.is_parent = 1 if _coerce_int(t.get("isParent")) else 0
            row.name = str(t.get("name") or "")[:255]
            row.type = t.get("type")
            row.status = t.get("status")
            row.pri = _coerce_int(t.get("pri"))
            row.story = _coerce_int(t.get("story"))
            row.assigned_to = account
            row.assigned_to_realname = realname or t.get("assignedToRealName")
            # 先按禅道账号映射本地用户；没设账号的用户用真实姓名兜底，确保看板能匹配到本人。
            uid = by_account.get((account or "").strip().lower()) if account else None
            if uid is None:
                nm = (row.assigned_to_realname or "").strip()
                if nm:
                    uid = by_name.get(nm)
            row.assignee_user_id = uid
            row.estimate = _as_float(t.get("estimate"))
            row.consumed = _as_float(t.get("consumed"))
            row.left = _as_float(t.get("left"))
            row.est_started = _parse_date(t.get("estStarted"))
            row.deadline = _parse_date(t.get("deadline"))
            row.real_started = _parse_dt(t.get("realStarted"))
            row.finished_date = _parse_dt(t.get("finishedDate"))
            row.synced_at = local_now()
            n += 1
        return n

    # ------------------------------------------------------------------
    # 读取（任务看板）
    # ------------------------------------------------------------------

    def list_tasks(
        self,
        *,
        scope: str = "all",
        current_user_id: Optional[int] = None,
        execution_id: Optional[int] = None,
        include_parents: bool = False,
    ) -> list[dict]:
        """从镜像读任务。scope=mine 只看自己；execution_id 限定某执行。"""
        q = self.db.query(ZentaoTaskMirror)
        if execution_id:
            q = q.filter(ZentaoTaskMirror.execution_id == execution_id)
        if scope == "mine" and current_user_id:
            q = q.filter(ZentaoTaskMirror.assignee_user_id == current_user_id)
        if not include_parents:
            q = q.filter(ZentaoTaskMirror.is_parent == 0)
        # 排除已取消/关闭，按截止日期排
        rows = q.order_by(ZentaoTaskMirror.deadline.asc().nullslast(), ZentaoTaskMirror.task_id.asc()).all()
        return [self._serialize(r) for r in rows]

    @staticmethod
    def _serialize(r: ZentaoTaskMirror) -> dict:
        return {
            "task_id": r.task_id,
            "execution_id": r.execution_id,
            "execution_name": r.execution_name_cache,
            "parent": r.parent,
            "is_parent": bool(r.is_parent),
            "name": r.name,
            "type": r.type,
            "status": r.status,
            "pri": r.pri,
            "story": r.story,
            "assigned_to": r.assigned_to,
            "assigned_to_realname": r.assigned_to_realname,
            "assignee_user_id": r.assignee_user_id,
            "estimate": r.estimate,
            "consumed": r.consumed,
            "left": r.left,
            "est_started": r.est_started.isoformat() if r.est_started else None,
            "deadline": r.deadline.isoformat() if r.deadline else None,
            "real_started": r.real_started.isoformat() if r.real_started else None,
            "finished_date": r.finished_date.isoformat() if r.finished_date else None,
            "synced_at": r.synced_at.isoformat() if r.synced_at else None,
        }


__all__ = ["ZentaoTaskMirrorService"]
