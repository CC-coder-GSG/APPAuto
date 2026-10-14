"""Selected requirement child tasks: update dates only, then verify remotely."""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.models import User
from app.models.zentao_task_mirror import ZentaoTaskMirror
from app.services.assignment_validation import validate_selection, validate_task_dates
from app.services.audit_service import audit
from app.services.sse_service import sse_publish
from app.services.zentao_system_client import get_system_zentao_client
from app.services.zentao_task_mirror_service import ZentaoTaskMirrorService

logger = logging.getLogger(__name__)


def _id(value) -> int:
    if isinstance(value, dict):
        value = value.get("id")
    try:
        return int(value or 0)
    except (ValueError, TypeError):
        return 0


def _binding_error(task: dict, req, execution_id: int) -> str:
    if _id(task.get("id")) != req.zentao_task_id:
        return "禅道返回的任务编号与关联不一致"
    if _id(task.get("execution")) != execution_id:
        return "关联任务不属于当前大版本的禅道执行"
    if task.get("deleted") in (True, 1, "1"):
        return "关联任务已删除"
    if str(task.get("type") or "") != "test" or _id(task.get("parent")) <= 0 or task.get("isParent") in (True, 1, "1"):
        return "关联任务不是有效的测试子任务，请先修正关联"
    if req.zentao_story_id and _id(task.get("story")) != req.zentao_story_id:
        return "关联任务的研发需求不一致，请先修正关联"
    if req.zentao_parent_task_id and _id(task.get("parent")) != req.zentao_parent_task_id:
        return "关联任务的父任务不一致，请先修正关联"
    return ""


class ZentaoTaskScheduleService:
    def __init__(self, db: Session):
        self.db = db

    def update_dates(self, major_version_id: int, requirement_ids: list[int], *, start: str | None,
                     deadline: str | None, actor: User) -> dict:
        validate_task_dates(start, deadline)
        major, reqs = validate_selection(self.db, major_version_id, requirement_ids)
        # Snapshot immutable values: cache/audit commits may expire ORM instances.
        entries = [{"requirement_id": r.id, "zentao_req_id": r.zentao_req_id, "title": r.title,
                    "task_id": r.zentao_task_id} for r in reqs]
        client = None
        client_error = "该大版本未绑定禅道执行"
        if major.zentao_execution_id and any(e["task_id"] for e in entries):
            try:
                client = get_system_zentao_client(self.db)
                client_error = "找不到可用的禅道账号绑定"
            except Exception:
                logger.exception("Unable to obtain Zentao client for schedule update")
                client_error = "禅道连接初始化失败，请检查账号绑定"
        execution_id = major.zentao_execution_id
        results = []
        outcomes: dict[int, dict] = {}
        tasks: dict[int, dict | None] = {}
        grouped = {}
        for r in reqs:
            if r.zentao_task_id:
                grouped.setdefault(r.zentao_task_id, []).append(r)
        for entry in entries:
            tid = entry["task_id"]
            if not tid:
                outcome = {"status": "skipped", "reason": "尚未创建禅道子任务，已跳过"}
            elif tid in outcomes:
                outcome = outcomes[tid]
            elif client is None:
                outcome = {"status": "failed", "reason": client_error}
            else:
                outcome, task = self._update_one(client, grouped[tid], execution_id, start, deadline)
                tasks[tid] = task
                outcomes[tid] = outcome
            results.append({**entry, **outcome})

        # Cache verified observations, including unchanged dates. Never infer dates from a PUT response.
        parents = set()
        for tid, task in tasks.items():
            if task is None:
                continue
            outcome = outcomes[tid]
            try:
                self._cache(task)
                if outcome["status"] == "updated":
                    audit(self.db, action="zentao_task.update_dates", target_type="zentao_task",
                          actor_id=actor.id, target_id=str(tid), detail=f"estStarted={start},deadline={deadline}")
                else:
                    self.db.commit()
                parents.add(_id(task.get("parent")))
            except Exception:
                self.db.rollback()
                logger.exception("Schedule observed remotely but cache write failed for task %s", tid)
                for row in results:
                    if row["task_id"] == tid:
                        row["warning"] = "禅道结果已核实，但本地缓存同步失败，请刷新后重试同步"
        # Zentao may adjust the parent dates automatically; refresh an existing mirror, never write the parent.
        for pid in parents:
            try:
                mirror = self.db.get(ZentaoTaskMirror, pid) if pid else None
                if mirror:
                    parent = client.get_task(pid)
                    if parent and _id(parent.get("id")) == pid and _id(parent.get("execution")) == execution_id:
                        ZentaoTaskMirrorService(self.db)._apply_task_data(mirror, parent)
                        self.db.commit()
            except Exception:
                self.db.rollback()
                logger.warning("Parent schedule cache refresh failed for task %s", pid)
        if tasks:
            try:
                sse_publish("zentao_task_changed", {"action": "update_dates", "task_ids": list(tasks)}, channels=["global"])
            except Exception:
                logger.warning("Schedule update SSE notification failed")
        counts = {status: sum(r["status"] == status for r in results)
                  for status in ("updated", "unchanged", "skipped", "failed", "unverified")}
        return {"ok": not (counts["failed"] or counts["unverified"]), "total": len(results),
                "counts": counts, "results": results, "task_start_date": start, "task_deadline": deadline}

    def _update_one(self, client, reqs, execution_id, start, deadline) -> tuple[dict, dict | None]:
        tid = reqs[0].zentao_task_id
        try:
            before = client.get_task(tid)
            if not isinstance(before, dict) or not before:
                raise ValueError("Empty task detail")
        except Exception:
            logger.warning("Task detail unavailable before date update: %s", tid)
            return {"status": "failed", "reason": "无法读取禅道任务，未执行修改；请检查连接、权限或任务是否已删除"}, None
        # Check all requirements referencing a shared task before writing it once.
        for req in reqs:
            reason = _binding_error(before, req, execution_id)
            if reason:
                return {"status": "skipped", "reason": reason}, None
        if before.get("estStarted") == start and before.get("deadline") == deadline:
            return {"status": "unchanged", "reason": "预计起止日期已一致，无需修改"}, before
        write_failed = False
        try:
            client.update_task_dates(tid, est_started=start, deadline=deadline)
        except Exception:
            write_failed = True
            logger.warning("Date update request errored; verifying task %s before reporting", tid)
        try:
            after = client.get_task(tid)
            if not isinstance(after, dict) or not after or any(_binding_error(after, r, execution_id) for r in reqs):
                raise ValueError("Unable to verify linked task")
        except Exception:
            return {"status": "unverified", "reason": "更新请求已发送，但无法核实结果；请刷新禅道检查后再重试"}, None
        if after.get("estStarted") == start and after.get("deadline") == deadline:
            return {"status": "updated", "reason": "预计起止日期已更新并核实"}, after
        return {"status": "unverified" if write_failed else "failed",
                "reason": "请求异常且日期未确认生效，请检查禅道后再重试" if write_failed
                          else "禅道未保存所设日期，请检查任务编辑权限或禅道日期限制"}, after

    def _cache(self, task: dict) -> None:
        tid = _id(task.get("id"))
        row = self.db.get(ZentaoTaskMirror, tid)
        if row is None:
            row = ZentaoTaskMirror(task_id=tid, execution_id=_id(task.get("execution")),
                                   name=task.get("name") or f"禅道任务 #{tid}", parent=_id(task.get("parent")),
                                   is_parent=False, story=_id(task.get("story")) or None, type=task.get("type"))
            self.db.add(row)
        ZentaoTaskMirrorService(self.db)._apply_task_data(row, task)
