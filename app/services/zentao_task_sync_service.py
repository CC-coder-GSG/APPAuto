"""测试管理系统「分配」↔ 禅道「任务」联动编排（2026-06-29 需求，能力 A/C）。

设计原则（与 zentao_version_diff / build_push 一致）：
  - 所有禅道写操作走 get_system_zentao_client（优先 chenwenbo）。
  - 单条失败不阻塞整批；任何禅道异常都收进 errors，不向路由抛。
  - 禅道为权威，本地回写 zentao_task_id / parent / 状态缓存做镜像。

能力 A（分配 → 建任务）：
  每次「分配并发布」在该执行下**新建一个测试父任务**，子任务**只含本次新分配、
  此前没建过任务的需求**（requirement.zentao_task_id 为空）；已有任务但换了负责人的
  需求直接改派其现有子任务，不进新父任务。

能力 C（开始/完成/重新激活）：
  start_requirement_task / finish_requirement_task / reactivate_requirement_task，
  工时按 work_hours 计算后回写禅道。
"""
from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Optional

from sqlalchemy.orm import Session

from app.models import Requirement, User, Version
from app.services.holiday_service import get_holiday_map
from app.services.zentao_system_client import get_system_zentao_client
from app.utils import work_hours
from app.utils.time_utils import local_now, parse_external_datetime_to_local_naive

logger = logging.getLogger(__name__)


def _as_date(value) -> Optional[date]:
    if value is None:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    parsed = parse_external_datetime_to_local_naive(str(value))
    return parsed.date() if parsed else None


def _fmt_date(d: Optional[date]) -> Optional[str]:
    return d.strftime("%Y-%m-%d") if d else None


def _fmt_dt(dt: Optional[datetime]) -> Optional[str]:
    return dt.strftime("%Y-%m-%d %H:%M:%S") if dt else None


def _zentao_dt_to_local(raw) -> Optional[datetime]:
    """禅道返回的 UTC ISO(...Z) → 上海本地 naive。"""
    return parse_external_datetime_to_local_naive(raw) if raw else None


def _coerce_int(v) -> Optional[int]:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _task_account(t: dict) -> Optional[str]:
    """禅道任务 assignedTo → account（兼容对象/字符串）。"""
    a = t.get("assignedTo")
    if isinstance(a, dict):
        return a.get("account")
    return str(a) if a else None


class ZentaoTaskSyncService:
    def __init__(self, db: Session):
        self.db = db

    # ------------------------------------------------------------------
    # 用户 → 禅道账号
    # ------------------------------------------------------------------

    def _resolve_account(self, user: Optional[User], assignable: dict[str, str]) -> Optional[str]:
        """解析某用户的禅道账号：优先 user.zentao_account，否则按姓名/用户名匹配并回填缓存。"""
        if user is None:
            return None
        if user.zentao_account:
            return user.zentao_account
        name = (user.shown_name or "").strip()
        uname = (user.username or "").strip().lower()
        for acc, realname in assignable.items():
            if (name and realname.strip() == name) or acc.strip().lower() == uname:
                user.zentao_account = acc  # 缓存，随本次事务提交
                return acc
        return None

    # ------------------------------------------------------------------
    # 能力 A：分配 → 建/改派任务
    # ------------------------------------------------------------------

    def create_tasks_for_assignment(
        self,
        major_version_id: int,
        assignments: list[dict],
        *,
        est_started=None,
        deadline=None,
        actor: Optional[User] = None,
    ) -> dict:
        """据分配清单在禅道建/改派任务。

        assignments：[{"requirement_id": int, "owner_id": int|None}, ...]
        est_started / deadline：父任务起止日期（date 或 'YYYY-MM-DD'）；缺省时只建任务不填日期。
        返回 {ok, created_tasks, reassigned_tasks, parent_task_id, unassigned, errors}
        """
        out: dict = {
            "ok": False,
            "parent_task_id": None,
            "created_tasks": [],
            "reassigned_tasks": [],
            "unassigned": [],   # [{requirement_id, owner_name}] 解析不到禅道账号
            "errors": [],
        }

        major = self.db.query(Version).filter(Version.id == major_version_id).first()
        if not major:
            out["errors"].append("大版本不存在")
            return out
        if not major.zentao_execution_id:
            out["errors"].append("该大版本未绑定禅道执行，无法创建禅道任务")
            return out

        client = get_system_zentao_client(self.db)
        if not client:
            out["errors"].append("找不到可用的禅道账号绑定，未创建禅道任务")
            return out

        exec_id = int(major.zentao_execution_id)
        try:
            assignable = client.list_assignable_users(exec_id)
        except Exception as exc:
            logger.warning("list_assignable_users(%s) failed: %s", exec_id, exc)
            assignable = {}

        start_d = _as_date(est_started)
        end_d = _as_date(deadline)
        holiday_map = {}
        parent_estimate = None
        if start_d and end_d:
            try:
                holiday_map = get_holiday_map(self.db, start_d, end_d)
            except Exception as exc:
                logger.warning("get_holiday_map failed: %s", exc)
            parent_estimate = work_hours.estimate_hours(start_d, end_d, holiday_map)

        # 分类需求
        req_ids = [a.get("requirement_id") for a in assignments if a.get("requirement_id")]
        req_map = {
            r.id: r
            for r in self.db.query(Requirement).filter(Requirement.id.in_(req_ids)).all()
        }
        owner_ids = {a.get("owner_id") for a in assignments if a.get("owner_id")}
        user_map = {
            u.id: u for u in self.db.query(User).filter(User.id.in_(owner_ids)).all()
        } if owner_ids else {}

        new_items: list[tuple[Requirement, User]] = []      # 待建子任务
        reassign_items: list[tuple[Requirement, User]] = []  # 待改派

        for a in assignments:
            req = req_map.get(a.get("requirement_id"))
            owner = user_map.get(a.get("owner_id"))
            if not req or not owner:
                continue
            if req.zentao_task_id:
                reassign_items.append((req, owner))
            else:
                new_items.append((req, owner))

        # ── 改派已有子任务 ──
        for req, owner in reassign_items:
            acc = self._resolve_account(owner, assignable)
            if not acc:
                out["unassigned"].append({"requirement_id": req.id, "owner_name": owner.shown_name})
                continue
            try:
                client.reassign_task(int(req.zentao_task_id), acc)
                out["reassigned_tasks"].append(req.zentao_task_id)
            except Exception as exc:
                logger.warning("reassign task %s -> %s failed: %s", req.zentao_task_id, acc, exc)
                out["errors"].append(f"改派子任务 #{req.zentao_task_id}（{req.title}）失败：{exc}")

        # ── 新需求：幂等处理（认领已存在任务 + 仅对真正缺失的新建）──
        # 先按 story 索引执行下已有的 test 子任务。若某需求的任务此前已建过（常见于
        # 子任务很多、单次请求超时导致禅道已建但本地没写回），本次直接认领写回，
        # 绝不重复新建。这样「重新发布一次」即可自愈历史未关联的需求。
        existing_by_story = self._index_existing_test_tasks(client, exec_id)

        adopt_items: list[tuple[Requirement, User, dict]] = []
        create_new_items: list[tuple[Requirement, User]] = []
        for req, owner in new_items:
            t = existing_by_story.get(req.zentao_story_id) if req.zentao_story_id else None
            if t:
                adopt_items.append((req, owner, t))
            else:
                create_new_items.append((req, owner))

        # 认领：写回本地 + 负责人不一致则改派。逐条提交，进度不丢。
        for req, owner, t in adopt_items:
            tid = _coerce_int(t.get("id"))
            if not tid:
                continue
            req.zentao_task_id = tid
            pid = _coerce_int(t.get("parent"))
            if pid:
                req.zentao_parent_task_id = pid
            req.zentao_task_status_cache = str(t.get("status") or "wait")
            acc = self._resolve_account(owner, assignable)
            cur_acc = _task_account(t)
            if acc and cur_acc and acc.strip().lower() != cur_acc.strip().lower():
                try:
                    client.reassign_task(tid, acc)
                    out["reassigned_tasks"].append(tid)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("reassign adopted task %s -> %s failed: %s", tid, acc, exc)
                    out["errors"].append(f"改派子任务 #{tid}（{req.title}）失败：{exc}")
            out["created_tasks"].append(tid)
            try:
                self.db.commit()
            except Exception as exc:  # noqa: BLE001
                logger.warning("commit adopt req %s failed: %s", req.id, exc)
                self.db.rollback()

        # ── 真正缺失的：建父任务 + 逐条子任务，每条建完即提交 ──
        parent_id: Optional[int] = None
        if create_new_items:
            parent_name = f"{major.version_no} 测试任务"
            parent_desc = (
                f"由测试管理系统于 {local_now().strftime('%Y-%m-%d %H:%M')} 分配，"
                f"共 {len(create_new_items)} 个研发需求子任务。"
            )
            actor_acc = self._resolve_account(actor, assignable) if actor else None
            try:
                created = client.create_execution_task(
                    exec_id,
                    name=parent_name,
                    assigned_to=actor_acc,
                    task_type="test",
                    est_started=_fmt_date(start_d),
                    deadline=_fmt_date(end_d),
                    estimate=parent_estimate,
                    desc=parent_desc,
                ) or {}
                parent_id = created.get("id") if isinstance(created, dict) else None
            except Exception as exc:
                logger.warning("create parent task in exec %s failed: %s", exec_id, exc)
                out["errors"].append(f"创建父任务失败：{exc}")

            if parent_id:
                out["parent_task_id"] = parent_id
                for req, owner in create_new_items:
                    acc = self._resolve_account(owner, assignable)
                    if not acc:
                        out["unassigned"].append({"requirement_id": req.id, "owner_name": owner.shown_name})
                        continue
                    child_name = (req.title or f"需求 {req.zentao_req_id}")[:180]
                    child_desc = f"研发需求 {req.zentao_req_id}：{req.title}"
                    try:
                        child = client.create_execution_task(
                            exec_id,
                            name=child_name,
                            assigned_to=acc,
                            task_type="test",
                            story=req.zentao_story_id or None,
                            est_started=_fmt_date(start_d),
                            deadline=_fmt_date(end_d),
                            estimate=req.estimated_test_hours,
                            desc=child_desc,
                        ) or {}
                        child_id = child.get("id") if isinstance(child, dict) else None
                        if not child_id:
                            out["errors"].append(f"子任务（{req.title}）创建未返回 id")
                            continue
                        # create 不认 parent，须 PUT 设父子
                        try:
                            client.link_task_parent(int(child_id), int(parent_id))
                        except Exception as exc:
                            logger.warning("link child %s -> parent %s failed: %s", child_id, parent_id, exc)
                            out["errors"].append(f"子任务 #{child_id} 挂到父任务失败：{exc}")
                        req.zentao_task_id = int(child_id)
                        req.zentao_parent_task_id = int(parent_id)
                        req.zentao_task_status_cache = "wait"
                        out["created_tasks"].append(child_id)
                        # 逐条提交：即使后续超时，已建的也不丢、下次按 story 认领不会重复建。
                        try:
                            self.db.commit()
                        except Exception as exc:  # noqa: BLE001
                            logger.warning("commit child req %s failed: %s", req.id, exc)
                            self.db.rollback()
                    except Exception as exc:
                        logger.warning("create child task for req %s failed: %s", req.id, exc)
                        out["errors"].append(f"为需求「{req.title}」创建子任务失败：{exc}")

        self.db.commit()
        out["ok"] = not out["errors"]
        return out

    def _index_existing_test_tasks(self, client, exec_id: int) -> dict[int, dict]:
        """按 story 索引执行下已有的 test 子任务，取同 story 中 id 最大（最新）的一条。"""
        try:
            rows = client.list_execution_tasks(exec_id) or []
        except Exception as exc:  # noqa: BLE001
            logger.warning("index existing tasks exec %s failed: %s", exec_id, exc)
            return {}
        idx: dict[int, dict] = {}
        for t in rows:
            if not isinstance(t, dict) or str(t.get("type") or "") != "test":
                continue
            sid = _coerce_int(t.get("story"))
            tid = _coerce_int(t.get("id"))
            if not sid or not tid:
                continue
            # 只认子任务（有 parent），父任务 story 通常为 0、不会进来
            prev = idx.get(sid)
            if prev is None or tid > _coerce_int(prev.get("id")):
                idx[sid] = t
        return idx

    # ------------------------------------------------------------------
    # 能力 C：开始 / 完成 / 重新激活
    # ------------------------------------------------------------------

    def _client_or_error(self, out: dict):
        client = get_system_zentao_client(self.db)
        if not client:
            out["errors"].append("找不到可用的禅道账号绑定")
        return client

    def start_requirement_task(self, requirement: Requirement, *, hours: Optional[float] = None) -> dict:
        """点击「开始」：禅道子任务 start，记录本地开始时刻。"""
        out: dict = {"ok": False, "errors": []}
        now = local_now()
        requirement.task_started_at = now
        left = hours if hours is not None else (requirement.estimated_test_hours or 4.0)
        if requirement.zentao_task_id:
            client = self._client_or_error(out)
            if client:
                try:
                    client.start_task(int(requirement.zentao_task_id), real_started=_fmt_dt(now), left=left)
                    requirement.zentao_task_status_cache = "doing"
                except Exception as exc:
                    logger.warning("start task %s failed: %s", requirement.zentao_task_id, exc)
                    out["errors"].append(f"禅道开始任务失败：{exc}")
        self.db.commit()
        out["ok"] = not out["errors"]
        return out

    def finish_requirement_task(self, requirement: Requirement) -> dict:
        """勾「测试完成」：算工时 → 禅道 finish，记录本地完成时刻。"""
        out: dict = {"ok": False, "errors": [], "consumed": 0.0}
        now = local_now()
        requirement.task_finished_at = now
        started = requirement.task_started_at
        consumed = 0.0
        if started:
            try:
                hmap = get_holiday_map(self.db, started.date(), now.date())
            except Exception:
                hmap = {}
            consumed = work_hours.consumed_hours(started, now, hmap)
        # 禅道要求 currentConsumed > 0
        if consumed <= 0:
            consumed = round(requirement.estimated_test_hours or 1.0, 2)
        out["consumed"] = consumed
        if requirement.zentao_task_id:
            client = self._client_or_error(out)
            if client:
                try:
                    client.finish_task(
                        int(requirement.zentao_task_id),
                        current_consumed=consumed,
                        finished_date=_fmt_dt(now),
                    )
                    requirement.zentao_task_status_cache = "done"
                except Exception as exc:
                    logger.warning("finish task %s failed: %s", requirement.zentao_task_id, exc)
                    out["errors"].append(f"禅道完成任务失败：{exc}")
        self.db.commit()
        out["ok"] = not out["errors"]
        return out

    def reactivate_requirement_task(self, requirement: Requirement) -> dict:
        """取消「测试完成」：禅道 restart（重新激活）。"""
        out: dict = {"ok": False, "errors": []}
        requirement.task_finished_at = None
        left = requirement.estimated_test_hours or 4.0
        if requirement.zentao_task_id:
            client = self._client_or_error(out)
            if client:
                consumed = 0.0
                try:
                    task = client.get_task(int(requirement.zentao_task_id)) or {}
                    consumed = float(task.get("consumed") or 0.0)
                except Exception:
                    consumed = 0.0
                # restart 要求 consumed 必填、left>0
                try:
                    client.restart_task(int(requirement.zentao_task_id), consumed=consumed, left=left)
                    requirement.zentao_task_status_cache = "doing"
                except Exception as exc:
                    logger.warning("restart task %s failed: %s", requirement.zentao_task_id, exc)
                    out["errors"].append(f"禅道重新激活任务失败：{exc}")
        self.db.commit()
        out["ok"] = not out["errors"]
        return out


__all__ = ["ZentaoTaskSyncService"]
