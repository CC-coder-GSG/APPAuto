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
from app.services.zentao_system_client import get_system_zentao_client, get_user_zentao_client
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


def _fmt_now() -> str:
    """当前上海本地时间 → 禅道可接受的 'YYYY-MM-DD HH:MM:SS'。"""
    return local_now().strftime("%Y-%m-%d %H:%M:%S")


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

    def sync_mine(self, current_user: User) -> dict[str, Any]:
        """只刷新「当前用户已有任务」所在的执行（通常 1~3 个），远快于 sync_all。

        任务工作台手动刷新用；跨执行的全量覆盖交给后台 sync_all 周期任务。
        """
        client = get_system_zentao_client(self.db)
        if not client:
            return {"ok": False, "error": "找不到可用的禅道账号绑定", "synced": 0}
        exec_ids = [
            row[0]
            for row in self.db.query(ZentaoTaskMirror.execution_id)
            .filter(ZentaoTaskMirror.assignee_user_id == current_user.id, ZentaoTaskMirror.execution_id.isnot(None))
            .distinct()
            .all()
        ]
        if not exec_ids:
            # 镜像里还没有本人的任务（后台尚未同步）→ 回退一次全量，保证首次可见。
            return self.sync_all()
        majors = (
            self.db.query(Version)
            .filter(Version.zentao_execution_id.in_(exec_ids))
            .all()
        )
        by_account, by_name = self._build_user_maps()
        synced = 0
        errors: list[str] = []
        for major in majors:
            try:
                synced += self._sync_execution(client, major, by_account, by_name)
            except Exception as exc:  # noqa: BLE001
                logger.warning("sync_mine exec %s failed: %s", major.zentao_execution_id, exc)
                errors.append(str(exc))
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

    def list_mine_with_links(self, current_user: User) -> list[dict]:
        """任务工作台数据：当前用户名下的（非父）任务，并标注是否关联本平台需求。

        关联判定：优先按 requirements.zentao_task_id == task_id；否则按
        requirements.zentao_story_id == task.story。命中则带上需求 id / 编号 /
        大版本，供前端「跳转到需求工作台对应位置」。
        """
        from sqlalchemy.orm import joinedload

        from app.models import Requirement

        tasks = self.list_tasks(scope="mine", current_user_id=current_user.id)
        if not tasks:
            return []
        task_ids = [t["task_id"] for t in tasks]
        story_ids = [t["story"] for t in tasks if t.get("story")]

        by_task_id: dict[int, Requirement] = {}
        by_story: dict[int, Requirement] = {}
        if task_ids:
            for req in (
                self.db.query(Requirement).options(joinedload(Requirement.owner))
                .filter(Requirement.zentao_task_id.in_(task_ids)).all()
            ):
                by_task_id[int(req.zentao_task_id)] = req
        if story_ids:
            for req in (
                self.db.query(Requirement).options(joinedload(Requirement.owner))
                .filter(Requirement.zentao_story_id.in_(story_ids)).all()
            ):
                if req.zentao_story_id is not None:
                    by_story.setdefault(int(req.zentao_story_id), req)

        my_account = (current_user.zentao_account or "").strip().lower()
        for t in tasks:
            req = by_task_id.get(t["task_id"]) or (by_story.get(t["story"]) if t.get("story") else None)
            # 是否可对禅道操作：镜像已映射到本人，或禅道账号匹配（与 operate_task 权限一致，
            # 兼容未设 zentao_account、靠真实姓名兜底映射的用户）。
            acc = (t.get("assigned_to") or "").strip().lower()
            assigned_to_me = (t.get("assignee_user_id") == current_user.id) or (bool(my_account) and acc == my_account)
            requirement_mine = bool(req) and req.owner_id == current_user.id
            if req:
                t["linked_requirement"] = {
                    "id": req.id,
                    "zentao_req_id": req.zentao_req_id,
                    "title": req.title,
                    "major_version_id": req.major_version_id,
                    "owner_id": req.owner_id,
                    "owner_name": req.owner.shown_name if req.owner else None,
                }
            else:
                t["linked_requirement"] = None
            t["assigned_to_me"] = assigned_to_me
            t["requirement_mine"] = requirement_mine
            # 关联需求且该需求归属当前账号 → 跳转到需求工作台管理（不在此直接操作禅道）。
            t["show_jump"] = bool(req) and requirement_mine
            # 可在此直接操作禅道：任务指派给本人，且不是「本人负责的需求」（独立任务，
            # 或需求归属他人的衍生任务——此时需求工作台里看不到，必须能在任务工作台操作）。
            t["can_operate"] = assigned_to_me and not requirement_mine
        return tasks

    # ------------------------------------------------------------------
    # 独立（未关联需求）任务的禅道操作：开始 / 完成 / 关闭 / 设置工时
    # ------------------------------------------------------------------

    _OP_ACTIONS = {"start", "pause", "finish", "close", "reactivate", "set_time"}

    def operate_task(
        self,
        *,
        task_id: int,
        action: str,
        current_user: User,
        hours: Optional[float] = None,
        consumed: Optional[float] = None,
        comment: Optional[str] = None,
    ) -> dict:
        """对未关联需求的禅道任务执行操作。仅任务指派人本人可操作。"""
        from fastapi import HTTPException

        if action not in self._OP_ACTIONS:
            raise HTTPException(status_code=400, detail=f"不支持的操作：{action}")
        row = self.db.query(ZentaoTaskMirror).filter(ZentaoTaskMirror.task_id == task_id).first()
        if not row:
            raise HTTPException(status_code=404, detail="任务不存在或未同步")
        # 权限：任务须指派给当前账号本人（禅道账号匹配，或镜像已映射到本人）。
        my_account = (current_user.zentao_account or "").strip().lower()
        acc = (row.assigned_to or "").strip().lower()
        is_mine = (row.assignee_user_id == current_user.id) or (bool(my_account) and acc == my_account)
        if not is_mine:
            raise HTTPException(status_code=403, detail="该任务未指派给你，无法操作")

        # 优先用「操作人本人」的禅道客户端：让禅道把操作人记为其本人、并保持指派不变；
        # 无本人绑定时回退系统账号（此时靠回传 assignedTo + 事后校正保持指派人）。
        acting_client = get_user_zentao_client(current_user.id, self.db)
        client = acting_client or get_system_zentao_client(self.db)
        if not client:
            raise HTTPException(status_code=502, detail="找不到可用的禅道账号绑定")

        original_account = row.assigned_to  # 操作前的指派人账号，用于事后校正
        errors: list[str] = []
        action_resp = None
        try:
            if action == "start":
                left = hours if (hours and hours > 0) else (row.left or row.estimate or 1.0)
                if (row.status or "").strip().lower() == "pause":
                    # 暂停中的任务用 restart「继续」，保持指派人
                    action_resp = client.restart_task(task_id, consumed=(row.consumed or 0.0), left=left, assigned_to=original_account)
                else:
                    action_resp = client.start_task(task_id, real_started=_fmt_now(), left=left, assigned_to=original_account)
            elif action == "pause":
                action_resp = client.pause_task(task_id, comment=comment)
            elif action == "finish":
                cur = consumed if (consumed and consumed > 0) else (row.left or row.estimate or 1.0)
                action_resp = client.finish_task(task_id, current_consumed=cur, finished_date=_fmt_now())
            elif action == "reactivate":
                left = hours if (hours and hours > 0) else (row.estimate or 1.0)
                action_resp = client.restart_task(task_id, consumed=(row.consumed or 0.0), left=left, assigned_to=original_account)
            elif action == "close":
                action_resp = client.close_task(task_id, comment=comment)
            elif action == "set_time":
                if hours is None or hours <= 0 or hours > 999:
                    raise HTTPException(status_code=400, detail="工时需在 0~999 小时之间")
                action_resp = client.update_task(task_id, {"estimate": hours, "left": hours})
            logger.info("operate task %s action=%s own_client=%s resp=%r", task_id, action, acting_client is not None, action_resp)
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning("operate task %s action=%s failed: %s", task_id, action, exc)
            errors.append(str(exc))

        # 操作后从禅道回读最新任务态，刷新镜像行。
        fresh = self._refresh_one_task(client, row)
        # 校验是否真的生效：禅道对某些操作可能返回 200 但并未真正切换状态。若状态未达预期，
        # 明确报错并带上禅道原始返回，便于定位（区分接口不支持 / 状态不允许 / 权限等）。
        _expected = {"start": "doing", "pause": "pause", "reactivate": "doing", "finish": "done", "close": "closed"}.get(action)
        if not errors and _expected and (row.status or "").strip().lower() != _expected:
            logger.warning("operate task %s action=%s no-op: status=%s expected=%s resp=%r", task_id, action, row.status, _expected, action_resp)
            errors.append(
                f"禅道未生效：任务当前状态为「{row.status or '未知'}」（期望「{_expected}」）。"
                f"禅道返回：{str(action_resp)[:200]}"
            )
        # 兜底校正：若禅道把指派人清空/改掉（系统账号操作 start/pause/continue 的已知副作用），
        # 且操作原本不该改指派人，则改派回原指派人。按「禅道真实值」判断（refresh 出于稳健
        # 不会用空值覆盖镜像，故这里直接看回读到的任务）。
        if not errors and original_account and action in {"start", "pause", "reactivate", "set_time"}:
            cur_acc, _ = _account_of((fresh or {}).get("assignedTo"))
            if (cur_acc or "").strip().lower() != original_account.strip().lower():
                try:
                    client.reassign_task(task_id, original_account)
                    self._refresh_one_task(client, row)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("restore assignee task %s -> %s failed: %s", task_id, original_account, exc)
        self.db.commit()
        return {"ok": not errors, "errors": errors, "task": self._serialize(row)}

    def _refresh_one_task(self, client, row: ZentaoTaskMirror) -> Optional[dict]:
        try:
            t = client.get_task(row.task_id) or {}
        except Exception as exc:  # noqa: BLE001
            logger.warning("refresh one task %s failed: %s", row.task_id, exc)
            return None
        if not isinstance(t, dict) or not t:
            return None
        account, realname = _account_of(t.get("assignedTo"))
        by_account, by_name = self._build_user_maps()
        if t.get("status") is not None:
            row.status = t.get("status")
        if account:
            row.assigned_to = account
            row.assigned_to_realname = realname or row.assigned_to_realname
            uid = by_account.get(account.strip().lower())
            if uid is None and row.assigned_to_realname:
                uid = by_name.get(row.assigned_to_realname.strip())
            row.assignee_user_id = uid
        row.estimate = _as_float(t.get("estimate")) if t.get("estimate") is not None else row.estimate
        row.consumed = _as_float(t.get("consumed")) if t.get("consumed") is not None else row.consumed
        row.left = _as_float(t.get("left")) if t.get("left") is not None else row.left
        if t.get("realStarted") is not None:
            row.real_started = _parse_dt(t.get("realStarted"))
        if t.get("finishedDate") is not None:
            row.finished_date = _parse_dt(t.get("finishedDate"))
        if t.get("deadline") is not None:
            row.deadline = _parse_date(t.get("deadline"))
        row.synced_at = local_now()
        return t

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
