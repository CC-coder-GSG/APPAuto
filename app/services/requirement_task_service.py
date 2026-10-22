"""Scoped test-task selection without changing a requirement's primary binding."""
from __future__ import annotations

from types import SimpleNamespace

from fastapi import HTTPException
from sqlalchemy import or_

from app.models import FinalTestRecord, Requirement, User, Version
from app.models.zentao_task_mirror import ZentaoTaskMirror
from app.utils.time_utils import local_now


class RequirementTaskService:
    DONE = {"done", "closed"}

    def __init__(self, db):
        self.db = db

    def rows_by_requirement(self, reqs):
        ids = {r.zentao_task_id for r in reqs if r.zentao_task_id}
        stories = {r.zentao_story_id for r in reqs if r.zentao_story_id}
        rows = self.db.query(ZentaoTaskMirror).filter(
            ZentaoTaskMirror.is_parent == 0, ZentaoTaskMirror.type == "test",
            or_(ZentaoTaskMirror.status != "cancel", ZentaoTaskMirror.status.is_(None)),
            or_(ZentaoTaskMirror.task_id.in_(ids), ZentaoTaskMirror.story.in_(stories)),
        ).order_by(ZentaoTaskMirror.task_id).all() if reqs else []
        return {r.id: [t for t in rows if t.execution_id == r.major_version.zentao_execution_id
                       and (t.task_id == r.zentao_task_id or
                            (r.zentao_story_id and t.story == r.zentao_story_id))]
                for r in reqs}

    @staticmethod
    def belongs_to_user(row, user, *, include_finished=True):
        account = (user.zentao_account or "").strip().lower()
        return (row.assignee_user_id == user.id or
                bool(account and (row.assigned_to or "").strip().lower() == account) or
                bool(include_finished and row.status in RequirementTaskService.DONE and account
                     and (row.finished_by or "").strip().lower() == account))

    def describe(self, reqs, user=None):
        from app.services.zentao_task_mirror_service import ZentaoTaskMirrorService

        result = {}
        for req_id, rows in self.rows_by_requirement(reqs).items():
            result[req_id] = [{**ZentaoTaskMirrorService._serialize(t),
                               "assigned_to_me": bool(user and self.belongs_to_user(t, user)),
                               "can_operate": bool(user and self.belongs_to_user(t, user, include_finished=False))}
                              for t in rows]
        return result

    @staticmethod
    def missing_primary(req, rows):
        return bool(req.zentao_task_id and req.zentao_task_status_cache != "cancel"
                    and req.zentao_task_id not in {t.task_id for t in rows})

    def task_count(self, req, rows):
        return len(rows) + int(self.missing_primary(req, rows))

    def reconcile(self, req, rows=None):
        """Only multi-task requirements use the all-tasks completion rule."""
        rows = self.rows_by_requirement([req])[req.id] if rows is None else rows
        missing_primary = self.missing_primary(req, rows)
        manual_only = bool(rows and rows[0].task_id != req.zentao_task_id)
        if self.task_count(req, rows) < 2 and not manual_only:
            return False
        complete = all(t.status in self.DONE and not t.task_story_change_pending for t in rows)
        # A known primary task missing from the mirror must not disappear from the denominator.
        if missing_primary:
            complete = complete and req.zentao_task_status_cache in self.DONE
        if bool(req.test_completed) == complete:
            return False
        from app.services.requirement_service import RequirementService

        svc = RequirementService(self.db)
        if complete:
            req.test_completed = True
            req.test_completed_at = max((t.finished_date for t in rows if t.finished_date), default=local_now())
            svc.recalculate_requirement_status(req)
        else:
            svc.reopen_after_task_reactivation(req)
        return True

    def get_requirement_task(self, requirement_id, task_id):
        req = self.db.get(Requirement, requirement_id)
        if not req:
            raise HTTPException(404, "需求不存在")
        row = next((t for t in self.rows_by_requirement([req])[req.id] if t.task_id == task_id), None)
        if row is None:
            raise HTTPException(404, "所选任务不属于该需求及大版本，或已取消")
        return req, row

    def _verify_remote(self, req, row, actor=None):
        from app.services.zentao_system_client import get_system_zentao_client

        client = get_system_zentao_client(self.db)
        if client is None and actor is not None:
            from app.services.zentao_system_client import get_user_zentao_client
            client = get_user_zentao_client(actor.id, self.db)
        if client is None:
            raise HTTPException(502, "找不到可用的禅道账号绑定")
        try:
            task = client.get_task(row.task_id)
        except Exception as exc:
            raise HTTPException(502, "无法读取所选任务，未执行修改") from exc
        from app.services.zentao_task_schedule_service import _id

        if (not isinstance(task, dict) or _id(task.get("id")) != row.task_id or
                _id(task.get("execution")) != req.major_version.zentao_execution_id or
                (req.zentao_story_id and _id(task.get("story")) != req.zentao_story_id) or
                task.get("type") != "test" or task.get("deleted") in (True, 1, "1") or
                task.get("isParent") in (True, 1, "1") or task.get("status") == "cancel"):
            raise HTTPException(409, "所选任务的需求、版本或类型已变化，请刷新后重试")
        from app.services.zentao_task_mirror_service import ZentaoTaskMirrorService
        ZentaoTaskMirrorService(self.db)._apply_task_data(row, task)
        if "assignedTo" in task:
            from app.services.zentao_task_mirror_service import _account_of
            if not _account_of(task.get("assignedTo"))[0]:
                row.assigned_to = None
                row.assignee_user_id = None
        return client

    def operate(self, requirement_id, task_id, user, *, action, hours=None, consumed=None,
                minor_version_id=None, result_status="passed", notes=None):
        if action not in {"start", "pause", "finish", "reactivate", "set_time"}:
            raise HTTPException(400, "不支持的需求任务操作")
        req, row = self.get_requirement_task(requirement_id, task_id)
        from app.models import TestExecution, TestResultStatus, VersionType
        if minor_version_id is not None:
            minor = self.db.get(Version, minor_version_id)
            if minor is None or minor.version_type != VersionType.MINOR or minor.parent_id != req.major_version_id:
                raise HTTPException(400, "小版本与需求所属大版本不匹配")
            if result_status not in {s.value for s in TestResultStatus}:
                raise HTTPException(400, "测试结果非法")
        if not self.belongs_to_user(row, user, include_finished=False):
            raise HTTPException(403, "所选任务未指派给你，无法操作")
        self._verify_remote(req, row, user)
        if not self.belongs_to_user(row, user, include_finished=False):
            raise HTTPException(403, "所选任务已改派给其他人，请刷新后重试")
        from app.services.zentao_task_mirror_service import ZentaoTaskMirrorService

        result = ZentaoTaskMirrorService(self.db).operate_task(
            task_id=task_id, action=action, current_user=user, hours=hours, consumed=consumed,
        )
        if not result.get("ok"):
            raise HTTPException(502, "；".join(result.get("errors") or []) or "禅道任务操作未确认生效")
        if action == "set_time":
            self._verify_remote(req, row, user)
            if row.estimate is None or abs(float(row.estimate) - hours) > 0.001:
                raise HTTPException(502, "禅道未保存所选任务的预计用时，请刷新后核实")
        if action == "set_time" and task_id == req.zentao_task_id:
            req.estimated_test_hours = hours
        rows = self.rows_by_requirement([req])[req.id]
        self.reconcile(req, rows)
        if req.major_version.final_test_enabled and action in {"finish", "reactivate"}:
            record = self.db.query(FinalTestRecord).filter_by(requirement_id=req.id, user_id=user.id).first()
            if record is None:
                record = FinalTestRecord(requirement_id=req.id, user_id=user.id)
                self.db.add(record)
            own = [t for t in rows if self.belongs_to_user(t, user)]
            record.test_completed = bool(own) and all(t.status in self.DONE for t in own)
            record.test_completed_at = local_now() if record.test_completed else None
        if action == "finish" and minor_version_id is not None:
            prefix = f"任务 #{task_id}"
            execution = self.db.query(TestExecution).filter(
                TestExecution.requirement_id == req.id, TestExecution.minor_version_id == minor_version_id,
                TestExecution.executed_by_id == user.id,
                or_(TestExecution.notes == prefix, TestExecution.notes.startswith(prefix + "：")),
            ).first()
            if execution is None:
                execution = TestExecution(requirement_id=req.id, minor_version_id=minor_version_id,
                                          executed_by_id=user.id)
                self.db.add(execution)
            execution.result_status = TestResultStatus(result_status)
            execution.notes = prefix + (f"：{notes}" if notes else "")
            execution.executed_at = local_now()
        from app.services.audit_service import audit
        audit(self.db, action="requirement.operate_linked_task", target_type="zentao_task", target_id=str(task_id),
              actor_id=user.id, detail=f"requirement={req.id},action={action}")
        self.db.commit()
        from app.services.requirement_service import RequirementService
        RequirementService._publish_completion_status(req, source="requirement_linked_task")
        return {**result, "requirement_test_completed": bool(req.test_completed)}

    def edit(self, requirement_id, task_id, actor, *, owner_id=None, start=None, deadline=None):
        req, row = self.get_requirement_task(requirement_id, task_id)
        from app.services.zentao_task_schedule_service import ZentaoTaskScheduleService
        from app.services.assignment_validation import validate_task_dates
        from app.services.audit_service import audit

        if start is not None or deadline is not None:
            validate_task_dates(start, deadline)
        owner = self.db.get(User, owner_id) if owner_id else None
        if owner_id and (owner is None or not owner.is_active or not owner.zentao_account):
            raise HTTPException(400, "所选人员未启用或未配置禅道账号")
        client = self._verify_remote(req, row, actor)
        results = {}
        if owner is not None:
            from app.services.zentao_task_mirror_service import ZentaoTaskMirrorService

            assigned = ZentaoTaskMirrorService(self.db).operate_task(
                task_id=task_id, action="assign", current_user=actor, assigned_to=owner.zentao_account,
            )
            results["assignment"] = assigned
            if not assigned.get("ok"):
                raise HTTPException(502, "；".join(assigned.get("errors") or []) or "任务改派未确认生效")
        if start is not None or deadline is not None:
            target = SimpleNamespace(zentao_task_id=task_id, zentao_story_id=req.zentao_story_id,
                                     zentao_parent_task_id=None, allow_root_task=True)
            schedule = ZentaoTaskScheduleService(self.db)
            outcome, observed = schedule._update_one(client, [target], row.execution_id, start, deadline)
            if observed:
                schedule._cache(observed)
            results["dates"] = outcome
        audit(self.db, action="requirement.edit_linked_task", target_type="zentao_task", target_id=str(task_id),
              actor_id=actor.id, detail=f"requirement={req.id},owner={owner_id},start={start},deadline={deadline}")
        self.db.commit()
        ok = results.get("dates", {}).get("status") not in {"failed", "skipped", "unverified"}
        return {"ok": ok, "task_id": task_id, "results": results}
