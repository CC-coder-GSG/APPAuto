from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from fastapi import HTTPException
from sqlalchemy import or_
from sqlalchemy.orm import Session, joinedload, selectinload

from app.models import BugTracking, Requirement, RequirementRetestRecord, Version
from app.models.final_test_record import FinalTestRecord
from app.models.user import User
from app.models.task_board import TaskBoardTask
from app.models.zentao_task_mirror import ZentaoTaskMirror
from app.services.workbench_link_service import WorkbenchLinkService
from app.services.workbench_service import WorkbenchService
from app.utils.time_utils import local_now


def _iso(value):
    return value.isoformat() if value else None


def _value(value):
    return getattr(value, "value", value)


class AssignmentProgressService:
    """Read-only requirement progress, using the workbench's association rules.

    Association queries run in batches. Summary responses contain counts only;
    expanded lists are paged separately without calling a mutating workbench GET.
    """

    def __init__(self, db: Session):
        self.db = db

    def _query(self, major_version_id=None, software_id=None):
        query = self.db.query(Requirement).join(Version, Requirement.major_version_id == Version.id).options(
            joinedload(Requirement.owner), joinedload(Requirement.major_version),
            selectinload(Requirement.test_cases),
        )
        # Apply BOTH constraints: a stale major selection cannot escape software scope.
        if major_version_id:
            query = query.filter(Requirement.major_version_id == major_version_id)
        if software_id:
            query = query.filter(Version.software_id == software_id)
        return query

    def get_requirement(self, requirement_id, major_version_id=None, software_id=None):
        req = self._query(major_version_id, software_id).filter(Requirement.id == requirement_id).first()
        if not req:
            raise HTTPException(status_code=404, detail="当前软件或版本中不存在该需求")
        return req

    def _collect(self, reqs):
        if not reqs:
            return {}
        link = WorkbenchLinkService(self.db)
        minors = link.minor_version_name_map()
        ids = [r.id for r in reqs]
        cases = link.build_requirement_case_view(reqs, minors)
        free, _ = link.build_requirement_free_bug_view(reqs, minors)
        retest, _ = link.build_requirement_free_bug_view(reqs, minors, include_retest=True)
        # Before test completion (or without its timestamp), a newly filed Bug
        # is an ordinary test finding, not evidence of a retest regression.
        completed = [r for r in reqs if r.test_completed and r.test_completed_at]
        evidence = link.build_retest_evidence(completed, minors)
        # Include direct links of every source, including old/orphaned case links,
        # and activation evidence whose original requirement link is different.
        direct = self.db.query(BugTracking).options(
            joinedload(BugTracking.dispatched_to), joinedload(BugTracking.retest_activated_by),
        ).filter(or_(BugTracking.requirement_id.in_(ids), BugTracking.retest_activated_req_id.in_(ids))).all()
        direct_map = defaultdict(list)
        for bug in direct:
            if bug.requirement_id in ids:
                direct_map[bug.requirement_id].append(bug)
            if bug.retest_activated and bug.retest_activated_req_id in ids and bug.retest_activated_req_id != bug.requirement_id:
                direct_map[bug.retest_activated_req_id].append(bug)

        records_map = defaultdict(list)
        records = self.db.query(RequirementRetestRecord).options(
            joinedload(RequirementRetestRecord.user), joinedload(RequirementRetestRecord.minor_version),
        ).filter(RequirementRetestRecord.requirement_id.in_(ids)).order_by(
            RequirementRetestRecord.updated_at, RequirementRetestRecord.id,
        ).all()
        for record in records:
            records_map[record.requirement_id].append({
                "user_name": record.user.shown_name if record.user else "未知",
                "passed": bool(record.passed), "updated_at": _iso(record.updated_at),
                "minor_version_name": record.minor_version.version_no if record.minor_version else None,
            })

        tasks_map = self._tasks(reqs)
        # Load metadata for all linked Bugs once (the link service returns briefs).
        linked_ids = {b.id for b in direct}
        for req in reqs:
            linked_ids.update(b["id"] for c in cases.get(req.id, []) for b in c.get("bugs", []))
            for mapping in (free, retest, evidence):
                linked_ids.update(b["id"] for b in mapping.get(req.id, []))
        bug_rows = {b.id: b for b in self.db.query(BugTracking).options(
            joinedload(BugTracking.retest_activated_by), joinedload(BugTracking.dispatched_to),
        ).filter(BugTracking.id.in_(linked_ids)).all()} if linked_ids else {}

        result = {}
        workbench = WorkbenchService(self.db)
        software_by_major = dict(self.db.query(Version.id, Version.software_id).all())
        for req in reqs:
            bugs = {}

            def add(brief, source):
                if software_by_major.get(brief["major_version_id"]) != req.major_version.software_id:
                    return
                item = bugs.setdefault(brief["id"], {**brief, "association_sources": []})
                if source not in item["association_sources"]:
                    item["association_sources"].append(source)

            for row in direct_map[req.id]:
                add(link.serialize_bug_brief(row, minors, expect_story_id=req.zentao_story_id), "直接关联" if row.requirement_id == req.id else "复测激活")
            for case in cases.get(req.id, []):
                for bug in case.get("bugs", []):
                    add(bug, "用例关联")
            for mapping, label in ((free, "需求归集"), (retest, "复测来源"), (evidence, "测后归集")):
                for bug in mapping.get(req.id, []):
                    add(bug, label)
            for item in bugs.values():
                row = bug_rows[item["id"]]
                # Activation belongs to the requirement where it happened.
                item["retest_activated"] = bool(row.retest_activated and row.retest_activated_req_id == req.id)
                item.update({
                    "source_type": _value(row.source_type),
                    "retest_activated_at": _iso(row.retest_activated_at),
                    "opened_at": _iso(row.zentao_opened_at or row.created_at),
                    "updated_at": _iso(row.zentao_remote_updated_at or row.updated_at),
                    "synced_at": _iso(row.last_zentao_synced_at),
                    "checked_at": _iso(row.last_zentao_checked_at),
                    "zentao_assigned_to_name": row.zentao_assigned_to_name or row.zentao_assigned_to_account,
                })
            bug_list = list(bugs.values())
            req_records = records_map[req.id]
            flags = workbench._retest_flags_for_req(
                req, bug_list, {b["id"] for b in evidence.get(req.id, [])},
                req_records[-1]["passed"] if req_records else None,
            )
            bug_list.sort(key=lambda b: (b["closed"], -datetime.fromisoformat(b["updated_at"]).timestamp(), b["id"]))
            retest_list = [b for b in bug_list if b.get("retest_problem_kind") or b["source_type"] == "retest"]
            retest_list.sort(key=lambda b: (not b.get("retest_problem", False), -(datetime.fromisoformat(b.get("retest_activated_at") or b["opened_at"]).timestamp()), b["id"]))
            tasks = tasks_map[req.id]
            summary = {
                "id": req.id, "zentao_req_id": req.zentao_req_id, "title": req.title,
                "owner_id": req.owner_id, "owner_name": req.owner.shown_name if req.owner else "未分配",
                "major_version_id": req.major_version_id, "major_version_name": req.major_version.version_no,
                "case_completed": bool(req.case_completed), "test_completed": bool(req.test_completed),
                "retest_completed": flags["retest_conclusion"] != "pending", **flags,
                "case_count": len(cases.get(req.id, [])), "bug_count": len(bug_list),
                "open_bug_count": sum(not b["closed"] for b in bug_list),
                "retest_bug_count": len(retest_list), "task_count": len(tasks),
                "task_done_count": sum(t["status"] in ("done", "closed") for t in tasks),
                "task_overdue_count": sum(t["overdue"] for t in tasks),
            }
            result[req.id] = {"requirement": summary, "bugs": bug_list, "retest_bugs": retest_list,
                              "tasks": tasks, "retest_records": req_records}
        return result

    def _tasks(self, reqs):
        story_ids = {r.zentao_story_id for r in reqs if r.zentao_story_id}
        own_ids = {task_id for r in reqs for task_id in (r.zentao_task_id, r.zentao_parent_task_id) if task_id}
        mirrors = self.db.query(ZentaoTaskMirror).filter(or_(
            ZentaoTaskMirror.story.in_(story_ids), ZentaoTaskMirror.task_id.in_(own_ids),
        )).all() if story_ids or own_ids else []
        by_id = {t.task_id: t for t in mirrors}
        by_story = defaultdict(list)
        for task in mirrors:
            by_story[task.story].append(task)
        local_map = defaultdict(list)
        for task in self.db.query(TaskBoardTask).options(joinedload(TaskBoardTask.assignee)).filter(
            TaskBoardTask.target_type == "requirement", TaskBoardTask.target_id.in_([r.id for r in reqs]),
            TaskBoardTask.archived.is_(False),
        ).all():
            local_map[task.target_id].append(task)
        result = {}
        now = local_now()
        today = now.date()
        for req in reqs:
            tasks = {}
            # Only include story-linked tasks from this version's execution.
            for row in by_story.get(req.zentao_story_id, []) if req.zentao_story_id else []:
                if row.execution_id != req.major_version.zentao_execution_id or row.is_parent or row.task_id == req.zentao_parent_task_id:
                    continue
                tasks[row.task_id] = row
            if req.zentao_task_id in by_id and not by_id[req.zentao_task_id].is_parent:
                row = by_id[req.zentao_task_id]
                if row.execution_id == req.major_version.zentao_execution_id:
                    tasks[row.task_id] = row
            items = []
            for task in tasks.values():
                parent = (task.parent or req.zentao_parent_task_id) if task.task_id == req.zentao_task_id else task.parent
                parent_row = by_id.get(parent)
                items.append({
                    "id": task.task_id, "source": "zentao", "name": task.name,
                    "type": task.type, "is_test_task": task.task_id == req.zentao_task_id,
                    "status": task.status, "assigned_to_name": task.assigned_to_realname or task.assigned_to,
                    "finished_by_name": task.finished_by_realname or task.finished_by,
                    "start_date": _iso(task.est_started), "deadline": _iso(task.deadline),
                    "finished_at": _iso(task.finished_date), "synced_at": _iso(task.synced_at),
                    "parent_id": parent or None, "parent_name": parent_row.name if parent_row else None,
                    "overdue": bool(task.deadline and task.deadline < today and task.status not in ("done", "closed", "cancel")),
                })
            if req.zentao_task_id and req.zentao_task_id not in tasks:
                # A known task without a scoped mirror stays visible as unsynced.
                items.append({"id": req.zentao_task_id, "source": "zentao", "name": "需求测试子任务",
                              "is_test_task": True, "status": req.zentao_task_status_cache,
                              "assigned_to_name": req.zentao_task_assigned_to,
                              "parent_id": req.zentao_parent_task_id, "synced_at": None, "overdue": False})
            for task in local_map[req.id]:
                if task.software_id not in (None, req.major_version.software_id) or task.major_version_id not in (None, req.major_version_id):
                    continue
                status = _value(task.status)
                items.append({
                    "id": task.id, "source": "local", "name": task.title, "description": task.description,
                    "status": status, "assigned_to_name": task.assignee.shown_name if task.assignee else None,
                    "start_date": _iso(task.started_at), "deadline": _iso(task.due_at),
                    "finished_at": _iso(task.completed_at), "updated_at": _iso(task.updated_at),
                    "overdue": bool(task.due_at and task.due_at < now and status != "done"),
                })
            items.sort(key=lambda t: (t["status"] in ("done", "closed", "cancel"), not t["overdue"], t["source"], t["id"]))
            result[req.id] = items
        return result

    def summary(self, major_version_id=None, software_id=None):
        if major_version_id:
            major_query = self.db.query(Version).filter(Version.id == major_version_id)
            if software_id:
                major_query = major_query.filter(Version.software_id == software_id)
            major = major_query.first()
            if major and major.final_test_enabled:
                return self._final_test_summary(major)
        reqs = self._query(major_version_id, software_id).order_by(Requirement.major_version_id, Requirement.id).all()
        collected = self._collect(reqs)
        owners = {}
        retest_by_major = {}
        for req in reqs:
            item = collected[req.id]["requirement"]
            owner = owners.setdefault(req.owner_id, {"owner_id": req.owner_id if req.owner_id is not None else -1, "owner_name": item["owner_name"], "requirements": [], "major_summaries": []})
            owner["requirements"].append(item)
            if item["test_completed"] and item["retest_conclusion"] == "pending":
                bucket = retest_by_major.setdefault(req.major_version_id, {"major_version_id": req.major_version_id, "major_version_name": item["major_version_name"], "pending_retest_count": 0})
                bucket["pending_retest_count"] += 1
        items = [v["requirement"] for v in collected.values()]
        # Preserve the existing summary shape used by the Android assignment view.
        for owner in owners.values():
            majors = {}
            for item in owner["requirements"]:
                bucket = majors.setdefault(item["major_version_id"], {
                    "major_version_id": item["major_version_id"], "major_version_name": item["major_version_name"],
                    "total_requirements": 0, "case_done": 0, "test_done": 0,
                })
                bucket["total_requirements"] += 1
                bucket["case_done"] += item["case_completed"]
                bucket["test_done"] += item["test_completed"]
            for bucket in majors.values():
                bucket["case_pending"] = bucket["total_requirements"] - bucket["case_done"]
                bucket["test_pending"] = bucket["total_requirements"] - bucket["test_done"]
            owner["major_summaries"] = sorted(majors.values(), key=lambda v: v["major_version_name"])
        case_done = sum(r.case_completed for r in reqs)
        test_done = sum(r.test_completed for r in reqs)
        return {
            "mode": "assignment",
            "summary": {"owners": sum(oid is not None for oid in owners), "requirements": len(reqs),
                        "case_done": case_done, "case_pending": len(reqs) - case_done,
                        "test_done": test_done, "test_pending": len(reqs) - test_done,
                        "retest_pending_total": sum(v["pending_retest_count"] for v in retest_by_major.values()),
                        "retest_failed_total": sum(i["retest_conclusion"] == "failed" for i in items),
                        "unassigned_total": sum(r.owner_id is None for r in reqs)},
            "owners": sorted(owners.values(), key=lambda o: (o["owner_id"] == -1, o["owner_name"])),
            "retest_pending_by_major": sorted(retest_by_major.values(), key=lambda v: v["major_version_name"]),
            "loaded_at": _iso(local_now()),
        }

    def _final_test_summary(self, major):
        """Final-test checkboxes belong to each person, never the assigned owner."""
        reqs = self.db.query(Requirement).filter(
            Requirement.major_version_id == major.id,
        ).order_by(Requirement.id).all()
        records = self.db.query(FinalTestRecord).filter(
            FinalTestRecord.requirement_id.in_([r.id for r in reqs]),
        ).all()
        states = {(r.user_id, r.requirement_id): r for r in records}
        # Include team members who have not started, plus existing participants.
        users = self.db.query(User).filter(or_(
            (User.is_active.is_(True) & User.is_team_member.is_(True)),
            User.id.in_({r.user_id for r in records}),
        )).all()
        owners = []
        for user in sorted(users, key=lambda u: (u.shown_name, u.id)):
            items = []
            for req in reqs:
                record = states.get((user.id, req.id))
                items.append({
                    "id": req.id, "zentao_req_id": req.zentao_req_id, "title": req.title,
                    "owner_id": user.id, "owner_name": user.shown_name,
                    "major_version_id": major.id, "major_version_name": major.version_no,
                    "case_completed": bool(record and record.case_completed),
                    "test_completed": bool(record and record.test_completed),
                })
            case_done = sum(item["case_completed"] for item in items)
            test_done = sum(item["test_completed"] for item in items)
            owners.append({
                "owner_id": user.id, "owner_name": user.shown_name, "requirements": items,
                "major_summaries": [{
                    "major_version_id": major.id, "major_version_name": major.version_no,
                    "total_requirements": len(reqs), "case_done": case_done, "test_done": test_done,
                    "case_pending": len(reqs) - case_done, "test_pending": len(reqs) - test_done,
                }],
            })
        total = len(reqs) * len(owners)
        case_done = sum(o["major_summaries"][0]["case_done"] for o in owners)
        test_done = sum(o["major_summaries"][0]["test_done"] for o in owners)
        return {
            "mode": "final_test",
            "summary": {
                "owners": len(owners), "requirements": len(reqs), "person_requirements": total,
                "case_done": case_done, "case_pending": total - case_done,
                "test_done": test_done, "test_pending": total - test_done,
            },
            "owners": owners, "loaded_at": _iso(local_now()),
        }

    def detail(self, requirement_id, major_version_id=None, software_id=None, *, bug_page=1, retest_page=1, task_page=1, page_size=20):
        req = self.get_requirement(requirement_id, major_version_id, software_id)
        data = self._collect([req])[req.id]
        for key, requested in (("bugs", bug_page), ("retest_bugs", retest_page), ("tasks", task_page)):
            rows = data[key]
            total = len(rows)
            page = min(requested, max(1, (total + page_size - 1) // page_size))
            data[key] = {"items": rows[(page - 1) * page_size:page * page_size], "total": total, "page": page, "page_size": page_size}
        data["loaded_at"] = _iso(local_now())
        return data

    def refresh(self, requirement_id, current_user, major_version_id=None, software_id=None):
        from app.services.workbench_refresh_service import WorkbenchRefreshService
        from app.services.zentao_task_mirror_service import ZentaoTaskMirrorService

        req = self.get_requirement(requirement_id, major_version_id, software_id)
        major_id = req.major_version_id
        req_software_id = req.major_version.software_id
        warnings = []
        if req_software_id:
            try:
                sync = WorkbenchRefreshService(self.db).preflight_refresh(
                    software_id=req_software_id, current_user=current_user, force=True, testcases_recent=True,
                )
                for key, label in (("bugs", "Bug"), ("testcases", "用例")):
                    result = sync.get(key) or {}
                    if result.get("ok") is False or result.get("error") or result.get("errors"):
                        warnings.append(f"{label}同步未完成：{result.get('error') or result.get('errors') or '请稍后重试'}")
            except Exception as exc:
                self.db.rollback()
                warnings.append(f"Bug/用例同步失败：{getattr(exc, 'detail', None) or exc}")
        else:
            warnings.append("需求版本未关联软件，无法同步 Bug/用例")
        try:
            result = ZentaoTaskMirrorService(self.db).sync_one_major(major_id)
            if not result.get("ok"):
                warnings.append(f"任务同步未完成：{result.get('error') or '请稍后重试'}")
        except Exception as exc:
            self.db.rollback()
            warnings.append(f"任务同步失败：{getattr(exc, 'detail', None) or exc}")
        self.db.expire_all()
        data = self.detail(requirement_id, major_version_id, software_id)
        data["warnings"] = warnings
        return data
