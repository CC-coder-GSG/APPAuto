"""禅道任务 → 本地镜像缓存（任务看板接入禅道，能力 D）。

后台 job 周期性遍历已绑定执行、拉任务、upsert 到 zentao_task_mirror；
任务看板从镜像读取，避免每次开看板都实时全量拉禅道。
"""
from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.models import Requirement, User, Version, VersionType
from app.models.zentao_task_mirror import ZentaoTaskMirror
from app.services.holiday_service import get_holiday_map
from app.services.sse_service import sse_publish
from app.utils import work_hours
from app.services.zentao_system_client import (
    get_system_zentao_client,
    get_system_zentao_web_login,
    get_user_zentao_client,
    get_user_zentao_web_login,
)
from app.services.zentao_effort_service import AUTO_NOTE, merge_extra_hours, submit_day_efforts
from app.services.zentao_web_session import (
    ZentaoWebSessionError,
    cancel_task_via_web,
    close_task_via_web,
    finish_task_via_web,
    pause_task_via_web,
    restart_task_via_web,
    start_task_via_web,
)
from app.utils.task_naming import ensure_test_prefix
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


def _response_confirms_status(response, expected: str) -> bool:
    """Return whether a write response itself explicitly confirms a task status."""
    if not isinstance(response, dict):
        return False
    candidates = (response, response.get("data"), response.get("task"))
    return any(
        isinstance(item, dict)
        and str(item.get("status") or "").strip().lower() == expected
        for item in candidates
    )


class ZentaoTaskMirrorService:
    def __init__(self, db: Session):
        self.db = db

    def _linked_requirement(self, task_id: int) -> Optional[Requirement]:
        return (
            self.db.query(Requirement)
            .filter(Requirement.zentao_task_id == int(task_id))
            .first()
        )

    @staticmethod
    def _hydrate_tracking_from_requirement(row: ZentaoTaskMirror, requirement: Optional[Requirement]) -> None:
        """Use one clock/effort ledger for linked tasks in both UI views."""
        if requirement is None:
            return
        row.local_started_at = requirement.task_started_at
        row.consumed_accum = float(requirement.task_consumed_accum or 0.0)
        row.efforts_submitted = float(requirement.task_efforts_submitted or 0.0)

    @staticmethod
    def _sync_requirement_from_mirror(row: ZentaoTaskMirror, requirement: Optional[Requirement]) -> None:
        if requirement is None:
            return
        requirement.zentao_task_status_cache = row.status
        requirement.zentao_task_assigned_to = row.assigned_to
        requirement.task_started_at = row.local_started_at
        requirement.task_consumed_accum = float(row.consumed_accum or 0.0)
        requirement.task_efforts_submitted = float(row.efforts_submitted or 0.0)
        status = str(row.status or "").strip().lower()
        if status == "done":
            requirement.task_finished_at = row.finished_date
        elif status == "doing":
            requirement.task_finished_at = None

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
            fb_acc, fb_name = _account_of(t.get("finishedBy"))
            row.finished_by = fb_acc
            row.finished_by_realname = fb_name or (t.get("finishedByRealName") if isinstance(t.get("finishedByRealName"), str) else None) or row.finished_by_realname
            row.estimate = _as_float(t.get("estimate"))
            row.consumed = _as_float(t.get("consumed"))
            row.left = _as_float(t.get("left"))
            row.est_started = _parse_date(t.get("estStarted"))
            row.deadline = _parse_date(t.get("deadline"))
            row.real_started = _parse_dt(t.get("realStarted"))
            # 禅道 left=0 工时触发的自动完成不写 finishedDate → 任务仍是完成态时，
            # 别用空值冲掉镜像里已兜底的完成时间（看板延期归列全靠它）
            _fin = _parse_dt(t.get("finishedDate"))
            if _fin is not None or str(t.get("status") or "").strip().lower() not in {"done", "closed"}:
                row.finished_date = _fin
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
        """任务工作台数据：当前用户名下的任务（含父任务），并标注是否关联本平台需求。

        关联判定：优先按 requirements.zentao_task_id == task_id；否则按
        requirements.zentao_story_id == task.story。命中则带上需求 id / 编号 /
        大版本，供前端「跳转到需求工作台对应位置」。

        父任务同步在其指派人名下展示（2026-07-06）：
        - 父任务带 children（全部子任务，含他人的）与完成进度，前端嵌套展示；
        - 子任务带 parent_info，前端标注所属父任务（与禅道层级一致）。
        """
        from sqlalchemy.orm import joinedload

        from app.models import Requirement

        tasks = self.list_tasks(scope="mine", current_user_id=current_user.id, include_parents=True)
        if not tasks:
            return []

        # 父子关系：我的父任务 → 其全部子任务；我的子任务 → 其父任务概要。
        my_parent_ids = [t["task_id"] for t in tasks if t.get("is_parent")]
        children_map: dict[int, list[dict]] = {}
        if my_parent_ids:
            child_rows = (
                self.db.query(ZentaoTaskMirror)
                .filter(ZentaoTaskMirror.parent.in_(my_parent_ids))
                .order_by(ZentaoTaskMirror.task_id.asc())
                .all()
            )
            for c in child_rows:
                children_map.setdefault(int(c.parent), []).append(self._serialize(c))
        referenced_parent_ids = {int(t["parent"]) for t in tasks if _coerce_int(t.get("parent")) and int(t["parent"]) > 0}
        parents_map: dict[int, dict] = {}
        if referenced_parent_ids:
            for p in self.db.query(ZentaoTaskMirror).filter(ZentaoTaskMirror.task_id.in_(referenced_parent_ids)).all():
                parents_map[p.task_id] = {
                    "task_id": p.task_id,
                    "name": p.name,
                    "status": p.status,
                    "assigned_to_realname": p.assigned_to_realname or p.assigned_to,
                }
        _finished = {"done", "closed", "cancel"}
        for t in tasks:
            if t.get("is_parent"):
                kids = children_map.get(t["task_id"], [])
                t["children"] = kids
                t["children_total"] = len(kids)
                t["children_done"] = sum(1 for k in kids if str(k.get("status") or "").strip().lower() in _finished)
            else:
                t["children"] = []
                t["children_total"] = 0
                t["children_done"] = 0
            pid = _coerce_int(t.get("parent"))
            t["parent_info"] = parents_map.get(pid) if pid and pid > 0 else None
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
    # 任务看板：新建禅道任务（复刻禅道创建页的核心字段）
    # ------------------------------------------------------------------

    _CREATE_TASK_TYPES = {"design", "devel", "test", "study", "discuss", "review", "affair", "misc"}

    def form_options(self, major_version_id: int) -> dict:
        """新建禅道任务的表单选项：可指派人 / 父任务候选 / 关联研发需求。

        可指派人、需求走禅道实时接口（单项失败不阻塞，收进 errors）；
        父任务候选读本地镜像（顶层且未取消/关闭的任务）。
        """
        from fastapi import HTTPException

        major = self.db.query(Version).filter(Version.id == major_version_id).first()
        if not major or not major.zentao_execution_id:
            raise HTTPException(status_code=400, detail="该大版本未绑定禅道执行")
        exec_id = int(major.zentao_execution_id)
        out: dict = {"execution_id": exec_id, "assignable": [], "parents": [], "stories": [], "errors": []}

        client = get_system_zentao_client(self.db)
        if client is None:
            out["errors"].append("找不到可用的禅道账号绑定，人员/需求列表不可用")
        else:
            try:
                out["assignable"] = [
                    {"account": acc, "realname": name}
                    for acc, name in (client.list_assignable_users(exec_id) or {}).items()
                ]
            except Exception as exc:  # noqa: BLE001
                logger.warning("form_options assignable exec %s failed: %s", exec_id, exc)
                out["errors"].append(f"拉取可指派人失败：{exc}")
            try:
                out["stories"] = [
                    {"id": _coerce_int(s.get("id")), "title": str(s.get("title") or "")}
                    for s in (client.list_execution_stories(exec_id) or [])
                    if _coerce_int(s.get("id"))
                ]
            except Exception as exc:  # noqa: BLE001
                logger.warning("form_options stories exec %s failed: %s", exec_id, exc)
                out["errors"].append(f"拉取研发需求失败：{exc}")

        rows = (
            self.db.query(ZentaoTaskMirror)
            .filter(
                ZentaoTaskMirror.execution_id == exec_id,
                ZentaoTaskMirror.parent == 0,
            )
            .order_by(ZentaoTaskMirror.is_parent.desc(), ZentaoTaskMirror.task_id.desc())
            .all()
        )
        out["parents"] = [
            {"id": r.task_id, "name": r.name, "is_parent": bool(r.is_parent), "status": r.status}
            for r in rows
            if str(r.status or "").strip().lower() not in {"cancel", "closed"}
        ]
        return out

    def create_board_task(
        self,
        *,
        current_user: User,
        major_version_id: int,
        name: str,
        task_type: str = "test",
        assigned_to: Optional[str] = None,
        parent_task_id: Optional[int] = None,
        story: Optional[int] = None,
        est_started: Optional[str] = None,
        deadline: Optional[str] = None,
        estimate: Optional[float] = None,
        pri: int = 3,
        desc: Optional[str] = None,
    ) -> dict:
        """从任务看板创建禅道任务并刷新镜像。

        创建人优先用本人禅道绑定（禅道正确记录 openedBy），无绑定回退系统账号。
        parent 在 create 时被禅道忽略，创建成功后再 PUT 挂父（失败降级为警告）。
        """
        from fastapi import HTTPException

        name = (name or "").strip()
        if not name:
            raise HTTPException(status_code=400, detail="任务名称不能为空")
        # 命名规范（2026-07-08）：测试相关任务统一 [测试] 前缀（已带则不重复）
        name = ensure_test_prefix(name)[:255]
        if task_type not in self._CREATE_TASK_TYPES:
            raise HTTPException(status_code=400, detail=f"不支持的任务类型：{task_type}")
        if pri not in (1, 2, 3, 4):
            raise HTTPException(status_code=400, detail="优先级需为 1~4")
        if estimate is not None and (estimate <= 0 or estimate > 999):
            raise HTTPException(status_code=400, detail="预计工时需在 0~999 小时之间")
        major = self.db.query(Version).filter(Version.id == major_version_id).first()
        if not major or not major.zentao_execution_id:
            raise HTTPException(status_code=400, detail="该大版本未绑定禅道执行")
        exec_id = int(major.zentao_execution_id)

        acting_client = get_user_zentao_client(current_user.id, self.db)
        system_client = get_system_zentao_client(self.db)
        candidates = []
        if acting_client is not None:
            candidates.append(acting_client)
        if system_client is not None and system_client is not acting_client:
            candidates.append(system_client)
        if not candidates:
            raise HTTPException(status_code=502, detail="找不到可用的禅道账号绑定")

        created = None
        used_client = None
        errors: list[str] = []
        for cli in candidates:
            try:
                created = cli.create_execution_task(
                    exec_id,
                    name=name,
                    assigned_to=(assigned_to or None),
                    task_type=task_type,
                    story=story or None,
                    est_started=est_started or None,
                    deadline=deadline or None,
                    estimate=estimate,
                    pri=pri,
                    desc=desc,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("create board task in exec %s failed: %s", exec_id, exc)
                errors.append(str(exc))
                continue
            if isinstance(created, dict) and _coerce_int(created.get("id")):
                used_client = cli
                break
            errors.append(f"禅道返回异常：{str(created)[:200]}")
        task_id = _coerce_int((created or {}).get("id")) if isinstance(created, dict) else None
        if not task_id or used_client is None:
            raise HTTPException(status_code=502, detail="创建禅道任务失败：" + ("；".join(errors) or "未返回任务 id"))

        warnings: list[str] = []
        if parent_task_id:
            try:
                used_client.link_task_parent(task_id, int(parent_task_id))
            except Exception as exc:  # noqa: BLE001
                logger.warning("link new task %s -> parent %s failed: %s", task_id, parent_task_id, exc)
                warnings.append(f"挂到父任务 #{parent_task_id} 失败：{exc}")

        # 刷新该执行的镜像，让看板/面板立刻可见（失败不影响创建结果）
        refreshed = self.sync_one_major(major_version_id)
        if not refreshed.get("ok"):
            warnings.append(f"镜像刷新失败（{refreshed.get('error')}），任务已创建、稍后会由后台同步补上")

        return {"ok": True, "task_id": task_id, "warnings": warnings}

    # ------------------------------------------------------------------
    # 独立（未关联需求）任务的禅道操作：开始 / 完成 / 关闭 / 设置工时
    # ------------------------------------------------------------------

    _OP_ACTIONS = {"start", "pause", "finish", "close", "cancel", "reactivate", "set_time", "assign"}

    def operate_task(
        self,
        *,
        task_id: int,
        action: str,
        current_user: User,
        hours: Optional[float] = None,
        consumed: Optional[float] = None,
        comment: Optional[str] = None,
        assigned_to: Optional[str] = None,
    ) -> dict:
        """对禅道任务执行操作。除 assign（面向所有用户开放）外，仅任务指派人本人可操作。"""
        from fastapi import HTTPException

        if action not in self._OP_ACTIONS:
            raise HTTPException(status_code=400, detail=f"不支持的操作：{action}")
        row = self.db.query(ZentaoTaskMirror).filter(ZentaoTaskMirror.task_id == task_id).first()
        if not row:
            raise HTTPException(status_code=404, detail="任务不存在或未同步")
        linked_requirement = self._linked_requirement(task_id)
        self._hydrate_tracking_from_requirement(row, linked_requirement)
        if action == "assign":
            assigned_to = (assigned_to or "").strip()
            if not assigned_to:
                raise HTTPException(status_code=400, detail="请选择要指派的人员")
        # 权限：任务须指派给当前账号本人（禅道账号匹配，或镜像已映射到本人）。
        # 例外 1：管理员可以关闭「已完成」的任务（2026-07-03 需求）。
        # 例外 2：assign（指派/转派）面向所有用户开放（2026-07-06 需求）。
        my_account = (current_user.zentao_account or "").strip().lower()
        acc = (row.assigned_to or "").strip().lower()
        is_mine = (row.assignee_user_id == current_user.id) or (bool(my_account) and acc == my_account)
        if not is_mine and action != "assign":
            role_val = getattr(current_user.role, "value", None) or str(current_user.role or "")
            admin_close = (
                action == "close"
                and role_val == "admin"
                and str(row.status or "").strip().lower() == "done"
            )
            if not admin_close:
                raise HTTPException(status_code=403, detail="该任务未指派给你，无法操作")

        # 父任务与禅道保持一致：状态由子任务驱动（全部完成后禅道自动完成父任务），
        # 子任务未完成时父任务只能暂停或取消；不允许直接开始/完成/设置工时。
        if row.is_parent and action != "assign":
            status_l = str(row.status or "").strip().lower()
            if status_l == "pause":
                allowed = {"start", "cancel"}          # 继续 / 取消
            elif status_l in {"closed", "cancel"}:
                allowed = {"reactivate"}
            elif status_l == "done":
                allowed = {"close", "reactivate"}      # 子任务全完成后禅道置 done → 可关闭
            else:  # wait / doing
                allowed = {"pause", "cancel"}
            if action not in allowed:
                children = self.db.query(ZentaoTaskMirror).filter(ZentaoTaskMirror.parent == row.task_id).all()
                pending = [c for c in children if str(c.status or "").strip().lower() not in {"done", "closed", "cancel"}]
                if action in {"finish", "close"} and pending:
                    raise HTTPException(
                        status_code=400,
                        detail=f"父任务不能直接{'完成' if action == 'finish' else '关闭'}：还有 {len(pending)} 个子任务未完成"
                               "（与禅道一致，子任务全部完成后父任务自动完成）",
                    )
                zh = {"start": "继续", "pause": "暂停", "cancel": "取消", "reactivate": "重新激活", "close": "关闭"}
                raise HTTPException(
                    status_code=400,
                    detail="父任务状态由子任务驱动（与禅道一致），当前可用操作：" + "、".join(zh.get(a, a) for a in sorted(allowed)),
                )

        # 候选客户端：优先「操作人本人」（禅道正确记录操作人、保持指派），失败/未生效再回退
        # 系统管理员账号（chenwenbo）。这样即便本人 token 被当作 guest（返回 401），也能由管理员
        # 账号完成操作；配合事后校正把指派人改回本人。
        original_account = row.assigned_to  # 操作前的指派人账号，用于事后校正
        acting_client = get_user_zentao_client(current_user.id, self.db)
        system_client = get_system_zentao_client(self.db)
        candidates: list[tuple[str, object]] = []
        if acting_client is not None:
            candidates.append(("self", acting_client))
        if system_client is not None and system_client is not acting_client:
            candidates.append(("system", system_client))
        if not candidates:
            raise HTTPException(status_code=502, detail="找不到可用的禅道账号绑定")

        if action == "set_time" and (hours is None or hours <= 0 or hours > 999):
            raise HTTPException(status_code=400, detail="工时需在 0~999 小时之间")
        if action == "finish" and consumed is not None and (consumed <= 0 or consumed > 999):
            raise HTTPException(status_code=400, detail="实际工时需在 0~999 小时之间")

        _expected = {"start": "doing", "pause": "pause", "reactivate": "doing", "finish": "done", "close": "closed", "cancel": "cancel"}.get(action)

        # 工时结算（暂停期不计工时）：操作前先固化时点与暂停态——dispatch 生效后
        # _refresh_one_task 会把 row.status 刷成新状态，之后就读不到操作前的状态了。
        # 只在 finish 时算自动工时：get_holiday_map 缺年份数据会联网补，别的操作不该付这个代价。
        # finish 的工时口径（2026-07-17）：先把「今天之前」的段按天拆分提交为禅道工时
        # 记录，finish 的 currentConsumed 只带今天这部分（禅道会为它生成完成当天的记录）；
        # 无本人网页凭据/提交失败则退回一次性提交总累计。
        op_now = local_now()
        was_paused = (row.status or "").strip().lower() == "pause"

        # Idempotency boundary before any effort submission. A previous action
        # can be effective in Zentao while the caller only sees a 401/stale GET.
        # In that case reconcile both local views and do not write the action or
        # the running effort segment a second time.
        if _expected:
            for _, candidate in candidates:
                current = self._fetch_one_task(candidate, task_id)
                if not current:
                    continue
                current_status = str(current.get("status") or "").strip().lower()
                if current_status == _expected:
                    self._apply_task_data(row, current)
                    if action == "pause":
                        row.consumed_accum = self._auto_consumed_hours(row, op_now)
                        row.local_started_at = None
                    elif action in {"start", "reactivate"} and row.local_started_at is None:
                        row.local_started_at = row.real_started or op_now
                    elif action in {"finish", "close", "cancel"}:
                        row.local_started_at = None
                        if action == "finish" and not row.finished_date:
                            row.finished_date = op_now
                    self._sync_requirement_from_mirror(row, linked_requirement)
                    self.db.commit()
                    task_data = self._serialize(row)
                    sse_publish(
                        "zentao_task_changed",
                        {
                            "task_id": task_id,
                            "status": task_data.get("status"),
                            "action": action,
                            "source": "task_workbench",
                            "task": task_data,
                        },
                        channels=["global"],
                    )
                    return {"ok": True, "errors": [], "task": task_data, "idempotent": True}
                # A successful read is authoritative; no need to ask a second
                # account before performing the requested transition.
                break
        finish_consumed = self._prepare_finish_consumed(row, current_user, op_now, explicit=consumed) if action == "finish" else 0.0

        # ⚠️ 暂停的分段工时必须在暂停「之前」提交：禅道对 pause 状态任务记工时会把
        # 状态自动置回 doing（2026-07-17 线上实证：暂停几秒后被"自动激活"，动作备注
        # 即工时记录的 work 文案）。先在 doing 状态提交（不改状态），再执行暂停；
        # 提交成功后把计时起点重置到当前时刻——暂停未生效时计时自然续跑、不会重复
        # 计入已提交段；暂停生效后由下方簿记停表。
        pause_presubmitted: Optional[float] = None
        if action == "pause":
            pause_presubmitted = self._try_submit_segment_efforts(row, current_user, op_now)
            if pause_presubmitted is not None:
                row.efforts_submitted = round(float(row.efforts_submitted or 0.0) + pause_presubmitted, 2)
                row.consumed_accum = 0.0
                row.local_started_at = op_now

        # 生命周期动作统一「REST → 同账号网页 cookie 会话」两级尝试（2026-07-17 扩展到
        # 全部动作）：本人 token 间歇性失效（过期刷新失败 / ipd4.3 被当 guest）时，此前
        # start/finish/close 直接落到系统账号代操作，禅道动作历史显示成系统账号（"陈文博
        # 开始了任务"）。现在先用本人网页会话兜底，保证操作人归属本人；系统账号只在
        # 本人 REST+网页都不可用时才出场。
        _LIFECYCLE = {"start", "pause", "finish", "close", "cancel", "reactivate"}
        web_logins = {}
        if action in _LIFECYCLE:
            web_logins["self"] = get_user_zentao_web_login(current_user.id, self.db)
            web_logins["system"] = get_system_zentao_web_login(self.db)

        def _dispatch(label, cli):
            if action == "set_time":
                return cli.update_task(task_id, {"estimate": hours, "left": hours})
            if action == "assign":
                return cli.reassign_task(task_id, assigned_to)

            resuming = (row.status or "").strip().lower() == "pause"
            start_left = hours if (hours and hours > 0) else (row.left or row.estimate or 1.0)
            react_left = hours if (hours and hours > 0) else (row.estimate or 1.0)

            def _rest():
                if action == "start":
                    if resuming:
                        return cli.restart_task(task_id, consumed=(row.consumed or 0.0), left=start_left, assigned_to=original_account)
                    return cli.start_task(task_id, real_started=_fmt_now(), left=start_left, assigned_to=original_account)
                if action == "pause":
                    return cli.pause_task(task_id, comment=comment)
                if action == "cancel":
                    return cli.cancel_task(task_id, comment=comment)
                if action == "finish":
                    return cli.finish_task(task_id, current_consumed=finish_consumed, finished_date=_fmt_now())
                if action == "reactivate":
                    return cli.restart_task(task_id, consumed=(row.consumed or 0.0), left=react_left, assigned_to=original_account)
                if action == "close":
                    return cli.close_task(task_id, comment=comment)
                return None

            def _web(web):
                if action == "start":
                    if resuming:
                        return restart_task_via_web(web, task_id, consumed=(row.consumed or 0.0), left=start_left, assigned_to=original_account, comment=comment)
                    return start_task_via_web(web, task_id, left=start_left, real_started=_fmt_now(), comment=comment)
                if action == "pause":
                    return pause_task_via_web(web, task_id, comment=comment)
                if action == "cancel":
                    return cancel_task_via_web(web, task_id, comment=comment)
                if action == "finish":
                    return finish_task_via_web(web, task_id, current_consumed=finish_consumed, finished_date=_fmt_now(), comment=comment)
                if action == "reactivate":
                    return restart_task_via_web(web, task_id, consumed=(row.consumed or 0.0), left=react_left, assigned_to=original_account, comment=comment)
                if action == "close":
                    return close_task_via_web(web, task_id, comment=comment)
                return None

            zh = {"start": "开始", "pause": "暂停", "finish": "完成", "close": "关闭", "cancel": "取消", "reactivate": "重新激活"}.get(action, action)
            resp = None
            try:
                resp = _rest()
                # finish carries incremental effort. A confirmed REST result
                # must not be sent again through the web fallback just because
                # the immediately following task read is temporarily stale.
                if action == "finish" and _response_confirms_status(resp, _expected):
                    return resp
                if str((cli.get_task(task_id) or {}).get("status") or "").strip().lower() == _expected:
                    return resp
            except Exception as exc:  # noqa: BLE001 — REST 失败/未生效都尝试网页会话
                logger.warning("%s via REST (%s) task %s failed: %s", action, label, task_id, exc)
            web = web_logins.get(label)
            if web is not None:
                return _web(web)
            if resp is not None:
                return resp  # 无网页凭据：交给外层校验判定未生效
            raise ZentaoWebSessionError(f"REST {zh}失败且无网页登录凭据")

        errors: list[str] = []
        used_client = None
        last_resp = None
        last_err = None
        confirmed_task = None
        last_actual_status = ""
        last_actual_assigned = ""
        for label, cli in candidates:
            current_actual_status = ""
            current_actual_assigned = ""
            # The preceding account/web fallback may have completed the write
            # even if its response raised. Check before another account repeats
            # the same lifecycle action.
            if _expected:
                already = self._fetch_one_task(cli, row.task_id)
                already_status = str((already or {}).get("status") or "").strip().lower()
                if already_status == _expected:
                    used_client = cli
                    confirmed_task = already
                    last_actual_status = already_status
                    break
            try:
                last_resp = _dispatch(label, cli)
            except HTTPException:
                raise
            except Exception as exc:  # noqa: BLE001
                last_err = exc
                logger.warning("operate task %s action=%s via %s failed: %s", task_id, action, label, exc)
                continue
            # 回读在确认前必须是只读的。旧实现直接刷新 row，导致操作未生效时也会
            # 把回读到的其他状态提交进本地镜像，前端随后刷新就像本地操作成功了一样。
            fresh = self._fetch_one_task(cli, row.task_id)
            if fresh:
                current_actual_status = str(fresh.get("status") or "").strip().lower()
                current_actual_assigned, _ = _account_of(fresh.get("assignedTo"))
                last_actual_status = current_actual_status
                last_actual_assigned = current_actual_assigned
            response_confirmed = action == "finish" and _response_confirms_status(last_resp, _expected)
            logger.info(
                "operate task %s action=%s via %s resp=%r status=%s",
                task_id,
                action,
                label,
                last_resp,
                current_actual_status or "unknown",
            )
            if action == "assign":
                # 指派按回读的指派人校验（状态不变化）
                took_effect = (current_actual_assigned or "").strip().lower() == assigned_to.strip().lower()
            else:
                took_effect = response_confirmed or not _expected or current_actual_status == _expected
            if took_effect:
                used_client = cli
                confirmed_task = fresh
                break  # 生效（或无需校验）
            # 200 但未生效 → 试下一个候选账号
            last_err = None
        if used_client is None:
            if last_err is not None:
                errors.append(str(last_err))
            elif action == "assign":
                errors.append(
                    f"禅道未生效：任务当前指派人为「{last_actual_assigned or '空'}」（期望「{assigned_to}」）。"
                    f"禅道返回：{str(last_resp)[:200]}"
                )
            else:
                errors.append(
                    f"禅道未生效：任务当前状态为「{last_actual_status or '未知'}」（期望「{_expected}」）。"
                    f"禅道返回：{str(last_resp)[:200]}"
                )

        # 只有禅道已确认达到目标状态，才把远端快照写入本地镜像。
        if used_client is not None:
            if confirmed_task:
                self._apply_task_data(row, confirmed_task)
            if action == "finish" and _response_confirms_status(last_resp, _expected):
                row.status = _expected

        # 工时结算簿记（仅操作生效时）：
        # start（继续）保留累计、重置本段起点；start（全新）连累计一起清零；
        # pause 的分段工时已在暂停前提交（防禅道对 pause 任务记工时自动激活），
        # 此处仅停表，提交失败的退回本地累计；
        # reactivate 从零起算；finish 停表、未提交累计已随 currentConsumed 上报。
        if used_client is not None:
            if action == "start":
                if not was_paused:
                    row.consumed_accum = 0.0
                row.local_started_at = op_now
            elif action == "pause":
                if pause_presubmitted is not None:
                    row.local_started_at = None  # 本段已在暂停前提交，暂停生效后停表
                else:
                    # 无本人网页凭据/提交失败 → 旧口径：本地累计，完成时一次性提交
                    self._settle_local_segment(row, op_now)
            elif action == "reactivate":
                row.consumed_accum = 0.0
                row.local_started_at = op_now
            elif action == "finish":
                row.efforts_submitted = round(float(row.efforts_submitted or 0.0) + finish_consumed, 2)
                row.consumed_accum = 0.0
                row.local_started_at = None
                # 禅道某些路径的完成不写 finishedDate（如 left=0 工时触发的自动完成），
                # 看板延期归列全靠它——回读拿不到就用操作时刻兜底
                if not row.finished_date:
                    row.finished_date = op_now

        # 兜底校正：操作生效后若禅道把指派人清空/改掉（系统账号代操作的已知副作用），改派回本人。
        client = used_client or candidates[0][1]
        if used_client is not None and original_account and action in {"start", "pause", "reactivate", "set_time"}:
            fresh = self._refresh_one_task(client, row)
            cur_acc, _ = _account_of((fresh or {}).get("assignedTo"))
            if (cur_acc or "").strip().lower() != original_account.strip().lower():
                try:
                    client.reassign_task(task_id, original_account)
                    self._refresh_one_task(client, row)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("restore assignee task %s -> %s failed: %s", task_id, original_account, exc)
        self._sync_requirement_from_mirror(row, linked_requirement)
        self.db.commit()
        task_data = self._serialize(row)
        if used_client is not None:
            sse_publish(
                "zentao_task_changed",
                {
                    "task_id": task_id,
                    "status": task_data.get("status"),
                    "action": action,
                    "source": "task_workbench",
                    "task": task_data,
                },
                channels=["global"],
            )
        return {"ok": not errors, "errors": errors, "task": task_data}

    def _local_segment_start(self, row: ZentaoTaskMirror) -> Optional[datetime]:
        """本段计时起点：平台记录的 local_started_at 优先；从未结算过（accum=0
        且没分段提交过工时记录）且没有本地起点时，回退禅道的实际开始时间
        real_started（兼容在禅道网页直接开始的任务）。已结算/已提交过则不回退——
        real_started 是首次开始，回退会把已结算的时段重复计入。"""
        if row.local_started_at:
            return row.local_started_at
        if not float(row.consumed_accum or 0.0) and not float(row.efforts_submitted or 0.0):
            return row.real_started
        return None

    def _auto_consumed_hours(self, row: ZentaoTaskMirror, now: datetime) -> float:
        """自动工时 = 暂停结算累计 + 本段（起点→now，工作日全天计、周末节假日跳过）。"""
        total = float(row.consumed_accum or 0.0)
        started = self._local_segment_start(row)
        if started and now > started:
            try:
                hmap = get_holiday_map(self.db, started.date(), now.date())
            except Exception:  # noqa: BLE001 — 节假日数据拉不到时按默认周末规则算
                hmap = {}
            total += work_hours.consumed_hours(started, now, hmap)
        return round(total, 2)

    def _settle_local_segment(self, row: ZentaoTaskMirror, now: datetime) -> None:
        """暂停（兜底口径）：把「本段起点 → now」结算进 consumed_accum 并停表。"""
        started = self._local_segment_start(row)
        settled = self._auto_consumed_hours(row, now)
        # 已真实开始但不足一分钟（或落在非工作日）时保留最小计时标记，避免完成时
        # 被误判成“从未计时”并回退预计工时。
        row.consumed_accum = max(settled, 0.1) if started else settled
        row.local_started_at = None

    def _split_segment_by_day(self, started: Optional[datetime], ended: datetime) -> list[tuple[date, float]]:
        """本段计时按天拆分 [(日期, 小时)]（工作日全天计、周末节假日跳过）。"""
        if not started or ended <= started:
            return []
        try:
            hmap = get_holiday_map(self.db, started.date(), ended.date())
        except Exception:  # noqa: BLE001 — 节假日数据拉不到时按默认周末规则算
            hmap = {}
        return work_hours.consumed_hours_by_day(started, ended, hmap)

    def _try_submit_segment_efforts(self, row: ZentaoTaskMirror, current_user: User, now: datetime) -> Optional[float]:
        """暂停：本段（+ 旧口径遗留的未提交累计，补录到今天）按天拆分提交为禅道
        工时记录。成功返回提交的小时数（可为 0——本段全落在周末/节假日）；
        无本人网页凭据或提交失败返回 None，调用方退回本地累计口径。

        ⚠️ 工时记录归属禅道当前登录人，只能用本人凭据，不回退系统账号。"""
        started = self._local_segment_start(row)
        day_rows = self._split_segment_by_day(started, now)
        if started and not day_rows:
            day_rows = [(now.date(), 0.1)]
        day_rows = merge_extra_hours(day_rows, float(row.consumed_accum or 0.0), now.date())
        if not day_rows:
            return 0.0
        login = get_user_zentao_web_login(current_user.id, self.db)
        if login is None:
            logger.info("task %s pause: no self web login, fall back to local accum", row.task_id)
            return None
        try:
            return submit_day_efforts(login, row.task_id, day_rows, left_before=float(row.left or 0.0), note=AUTO_NOTE)
        except Exception as exc:  # noqa: BLE001 — 提交失败退回本地累计，不阻塞暂停
            logger.warning("task %s pause: submit efforts failed, fall back to local accum: %s", row.task_id, exc)
            return None

    def _prepare_finish_consumed(self, row: ZentaoTaskMirror, current_user: User, now: datetime, *, explicit: Optional[float]) -> float:
        """完成时的 currentConsumed：优先把「今天之前」的段按天拆分提交为工时记录，
        finish 只带今天的部分 + 未提交累计（禅道会为它生成完成当天的记录）；
        无本人网页凭据/提交失败则退回一次性提交全部。禅道要求 >0，最小 0.1。"""
        if explicit is not None:
            return round(float(explicit), 2)
        started = self._local_segment_start(row)
        day_rows = self._split_segment_by_day(started, now)
        today = now.date()
        prev_rows = [(d, h) for d, h in day_rows if d < today]
        remainder = round(
            sum(h for d, h in day_rows if d >= today) + float(row.consumed_accum or 0.0), 2
        )
        if prev_rows:
            login = get_user_zentao_web_login(current_user.id, self.db)
            if login is not None:
                try:
                    submitted = submit_day_efforts(
                        login, row.task_id, prev_rows, left_before=float(row.left or 0.0), note=AUTO_NOTE
                    )
                    row.efforts_submitted = round(float(row.efforts_submitted or 0.0) + submitted, 2)
                    # finish 失败时 remainder 留在 accum 里可重试；成功后簿记会清零
                    row.consumed_accum = remainder
                    row.local_started_at = None
                    return max(remainder, 0.1)
                except Exception as exc:  # noqa: BLE001 — 拆分提交失败退回一次性
                    logger.warning("task %s finish: pre-submit efforts failed, fall back to lump sum: %s", row.task_id, exc)
            total = round(remainder + sum(h for _, h in prev_rows), 2)
        else:
            total = remainder
        if total > 0:
            return max(total, 0.1)
        if float(row.efforts_submitted or 0.0) > 0:
            # 各段都已提交过工时记录 → 不能再拿剩余/预计工时兜底（会重复计入），给最小值
            return 0.1
        if started is not None:
            # 已计时但不足一分钟/当日不计工时：禅道要求完成工时 >0，按最小值提交。
            return 0.1
        from fastapi import HTTPException
        raise HTTPException(
            status_code=400,
            detail="未检测到平台计时记录，请确认实际工时后再完成任务",
        )

    def assignable_users(self, task_id: int) -> dict:
        """任务所在执行的可指派人列表（指派弹窗数据源，面向所有用户）。"""
        from fastapi import HTTPException

        row = self.db.query(ZentaoTaskMirror).filter(ZentaoTaskMirror.task_id == task_id).first()
        if not row:
            raise HTTPException(status_code=404, detail="任务不存在或未同步")
        if not row.execution_id:
            raise HTTPException(status_code=400, detail="任务缺少执行信息，无法拉取可指派人")
        client = get_system_zentao_client(self.db)
        if client is None:
            raise HTTPException(status_code=502, detail="找不到可用的禅道账号绑定")
        try:
            users = client.list_assignable_users(int(row.execution_id)) or {}
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=502, detail=f"拉取可指派人失败：{exc}")
        return {
            "task_id": task_id,
            "current": row.assigned_to,
            "users": [{"account": acc, "realname": name} for acc, name in users.items()],
        }

    def _fetch_one_task(self, client, task_id: int) -> Optional[dict]:
        try:
            t = client.get_task(task_id) or {}
        except Exception as exc:  # noqa: BLE001
            logger.warning("fetch one task %s failed: %s", task_id, exc)
            return None
        if not isinstance(t, dict) or not t:
            return None
        return t

    def _apply_task_data(self, row: ZentaoTaskMirror, t: dict) -> None:
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
        if t.get("finishedBy") is not None:
            fb_acc, fb_name = _account_of(t.get("finishedBy"))
            if fb_acc:
                row.finished_by = fb_acc
                row.finished_by_realname = fb_name or row.finished_by_realname
        row.estimate = _as_float(t.get("estimate")) if t.get("estimate") is not None else row.estimate
        row.consumed = _as_float(t.get("consumed")) if t.get("consumed") is not None else row.consumed
        row.left = _as_float(t.get("left")) if t.get("left") is not None else row.left
        if t.get("realStarted") is not None:
            row.real_started = _parse_dt(t.get("realStarted"))
        if t.get("finishedDate") is not None:
            _fin = _parse_dt(t.get("finishedDate"))
            # 完成态任务的空 finishedDate（left=0 自动完成）不冲掉已兜底的完成时间
            if _fin is not None or str(t.get("status") or "").strip().lower() not in {"done", "closed"}:
                row.finished_date = _fin
        if t.get("deadline") is not None:
            row.deadline = _parse_date(t.get("deadline"))
        row.synced_at = local_now()

    def _refresh_one_task(self, client, row: ZentaoTaskMirror) -> Optional[dict]:
        t = self._fetch_one_task(client, row.task_id)
        if not t:
            return None
        self._apply_task_data(row, t)
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
            "finished_by": r.finished_by,
            "finished_by_realname": r.finished_by_realname,
            "estimate": r.estimate,
            "consumed": r.consumed,
            "left": r.left,
            "est_started": r.est_started.isoformat() if r.est_started else None,
            "deadline": r.deadline.isoformat() if r.deadline else None,
            "real_started": r.real_started.isoformat() if r.real_started else None,
            "finished_date": r.finished_date.isoformat() if r.finished_date else None,
            "synced_at": r.synced_at.isoformat() if r.synced_at else None,
            "has_time_tracking": bool(
                r.local_started_at
                or r.real_started
                or float(r.consumed_accum or 0.0) > 0
                or float(r.efforts_submitted or 0.0) > 0
            ),
            "suggested_consumed_hours": round(float(r.left or r.estimate or 1.0), 2),
        }


__all__ = ["ZentaoTaskMirrorService"]
