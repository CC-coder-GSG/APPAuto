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
from app.services.zentao_system_client import (
    get_system_zentao_client,
    get_system_zentao_web_login,
    get_user_zentao_client,
    get_user_zentao_web_login,
)
from app.services.zentao_web_session import ZentaoWebSessionError, pause_task_via_web
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


# 禅道侧「已失效」的任务状态：被取消 / 关闭的任务不应再被认领或改派，
# 需求应视为「未建任务」重新创建一条新的子任务。
_DEAD_TASK_STATUSES = {"cancel", "closed"}


def _task_status(t: dict) -> str:
    return str((t or {}).get("status") or "").strip().lower()


def _is_dead_task(t: Optional[dict]) -> bool:
    return _task_status(t) in _DEAD_TASK_STATUSES


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

        # 先拉取执行下现有任务索引（by_id 判活 / by_story 认领），据此分类：
        #   - 本地已关联且禅道侧任务仍存活（非取消/关闭/删除）→ 改派
        #   - 否则清除本地陈旧关联，走「认领同 story 存活任务 / 新建」流程
        # 这样「分配后在禅道取消/关闭了任务，再重新分配」也能正确新建一条新任务。
        by_id, existing_by_story = self._index_existing_test_tasks(client, exec_id)

        reassign_items: list[tuple[Requirement, User]] = []  # 待改派（现有存活任务）
        pending_items: list[tuple[Requirement, User]] = []   # 需认领或新建

        for a in assignments:
            req = req_map.get(a.get("requirement_id"))
            owner = user_map.get(a.get("owner_id"))
            if not req or not owner:
                continue
            live = by_id.get(int(req.zentao_task_id)) if req.zentao_task_id else None
            if req.zentao_task_id and _is_dead_task(live):
                # 禅道侧已被取消/关闭：作废本地关联，按缺失重新建任务。
                # （仅在明确查到 dead 状态时才重建；查不到可能是分页/临时不可见，
                #  仍走改派以避免误重建。）
                logger.info(
                    "req %s 本地关联子任务 #%s 在禅道已%s，将重新建任务",
                    req.id, req.zentao_task_id, _task_status(live),
                )
                req.zentao_task_id = None
                req.zentao_parent_task_id = None
                req.zentao_task_status_cache = None
                req.zentao_task_assigned_to = None
                pending_items.append((req, owner))
            elif req.zentao_task_id:
                reassign_items.append((req, owner))
            else:
                pending_items.append((req, owner))

        # ── 改派已有子任务 ──
        for req, owner in reassign_items:
            acc = self._resolve_account(owner, assignable)
            if not acc:
                out["unassigned"].append({"requirement_id": req.id, "owner_name": owner.shown_name})
                continue
            try:
                client.reassign_task(int(req.zentao_task_id), acc)
                req.zentao_task_assigned_to = acc
                out["reassigned_tasks"].append(req.zentao_task_id)
            except Exception as exc:
                logger.warning("reassign task %s -> %s failed: %s", req.zentao_task_id, acc, exc)
                out["errors"].append(f"改派子任务 #{req.zentao_task_id}（{req.title}）失败：{exc}")

        # ── 认领同 story 的存活任务 + 仅对真正缺失的新建 ──
        # 若某需求的任务此前已建过（常见于子任务很多、单次请求超时导致禅道已建但本地
        # 没写回），本次直接认领写回，绝不重复新建。已取消/关闭的死任务不在 by_story 中，
        # 不会被认领。
        adopt_items: list[tuple[Requirement, User, dict]] = []
        create_new_items: list[tuple[Requirement, User]] = []
        for req, owner in pending_items:
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
            req.zentao_task_assigned_to = cur_acc  # 先按禅道现值缓存，改派成功后再覆盖
            if acc and cur_acc and acc.strip().lower() != cur_acc.strip().lower():
                try:
                    client.reassign_task(tid, acc)
                    req.zentao_task_assigned_to = acc
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
                        req.zentao_task_assigned_to = acc
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

    def _index_existing_test_tasks(self, client, exec_id: int) -> tuple[dict[int, dict], dict[int, dict]]:
        """索引执行下已有的 test 任务，返回 (by_id, by_story)。

        - by_id：按任务 id 索引全部 test 任务（含已取消/关闭），用于按 id 判活。
        - by_story：按 story 索引「存活」(非取消/关闭)的 test 子任务，取同 story 中
          id 最大（最新）的一条，供认领。已取消/关闭的任务不进 by_story，避免把
          禅道上已被取消/关闭的死任务重新认领回来（会导致重新分配时不新建）。
        """
        try:
            rows = client.list_execution_tasks(exec_id) or []
        except Exception as exc:  # noqa: BLE001
            logger.warning("index existing tasks exec %s failed: %s", exec_id, exc)
            return {}, {}
        by_id: dict[int, dict] = {}
        by_story: dict[int, dict] = {}
        for t in rows:
            if not isinstance(t, dict) or str(t.get("type") or "") != "test":
                continue
            tid = _coerce_int(t.get("id"))
            if tid:
                by_id[tid] = t
            sid = _coerce_int(t.get("story"))
            if not sid or not tid:
                continue
            if _is_dead_task(t):
                continue
            # 只认子任务（有 parent），父任务 story 通常为 0、不会进来
            prev = by_story.get(sid)
            if prev is None or tid > _coerce_int(prev.get("id")):
                by_story[sid] = t
        return by_id, by_story

    def sync_tasks_status_for_major(self, major_version_id: int) -> dict:
        """最佳努力：拉取该大版本禅道执行下的任务，回写本地状态缓存 + 指派人账号。

        只更新按 zentao_task_id 命中的需求（禅道为权威）；找不到的（可能被彻底
        删除）不动，避免网络/分页抖动误清关联。任何异常都吞掉，不抛给调用方。
        """
        out: dict = {"updated": 0, "errors": []}
        major = self.db.query(Version).filter(Version.id == major_version_id).first()
        if not major or not major.zentao_execution_id:
            return out
        client = get_system_zentao_client(self.db)
        if not client:
            return out
        by_id, _ = self._index_existing_test_tasks(client, int(major.zentao_execution_id))
        if not by_id:
            return out
        reqs = (
            self.db.query(Requirement)
            .filter(
                Requirement.major_version_id == major_version_id,
                Requirement.zentao_task_id.isnot(None),
            )
            .all()
        )
        changed = False
        for req in reqs:
            t = by_id.get(int(req.zentao_task_id))
            if not t:
                continue
            status = _task_status(t) or None
            acc = _task_account(t)
            if status and req.zentao_task_status_cache != status:
                req.zentao_task_status_cache = status
                changed = True
                out["updated"] += 1
            if acc is not None and req.zentao_task_assigned_to != acc:
                req.zentao_task_assigned_to = acc
                changed = True
        if changed:
            try:
                self.db.commit()
            except Exception as exc:  # noqa: BLE001
                logger.warning("commit task status sync major %s failed: %s", major_version_id, exc)
                self.db.rollback()
        return out

    # ------------------------------------------------------------------
    # 能力 C：开始 / 完成 / 重新激活
    # ------------------------------------------------------------------

    def _client_or_error(self, out: dict, acting_user: Optional[User] = None):
        """优先用「操作人本人」的禅道客户端（正确记录操作人、保持指派不变）；
        无本人绑定时回退系统账号。"""
        client = get_user_zentao_client(acting_user.id, self.db) if acting_user is not None else None
        if client is None:
            client = get_system_zentao_client(self.db)
        if not client:
            out["errors"].append("找不到可用的禅道账号绑定")
        return client

    def _restore_assignee_if_changed(self, client, requirement: Requirement) -> None:
        """兜底：若禅道把指派人清空/改掉（系统账号 start/continue 的已知副作用），
        改派回记录的指派人，避免出现「开始后变未指派」。"""
        want = (requirement.zentao_task_assigned_to or "").strip()
        if not want or not requirement.zentao_task_id:
            return
        try:
            task = client.get_task(int(requirement.zentao_task_id)) or {}
            cur = _task_account(task)
            if (cur or "").strip().lower() != want.lower():
                client.reassign_task(int(requirement.zentao_task_id), want)
        except Exception as exc:  # noqa: BLE001
            logger.warning("restore assignee req task %s -> %s failed: %s", requirement.zentao_task_id, want, exc)

    def start_requirement_task(self, requirement: Requirement, *, hours: Optional[float] = None, acting_user: Optional[User] = None) -> dict:
        """点击「开始」：禅道子任务 start（暂停中的则 restart 继续），记录本地开始时刻。"""
        out: dict = {"ok": False, "errors": []}
        now = local_now()
        requirement.task_started_at = now
        left = hours if hours is not None else (requirement.estimated_test_hours or 4.0)
        if requirement.zentao_task_id:
            client = self._client_or_error(out, acting_user)
            if client:
                assignee = requirement.zentao_task_assigned_to or None
                paused = str(requirement.zentao_task_status_cache or "").strip().lower() == "pause"
                try:
                    if paused:
                        # 继续暂停的任务
                        consumed = 0.0
                        try:
                            consumed = float((client.get_task(int(requirement.zentao_task_id)) or {}).get("consumed") or 0.0)
                        except Exception:
                            consumed = 0.0
                        client.restart_task(int(requirement.zentao_task_id), consumed=consumed, left=left, assigned_to=assignee)
                    else:
                        client.start_task(int(requirement.zentao_task_id), real_started=_fmt_dt(now), left=left, assigned_to=assignee)
                    requirement.zentao_task_status_cache = "doing"
                    self._restore_assignee_if_changed(client, requirement)
                except Exception as exc:
                    logger.warning("start task %s failed: %s", requirement.zentao_task_id, exc)
                    out["errors"].append(f"禅道开始任务失败：{exc}")
        self.db.commit()
        out["ok"] = not out["errors"]
        return out

    def pause_requirement_task(self, requirement: Requirement, *, acting_user: Optional[User] = None, comment: Optional[str] = None) -> dict:
        """点击「暂停」：禅道子任务 pause（status→pause），之后可再「开始」继续。

        本禅道（ipd4.3）REST pause 未实现、Token 页面动作被迭代 ACL 拦截，
        优先走网页 cookie 会话（zentao_web_session），并回读校验状态真的切到 pause。
        """
        out: dict = {"ok": False, "errors": []}
        if requirement.zentao_task_id:
            task_id = int(requirement.zentao_task_id)
            client = self._client_or_error(out, acting_user)
            if client:
                web_logins = []
                if acting_user is not None:
                    web_logins.append(get_user_zentao_web_login(acting_user.id, self.db))
                web_logins.append(get_system_zentao_web_login(self.db))
                paused = False
                last_err: Optional[Exception] = None
                for web in web_logins:
                    if web is None:
                        continue
                    try:
                        pause_task_via_web(web, task_id, comment=comment)
                        paused = True
                        break
                    except ZentaoWebSessionError as exc:
                        last_err = exc
                        logger.warning("pause via web session task %s (%s) failed: %s", task_id, web.account, exc)
                if not paused:
                    # 无网页凭据或网页会话失败 → 尝试 REST（其他禅道版本可用）
                    try:
                        client.pause_task(task_id, comment=comment)
                    except Exception as exc:
                        last_err = exc
                        logger.warning("pause task %s via REST failed: %s", task_id, exc)
                # 回读校验：禅道 200 不代表生效（历史上曾静默失败）
                status = None
                try:
                    status = str((client.get_task(task_id) or {}).get("status") or "").strip().lower()
                except Exception as exc:  # noqa: BLE001
                    logger.warning("verify pause task %s failed: %s", task_id, exc)
                if status == "pause":
                    requirement.zentao_task_status_cache = "pause"
                    self._restore_assignee_if_changed(client, requirement)
                else:
                    detail = f"：{last_err}" if last_err else f"（任务当前状态为「{status or '未知'}」）"
                    out["errors"].append(f"禅道暂停任务未生效{detail}")
        self.db.commit()
        out["ok"] = not out["errors"]
        return out

    def finish_requirement_task(self, requirement: Requirement, *, acting_user: Optional[User] = None) -> dict:
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
            client = self._client_or_error(out, acting_user)
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

    def reactivate_requirement_task(self, requirement: Requirement, *, acting_user: Optional[User] = None) -> dict:
        """取消「测试完成」：禅道 restart（重新激活）。"""
        out: dict = {"ok": False, "errors": []}
        requirement.task_finished_at = None
        left = requirement.estimated_test_hours or 4.0
        if requirement.zentao_task_id:
            client = self._client_or_error(out, acting_user)
            if client:
                consumed = 0.0
                try:
                    task = client.get_task(int(requirement.zentao_task_id)) or {}
                    consumed = float(task.get("consumed") or 0.0)
                except Exception:
                    consumed = 0.0
                # restart 要求 consumed 必填、left>0
                try:
                    client.restart_task(int(requirement.zentao_task_id), consumed=consumed, left=left, assigned_to=(requirement.zentao_task_assigned_to or None))
                    requirement.zentao_task_status_cache = "doing"
                    self._restore_assignee_if_changed(client, requirement)
                except Exception as exc:
                    logger.warning("restart task %s failed: %s", requirement.zentao_task_id, exc)
                    out["errors"].append(f"禅道重新激活任务失败：{exc}")
        self.db.commit()
        out["ok"] = not out["errors"]
        return out


__all__ = ["ZentaoTaskSyncService"]
