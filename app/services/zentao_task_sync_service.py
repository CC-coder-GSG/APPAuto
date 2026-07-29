"""测试管理系统「分配」↔ 禅道「任务」联动编排（2026-06-29 需求，能力 A/C）。

设计原则（与 zentao_version_diff / build_push 一致）：
  - 所有禅道写操作走 get_system_zentao_client（优先 chenwenbo）。
  - 单条失败不阻塞整批；任何禅道异常都收进 errors，不向路由抛。
  - 禅道为权威，本地回写 zentao_task_id / parent / 状态缓存做镜像。

能力 A（分配 → 建任务）：
  「分配并发布」在该执行下建测试父任务，子任务**只含本次新分配、此前没建过
  任务的需求**（requirement.zentao_task_id 为空）；已有任务但换了负责人的需求
  直接改派其现有子任务。增量分配（2026-07-07）：执行下已有同名存活父任务时
  复用它挂新子任务，整个版本只保留一个「{版本号} 测试任务」父任务。

能力 C（开始/完成/重新激活）：
  start_requirement_task / finish_requirement_task / reactivate_requirement_task，
  工时按 work_hours 计算后回写禅道。
"""
from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models import Requirement, User, Version
from app.models.zentao_task_mirror import ZentaoTaskMirror
from app.services.holiday_service import get_holiday_map
from app.services.sse_service import sse_publish
from app.services.zentao_system_client import (
    get_system_zentao_client,
    get_system_zentao_web_login,
    get_user_zentao_client,
    get_user_zentao_web_login,
)
from app.services.zentao_task_status import effective_task_status, raw_task_status
from app.services.zentao_effort_service import AUTO_NOTE, merge_extra_hours, submit_day_efforts
from app.services.zentao_web_session import (
    ZentaoWebSessionError,
    finish_task_via_web,
    pause_task_via_web,
    restart_task_via_web,
    start_task_via_web,
)
from app.utils import work_hours
from app.utils.task_naming import ensure_test_prefix
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
    return raw_task_status(t)


def _response_confirms_status(response, expected: str) -> bool:
    """Return whether a write response itself explicitly confirms a task status."""
    if not isinstance(response, dict):
        return False
    candidates = (response, response.get("data"), response.get("task"))
    return any(
        isinstance(item, dict) and effective_task_status(item) == expected
        for item in candidates
    )


def _is_dead_task(t: Optional[dict]) -> bool:
    return _task_status(t) in _DEAD_TASK_STATUSES


class ZentaoTaskSyncService:
    def __init__(self, db: Session):
        self.db = db

    def _update_task_mirror_status(
        self,
        requirement: Requirement,
        status: str,
        *,
        assigned_to: Optional[str] = None,
        assignee: Optional[User] = None,
    ) -> None:
        """Keep the task-board mirror aligned after a confirmed requirement action."""
        if not requirement.zentao_task_id:
            return
        row = (
            self.db.query(ZentaoTaskMirror)
            .filter(ZentaoTaskMirror.task_id == int(requirement.zentao_task_id))
            .first()
        )
        if row is None:
            return
        row.status = status
        if assigned_to is not None:
            row.assigned_to = assigned_to or None
            row.assignee_user_id = assignee.id if assignee is not None else None
            row.assigned_to_realname = assignee.shown_name if assignee is not None else None
        row.synced_at = local_now()
        # The requirement workbench and task board are two views of the same
        # Zentao task. Keep lifecycle bookkeeping aligned too; otherwise a
        # stale board row can submit the same running segment a second time.
        row.local_started_at = requirement.task_started_at
        row.consumed_accum = float(requirement.task_consumed_accum or 0.0)
        row.efforts_submitted = float(requirement.task_efforts_submitted or 0.0)
        if status in {"doing", "changed"} and row.real_started is None and requirement.task_started_at:
            row.real_started = requirement.task_started_at
        if status == "done" and requirement.task_finished_at:
            row.finished_date = requirement.task_finished_at

    @staticmethod
    def _publish_task_change(requirement: Requirement, status: str, action: str) -> None:
        if not requirement.zentao_task_id:
            return
        sse_publish(
            "zentao_task_changed",
            {
                "task_id": int(requirement.zentao_task_id),
                "requirement_id": requirement.id,
                "status": status,
                "action": action,
                "source": "requirement_workbench",
            },
            channels=["global"],
        )

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

    def resolve_requirement_owner_account(
        self,
        requirement: Requirement,
        *,
        acting_user: Optional[User] = None,
    ) -> str:
        """Resolve the requirement owner's Zentao account for reactivation."""
        owner = requirement.owner
        if owner is None and requirement.owner_id:
            owner = self.db.query(User).filter(User.id == requirement.owner_id).first()
        if owner is None:
            raise HTTPException(status_code=400, detail="需求未设置负责人，无法重新指派禅道任务")

        configured = (owner.zentao_account or "").strip()
        if configured:
            return configured

        major = requirement.major_version
        if major is None and requirement.major_version_id:
            major = self.db.query(Version).filter(Version.id == requirement.major_version_id).first()
        execution_id = getattr(major, "zentao_execution_id", None)
        if execution_id:
            acting = get_user_zentao_client(acting_user.id, self.db) if acting_user is not None else None
            system = get_system_zentao_client(self.db)
            clients = [client for client in (acting, system) if client is not None]
            seen: set[int] = set()
            for client in clients:
                marker = id(client)
                if marker in seen:
                    continue
                seen.add(marker)
                try:
                    account = self._resolve_account(
                        owner,
                        client.list_assignable_users(int(execution_id)) or {},
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "resolve requirement %s owner in execution %s failed: %s",
                        requirement.id,
                        execution_id,
                        exc,
                    )
                    continue
                if account:
                    return account

        raise HTTPException(
            status_code=400,
            detail=f"需求负责人“{owner.shown_name}”未绑定禅道账号，无法重新指派任务",
        )

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
            "reused_parent": False,  # True=挂进执行下已有的同名父任务（增量分配）
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

        # ── 真正缺失的：复用/新建父任务 + 逐条子任务，每条建完即提交 ──
        # 增量分配（2026-07-07）：执行下已有同名存活父任务时直接复用，把本批
        # 新子任务挂进去——整个版本始终只有一个「测试任务」父任务，而不是每批一个。
        parent_id: Optional[int] = None
        if create_new_items:
            # [测试] 前缀是 2026-07-08 起的命名规范；复用匹配时兼容之前建的
            # 无前缀父任务，避免增量分配因改名再多出一个父任务。
            legacy_parent_name = f"{major.version_no} 测试任务"
            parent_name = ensure_test_prefix(legacy_parent_name)
            reuse = None
            for t in by_id.values():
                if _is_dead_task(t) or str(t.get("name") or "").strip() not in (parent_name, legacy_parent_name):
                    continue
                # 只认顶层任务（parent<=0），避免误把某个子任务当父容器
                if (_coerce_int(t.get("parent")) or 0) > 0:
                    continue
                if reuse is None or (_coerce_int(t.get("id")) or 0) > (_coerce_int(reuse.get("id")) or 0):
                    reuse = t
            if reuse is not None:
                parent_id = _coerce_int(reuse.get("id"))
                out["reused_parent"] = True
                logger.info("assignment reuses existing parent task #%s (%s)", parent_id, parent_name)
            else:
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
                    child_name = ensure_test_prefix((req.title or f"需求 {req.zentao_req_id}")[:180])
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
            status = effective_task_status(t) or None
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

    def _operate_task_with_verify(
        self,
        requirement: Requirement,
        *,
        expected: str,
        accepted_statuses: Optional[set[str]] = None,
        op,
        acting_user: Optional[User],
        out: dict,
        zh: str,
        restore_assignee: bool = True,
        web_op=None,
        accept_response_status: bool = False,
        desired_assignee: Optional[str] = None,
        desired_assignee_user: Optional[User] = None,
    ) -> bool:
        """对候选账号执行 op(client)，回读校验任务状态。

        账号顺序（2026-07-17 操作人归属修复）：本人 REST → 本人网页 cookie 会话
        （web_op，传入时）→ 系统账号 REST → 系统账号网页会话。此前本人 token 间歇
        失效时直接落到系统账号，禅道动作历史显示成系统账号操作（"陈文博开始了任务"）；
        现在网页兜底保证操作人归属本人。

        禅道 ipd4.3 已知坑：本人 token 被当 guest 时 REST 返回 200 但不生效；
        网络超时时也可能「抛错但已生效」。因此无论 op 成败都回读，状态真切到
        expected 才写缓存并返回成功，否则换下一级重试。全部未生效时不改本地
        状态；后续由正常同步任务刷新，避免一次失败操作反而推动本地状态变化。
        """
        task_id = int(requirement.zentao_task_id)
        acting = get_user_zentao_client(acting_user.id, self.db) if acting_user is not None else None
        system = get_system_zentao_client(self.db)
        candidates: list[tuple[str, object]] = []
        if acting is not None:
            candidates.append(("self", acting))
        if system is not None and system is not acting:
            candidates.append(("system", system))
        if not candidates:
            out["errors"].append("找不到可用的禅道账号绑定")
            return False
        web_logins: dict = {}
        if web_op is not None:
            web_logins["self"] = get_user_zentao_web_login(acting_user.id, self.db) if acting_user is not None else None
            web_logins["system"] = get_system_zentao_web_login(self.db)
        last_err: Optional[Exception] = None
        actual = ""
        target_status_seen = False
        last_assignee = ""
        accepted = {
            str(status or "").strip().lower()
            for status in (accepted_statuses or {expected})
            if str(status or "").strip()
        }
        accepted.add(str(expected or "").strip().lower())

        def _matches(status: str) -> bool:
            return str(status or "").strip().lower() in accepted

        def _readback(client) -> str:
            try:
                return effective_task_status(client.get_task(task_id) or {})
            except Exception as exc:  # noqa: BLE001
                logger.warning("readback task %s after %s failed: %s", task_id, zh, exc)
                return ""

        def _ensure_desired_assignee(client) -> bool:
            nonlocal last_err, last_assignee
            want = (desired_assignee or "").strip()
            if not want:
                return True
            try:
                task = client.get_task(task_id) or {}
                current = (_task_account(task) or "").strip()
                last_assignee = current
                if current.lower() == want.lower():
                    requirement.zentao_task_assigned_to = want
                    return True
                client.reassign_task(task_id, want)
                task = client.get_task(task_id) or {}
                current = (_task_account(task) or "").strip()
                last_assignee = current
                if current.lower() != want.lower():
                    return False
                requirement.zentao_task_assigned_to = want
                return True
            except Exception as exc:  # noqa: BLE001
                last_err = exc
                logger.warning(
                    "ensure task %s assignee %s after %s failed: %s",
                    task_id,
                    want,
                    zh,
                    exc,
                )
                return False

        def _succeed(client, actual_status: str = "") -> bool:
            confirmed_status = str(actual_status or expected).strip().lower()
            requirement.zentao_task_status_cache = confirmed_status
            if desired_assignee:
                if not _ensure_desired_assignee(client):
                    return False
                self._update_task_mirror_status(
                    requirement,
                    confirmed_status,
                    assigned_to=(desired_assignee or "").strip(),
                    assignee=desired_assignee_user,
                )
            else:
                self._update_task_mirror_status(requirement, confirmed_status)
            if restore_assignee and not desired_assignee:
                self._restore_assignee_if_changed(client, requirement)
            return True

        for label, client in candidates:
            response = None
            # A previous attempt may have reached Zentao even if its response
            # or immediate readback failed (notably after a 401). Do not repeat
            # the lifecycle write when this candidate already sees the target.
            actual = _readback(client)
            if _matches(actual):
                target_status_seen = True
                if _succeed(client, actual):
                    return True
                continue
            try:
                response = op(client)
            except Exception as exc:  # noqa: BLE001
                last_err = exc
                logger.warning("%s task %s via %s REST failed: %s", zh, task_id, label, exc)
            # finish writes currentConsumed. When REST explicitly confirms the
            # final status, an immediately stale GET must not trigger a second
            # finish through the web fallback.
            if accept_response_status and _response_confirms_status(response, expected):
                return _succeed(client)
            actual = _readback(client)
            if _matches(actual):
                target_status_seen = True
                if _succeed(client, actual):
                    return True
                continue
            # REST 未生效 → 同账号网页 cookie 会话兜底（操作人归属不变）
            web = web_logins.get(label)
            if web_op is not None and web is not None:
                try:
                    web_op(web)
                except Exception as exc:  # noqa: BLE001
                    last_err = exc
                    logger.warning("%s task %s via %s web session failed: %s", zh, task_id, label, exc)
                actual = _readback(client)
                if _matches(actual):
                    target_status_seen = True
                    if _succeed(client, actual):
                        return True
                    continue
        if desired_assignee and target_status_seen:
            detail = f"：{last_err}" if last_err else ""
            current = f"（当前指派人：{last_assignee or '未知'}）"
            out["errors"].append(
                f"禅道{zh}任务已生效，但未能指派给需求负责人{current}{detail}"
            )
            return False
        detail = f"：{last_err}" if last_err else "（禅道返回成功但状态未切换）"
        out["errors"].append(f"禅道{zh}任务未生效{detail}")
        return False

    def start_requirement_task(self, requirement: Requirement, *, hours: Optional[float] = None, acting_user: Optional[User] = None) -> dict:
        """点击「开始」：禅道子任务 start（暂停中的则 restart 继续），记录本地开始时刻。

        工时口径：暂停期不计工时。继续暂停任务时保留已结算的累计工时
        （task_consumed_accum），只重置本段计时起点；全新开始则连累计一起清零。
        """
        out: dict = {"ok": False, "errors": []}
        now = local_now()
        paused = str(requirement.zentao_task_status_cache or "").strip().lower() == "pause"
        confirmed = not requirement.zentao_task_id
        left = hours if hours is not None else (requirement.estimated_test_hours or 4.0)
        if requirement.zentao_task_id:
            task_id = int(requirement.zentao_task_id)
            assignee = requirement.zentao_task_assigned_to or None

            def _op(client):
                if paused:
                    # 继续暂停的任务
                    consumed = 0.0
                    try:
                        consumed = float((client.get_task(task_id) or {}).get("consumed") or 0.0)
                    except Exception:
                        consumed = 0.0
                    client.restart_task(task_id, consumed=consumed, left=left, assigned_to=assignee)
                else:
                    client.start_task(task_id, real_started=_fmt_dt(now), left=left, assigned_to=assignee)

            def _web_op(web):
                # 网页表单的 consumed 有当前消耗预填值，restart 传 None 沿用即可
                if paused:
                    restart_task_via_web(web, task_id, left=left, assigned_to=assignee)
                else:
                    start_task_via_web(web, task_id, left=left, real_started=_fmt_dt(now))

            confirmed = self._operate_task_with_verify(
                requirement, expected="doing", op=_op, acting_user=acting_user, out=out, zh="开始", web_op=_web_op
            )
        if confirmed:
            if not paused:
                requirement.task_consumed_accum = 0.0
            requirement.task_started_at = now
            # _operate_task_with_verify 先确认远端状态；计时字段写入后再补齐镜像时间。
            self._update_task_mirror_status(requirement, "doing")
        self.db.commit()
        out["ok"] = not out["errors"]
        if out["ok"]:
            self._publish_task_change(requirement, "doing", "start")
        return out

    def pause_requirement_task(self, requirement: Requirement, *, acting_user: Optional[User] = None, comment: Optional[str] = None) -> dict:
        """点击「暂停」：禅道子任务 pause（status→pause），之后可再「开始」继续。

        REST 优先（ipd4.3 空 body 会被静默忽略，pause_task 已固定带 comment 字段）；
        REST 未生效（如 token 被当 guest）再回退网页 cookie 会话（zentao_web_session）。
        始终回读校验状态真的切到 pause。

        工时口径：暂停生效时把「本段开始→暂停」结算进 task_consumed_accum 并清空
        task_started_at，保证暂停到再次开始之间的时间不计工时。

        ⚠️ 分段工时必须在暂停「之前」提交：禅道对 pause 状态任务记工时会把状态自动
        置回 doing（2026-07-17 线上实证：暂停几秒后被"自动激活"，动作备注即工时记录
        的 work 文案）。先在 doing 状态提交（不改状态），再执行暂停；提交成功后把
        计时起点重置到当前时刻——暂停未生效时计时自然续跑、不会重复计入已提交段。
        """
        out: dict = {"ok": False, "errors": []}
        now = local_now()
        # Pause is idempotent across both UI entry points.  Check Zentao before
        # creating effort rows: the previous request may already have paused
        # the task even though its response/readback ended with a 401.
        if requirement.zentao_task_id:
            task_id = int(requirement.zentao_task_id)
            acting = get_user_zentao_client(acting_user.id, self.db) if acting_user is not None else None
            system = get_system_zentao_client(self.db)
            precheck_clients = []
            if acting is not None:
                precheck_clients.append(acting)
            if system is not None and system is not acting:
                precheck_clients.append(system)
            for candidate in precheck_clients:
                try:
                    current_status = effective_task_status(candidate.get_task(task_id) or {})
                except Exception as exc:  # noqa: BLE001
                    logger.warning("precheck pause task %s failed: %s", task_id, exc)
                    continue
                if current_status == "pause":
                    requirement.zentao_task_status_cache = "pause"
                    # Reconcile a stale local clock without another external
                    # effort. Do not force 0.1h: an earlier attempt may have
                    # reset this clock only milliseconds ago after submitting.
                    self._settle_consumed_segment(requirement, now, minimum=False)
                    self._update_task_mirror_status(requirement, "pause")
                    self._restore_assignee_if_changed(candidate, requirement)
                    self.db.commit()
                    out["ok"] = True
                    self._publish_task_change(requirement, "pause", "pause")
                    return out
                if current_status:
                    break
        presubmitted = self._try_submit_requirement_efforts(requirement, acting_user, now)
        if presubmitted is not None:
            requirement.task_efforts_submitted = round(float(requirement.task_efforts_submitted or 0.0) + presubmitted, 2)
            requirement.task_consumed_accum = 0.0
            requirement.task_started_at = now
        if requirement.zentao_task_id:
            task_id = int(requirement.zentao_task_id)
            client = self._client_or_error(out, acting_user)
            if client:
                last_err: Optional[Exception] = None
                verify_clients = [client]
                system_verify = get_system_zentao_client(self.db)
                if system_verify is not None and system_verify is not client:
                    verify_clients.append(system_verify)

                def _paused() -> bool:
                    for verify_client in verify_clients:
                        try:
                            if str((verify_client.get_task(task_id) or {}).get("status") or "").strip().lower() == "pause":
                                return True
                        except Exception as exc:  # noqa: BLE001
                            logger.warning("verify pause task %s failed: %s", task_id, exc)
                    return False

                try:
                    client.pause_task(task_id, comment=comment)
                except Exception as exc:  # noqa: BLE001
                    last_err = exc
                    logger.warning("pause task %s via REST failed: %s", task_id, exc)
                if not _paused():
                    web_logins = []
                    if acting_user is not None:
                        web_logins.append(get_user_zentao_web_login(acting_user.id, self.db))
                    web_logins.append(get_system_zentao_web_login(self.db))
                    for web in web_logins:
                        if web is None:
                            continue
                        try:
                            pause_task_via_web(web, task_id, comment=comment)
                            break
                        except ZentaoWebSessionError as exc:
                            last_err = exc
                            logger.warning("pause via web session task %s (%s) failed: %s", task_id, web.account, exc)
                # 回读校验：禅道 200 不代表生效（历史上曾静默失败）
                if _paused():
                    requirement.zentao_task_status_cache = "pause"
                    self._update_task_mirror_status(requirement, "pause")
                    self._restore_assignee_if_changed(client, requirement)
                else:
                    detail = f"：{last_err}" if last_err else "（禅道返回成功但状态未切换）"
                    out["errors"].append(f"禅道暂停任务未生效{detail}")
        if not out["errors"]:
            if presubmitted is not None:
                requirement.task_started_at = None  # 本段已在暂停前提交，暂停生效后停表
            else:
                # 无本人网页凭据/提交失败 → 旧口径：本地累计，完成时一次性提交
                self._settle_consumed_segment(requirement, now)
            # The verification helper updates the visible mirror status before
            # these fields are finalized; copy the finalized tracking state too.
            self._update_task_mirror_status(requirement, "pause")
        self.db.commit()
        out["ok"] = not out["errors"]
        if out["ok"]:
            self._publish_task_change(requirement, "pause", "pause")
        return out

    def _settle_consumed_segment(self, requirement: Requirement, now, *, minimum: bool = True) -> None:
        """兜底口径：把「本段开始 → now」的工时结算进 task_consumed_accum 并清空计时起点。"""
        started = requirement.task_started_at
        if not started:
            return
        try:
            hmap = get_holiday_map(self.db, started.date(), now.date())
        except Exception:
            hmap = {}
        segment = work_hours.consumed_hours(started, now, hmap)
        if segment <= 0 and minimum:
            segment = 0.1
        requirement.task_consumed_accum = round(float(requirement.task_consumed_accum or 0.0) + segment, 2)
        requirement.task_started_at = None

    def _split_segment_by_day(self, started, ended) -> list[tuple[date, float]]:
        """本段计时按天拆分 [(日期, 小时)]（工作日全天计、周末节假日跳过）。"""
        if not started or ended <= started:
            return []
        try:
            hmap = get_holiday_map(self.db, started.date(), ended.date())
        except Exception:
            hmap = {}
        return work_hours.consumed_hours_by_day(started, ended, hmap)

    def _task_left_hours(self, task_id: int, fallback: float) -> float:
        """任务当前剩余工时（工时记录的 left 列递减起点），拉不到用 fallback。"""
        client = get_system_zentao_client(self.db)
        if client is None:
            return fallback
        try:
            return float((client.get_task(task_id) or {}).get("left") or fallback)
        except Exception:  # noqa: BLE001
            return fallback

    def _try_submit_requirement_efforts(self, requirement: Requirement, acting_user: Optional[User], now) -> Optional[float]:
        """暂停：本段（+ 旧口径遗留的未提交累计，补录到今天）按天拆分提交为禅道
        工时记录。成功返回提交小时数（可为 0）；没有任务/无本人网页凭据/提交失败
        返回 None，调用方退回本地累计口径。

        ⚠️ 工时记录归属禅道当前登录人，只能用本人凭据，不回退系统账号。"""
        if not requirement.zentao_task_id:
            return None
        started = requirement.task_started_at
        day_rows = self._split_segment_by_day(started, now)
        if started and not day_rows:
            day_rows = [(now.date(), 0.1)]
        day_rows = merge_extra_hours(day_rows, float(requirement.task_consumed_accum or 0.0), now.date())
        if not day_rows:
            return 0.0
        login = get_user_zentao_web_login(acting_user.id, self.db) if acting_user is not None else None
        if login is None:
            logger.info("requirement %s pause: no self web login, fall back to local accum", requirement.id)
            return None
        task_id = int(requirement.zentao_task_id)
        try:
            return submit_day_efforts(
                login, task_id, day_rows,
                left_before=self._task_left_hours(task_id, requirement.estimated_test_hours or 0.0),
                note=AUTO_NOTE,
            )
        except Exception as exc:  # noqa: BLE001 — 提交失败退回本地累计，不阻塞暂停
            logger.warning("requirement %s pause: submit efforts failed, fall back to local accum: %s", requirement.id, exc)
            return None

    def _prepare_requirement_finish_consumed(
        self,
        requirement: Requirement,
        acting_user: Optional[User],
        now,
        *,
        explicit: Optional[float] = None,
    ) -> float:
        """完成时的 currentConsumed：优先把「今天之前」的段按天拆分提交为工时记录，
        finish 只带今天的部分 + 未提交累计；无本人网页凭据/提交失败退回一次性提交
        全部。禅道要求 >0，已计时但不足一分钟按 0.1；完全没有计时依据时要求
        调用方提供人工确认的实际工时。"""
        if explicit is not None:
            return round(float(explicit), 2)
        started = requirement.task_started_at
        day_rows = self._split_segment_by_day(started, now)
        today = now.date()
        prev_rows = [(d, h) for d, h in day_rows if d < today]
        remainder = round(
            sum(h for d, h in day_rows if d >= today) + float(requirement.task_consumed_accum or 0.0), 2
        )
        if prev_rows and requirement.zentao_task_id and acting_user is not None:
            login = get_user_zentao_web_login(acting_user.id, self.db)
            if login is not None:
                task_id = int(requirement.zentao_task_id)
                try:
                    submitted = submit_day_efforts(
                        login, task_id, prev_rows,
                        left_before=self._task_left_hours(task_id, requirement.estimated_test_hours or 0.0),
                        note=AUTO_NOTE,
                    )
                    requirement.task_efforts_submitted = round(float(requirement.task_efforts_submitted or 0.0) + submitted, 2)
                    # finish 未生效时 remainder 留在 accum 里可重试；生效后簿记清零
                    requirement.task_consumed_accum = remainder
                    requirement.task_started_at = None
                    return max(remainder, 0.1)
                except Exception as exc:  # noqa: BLE001 — 拆分提交失败退回一次性
                    logger.warning("requirement %s finish: pre-submit efforts failed, fall back to lump sum: %s", requirement.id, exc)
        total = round(remainder + sum(h for _, h in prev_rows), 2)
        if total > 0:
            return max(total, 0.1)
        if float(requirement.task_efforts_submitted or 0.0) > 0:
            # 各段都已提交过工时记录 → 不能再拿预计工时兜底（会重复计入），给最小值
            return 0.1
        if started is not None:
            return 0.1
        raise HTTPException(
            status_code=400,
            detail="未检测到平台计时记录，请确认实际工时后再完成任务",
        )

    def finish_requirement_task(
        self,
        requirement: Requirement,
        *,
        acting_user: Optional[User] = None,
        consumed: Optional[float] = None,
    ) -> dict:
        """勾「测试完成」：算工时 → 禅道 finish，记录本地完成时刻。

        总工时 = 暂停时结算的累计（task_consumed_accum）+ 最后一段（开始→完成）；
        暂停中直接完成时只有累计部分，暂停期不计入。
        """
        out: dict = {"ok": False, "errors": [], "consumed": 0.0}
        now = local_now()
        confirmed = not requirement.zentao_task_id
        if consumed is not None and (consumed <= 0 or consumed > 999):
            raise HTTPException(status_code=400, detail="实际工时需在 0~999 小时之间")
        # 今天之前的段先按天拆分提交为工时记录，finish 只带今天的部分；
        # 无本人网页凭据/提交失败则一次性提交全部（旧口径兜底）。
        consumed_hours = self._prepare_requirement_finish_consumed(
            requirement, acting_user, now, explicit=consumed
        )
        out["consumed"] = consumed_hours
        if requirement.zentao_task_id:
            task_id = int(requirement.zentao_task_id)

            def _op(client):
                return client.finish_task(task_id, current_consumed=consumed_hours, finished_date=_fmt_dt(now))

            def _web_op(web):
                finish_task_via_web(web, task_id, current_consumed=consumed_hours, finished_date=_fmt_dt(now))

            # 完成后禅道会把指派人转给任务创建者，属预期流转，不做指派校正
            confirmed = self._operate_task_with_verify(
                requirement, expected="done", op=_op, acting_user=acting_user, out=out, zh="完成",
                restore_assignee=False, web_op=_web_op, accept_response_status=True,
            )
        if confirmed:
            requirement.task_finished_at = now
            # finish 的 currentConsumed 已随完成上报（禅道生成完成当天的工时记录）
            requirement.task_efforts_submitted = round(
                float(requirement.task_efforts_submitted or 0.0) + consumed_hours, 2
            )
            requirement.task_consumed_accum = 0.0
            requirement.task_started_at = None
            self._update_task_mirror_status(requirement, "done")
        self.db.commit()
        out["ok"] = not out["errors"]
        if out["ok"]:
            self._publish_task_change(requirement, "done", "finish")
        return out

    def reactivate_requirement_task(
        self,
        requirement: Requirement,
        *,
        acting_user: Optional[User] = None,
        desired_assignee: Optional[str] = None,
        desired_assignee_user: Optional[User] = None,
    ) -> dict:
        """取消「测试完成」：禅道 restart（重新激活）。

        工时口径：重新激活后从零起算新计时段（禅道 finish 的 currentConsumed 是
        增量，会累加到禅道已有总耗时上；本地保留旧值会导致再次完成时重复上报）。
        """
        out: dict = {"ok": False, "errors": []}
        now = local_now()
        confirmed = not requirement.zentao_task_id
        left = requirement.estimated_test_hours or 4.0
        if requirement.zentao_task_id:
            task_id = int(requirement.zentao_task_id)
            wanted = (desired_assignee or "").strip()

            def _op(client):
                consumed = 0.0
                current_assignee = ""
                try:
                    task = client.get_task(task_id) or {}
                    consumed = float(task.get("consumed") or 0.0)
                    current_assignee = (_task_account(task) or "").strip()
                except Exception:
                    consumed = 0.0
                # restart 要求 consumed 必填、left>0
                if wanted:
                    restart_assignee = wanted if current_assignee.lower() != wanted.lower() else None
                else:
                    restart_assignee = requirement.zentao_task_assigned_to or None
                client.restart_task(
                    task_id,
                    consumed=consumed,
                    left=left,
                    assigned_to=restart_assignee,
                )

            def _web_op(web):
                restart_task_via_web(
                    web,
                    task_id,
                    left=left,
                    assigned_to=wanted or (requirement.zentao_task_assigned_to or None),
                )

            confirmed = self._operate_task_with_verify(
                requirement,
                expected="doing",
                accepted_statuses={"doing", "changed"},
                op=_op,
                acting_user=acting_user,
                out=out,
                zh="重新激活",
                web_op=_web_op,
                desired_assignee=wanted or None,
                desired_assignee_user=desired_assignee_user,
            )
        if confirmed:
            requirement.task_finished_at = None
            requirement.task_started_at = now
            requirement.task_consumed_accum = 0.0
            active_status = str(requirement.zentao_task_status_cache or "doing").strip().lower()
            self._update_task_mirror_status(requirement, active_status)
        self.db.commit()
        out["ok"] = not out["errors"]
        if out["ok"]:
            active_status = str(requirement.zentao_task_status_cache or "doing").strip().lower()
            self._publish_task_change(requirement, active_status, "reactivate")
        return out


__all__ = ["ZentaoTaskSyncService"]
