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
from app.services.zentao_system_client import (
    get_system_zentao_client,
    get_system_zentao_web_login,
    get_user_zentao_client,
    get_user_zentao_web_login,
)
from app.services.zentao_web_session import ZentaoWebSessionError, cancel_task_via_web, pause_task_via_web
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

        _expected = {"start": "doing", "pause": "pause", "reactivate": "doing", "finish": "done", "close": "closed", "cancel": "cancel"}.get(action)

        # 暂停/取消：REST 优先（ipd4.3 空 body 会被静默忽略，pause/cancel_task 已固定带 comment 字段）；
        # REST 未生效（如 token 被当 guest 时 200 无效果）再回退网页 cookie 会话。
        web_logins = {}
        if action in {"pause", "cancel"}:
            web_logins["self"] = get_user_zentao_web_login(current_user.id, self.db)
            web_logins["system"] = get_system_zentao_web_login(self.db)

        def _dispatch(label, cli):
            if action == "start":
                left = hours if (hours and hours > 0) else (row.left or row.estimate or 1.0)
                if (row.status or "").strip().lower() == "pause":
                    return cli.restart_task(task_id, consumed=(row.consumed or 0.0), left=left, assigned_to=original_account)
                return cli.start_task(task_id, real_started=_fmt_now(), left=left, assigned_to=original_account)
            if action in {"pause", "cancel"}:
                rest_call = cli.pause_task if action == "pause" else cli.cancel_task
                web_call = pause_task_via_web if action == "pause" else cancel_task_via_web
                zh = "暂停" if action == "pause" else "取消"
                resp = None
                try:
                    resp = rest_call(task_id, comment=comment)
                    if str((cli.get_task(task_id) or {}).get("status") or "").strip().lower() == _expected:
                        return resp
                except Exception as exc:  # noqa: BLE001 — REST 失败/未生效都尝试网页会话
                    logger.warning("%s via REST (%s) task %s failed: %s", action, label, task_id, exc)
                web = web_logins.get(label)
                if web is not None:
                    return web_call(web, task_id, comment=comment)
                if resp is not None:
                    return resp  # 无网页凭据：交给外层校验判定未生效
                raise ZentaoWebSessionError(f"REST {zh}失败且无网页登录凭据")
            if action == "finish":
                cur = consumed if (consumed and consumed > 0) else (row.left or row.estimate or 1.0)
                return cli.finish_task(task_id, current_consumed=cur, finished_date=_fmt_now())
            if action == "reactivate":
                left = hours if (hours and hours > 0) else (row.estimate or 1.0)
                return cli.restart_task(task_id, consumed=(row.consumed or 0.0), left=left, assigned_to=original_account)
            if action == "close":
                return cli.close_task(task_id, comment=comment)
            if action == "set_time":
                return cli.update_task(task_id, {"estimate": hours, "left": hours})
            if action == "assign":
                return cli.reassign_task(task_id, assigned_to)
            return None

        errors: list[str] = []
        used_client = None
        last_resp = None
        last_err = None
        for label, cli in candidates:
            try:
                last_resp = _dispatch(label, cli)
            except HTTPException:
                raise
            except Exception as exc:  # noqa: BLE001
                last_err = exc
                logger.warning("operate task %s action=%s via %s failed: %s", task_id, action, label, exc)
                continue
            self._refresh_one_task(cli, row)
            logger.info("operate task %s action=%s via %s resp=%r status=%s", task_id, action, label, last_resp, row.status)
            if action == "assign":
                # 指派按回读的指派人校验（状态不变化）
                took_effect = (row.assigned_to or "").strip().lower() == assigned_to.strip().lower()
            else:
                took_effect = not _expected or (row.status or "").strip().lower() == _expected
            if took_effect:
                used_client = cli
                break  # 生效（或无需校验）
            # 200 但未生效 → 试下一个候选账号
            last_err = None
        if used_client is None:
            if last_err is not None:
                errors.append(str(last_err))
            elif action == "assign":
                errors.append(
                    f"禅道未生效：任务当前指派人为「{row.assigned_to or '空'}」（期望「{assigned_to}」）。"
                    f"禅道返回：{str(last_resp)[:200]}"
                )
            else:
                errors.append(
                    f"禅道未生效：任务当前状态为「{row.status or '未知'}」（期望「{_expected}」）。"
                    f"禅道返回：{str(last_resp)[:200]}"
                )

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
        self.db.commit()
        return {"ok": not errors, "errors": errors, "task": self._serialize(row)}

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
