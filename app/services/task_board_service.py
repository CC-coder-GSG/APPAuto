from __future__ import annotations

from datetime import date, datetime
from typing import Any, Iterable, Optional

from sqlalchemy import and_
from sqlalchemy.orm import Session, joinedload

from app.core.exceptions import PermissionDenied, ValidationFailed
from app.models import (
    BugTracking,
    BuildRecord,
    FeedbackRecord,
    FieldTestRecord,
    Requirement,
    SoftwareProduct,
    TaskBoardPriority,
    TaskBoardStatus,
    TaskBoardTargetType,
    TaskBoardTask,
    TaskBoardUpdate,
    User,
    UserRole,
    Version,
)
from app.services.audit_service import audit
from app.services.permission_service import has_tab_access
from app.services.sse_service import sse_publish
from app.utils.time_utils import local_now


_COLUMN_ORDER = [
    TaskBoardStatus.TODO,
    TaskBoardStatus.IN_PROGRESS,
    TaskBoardStatus.BLOCKED,
    TaskBoardStatus.DONE,
    TaskBoardStatus.DEFERRED,
]


def _enum_value(raw: Any) -> Optional[str]:
    if raw is None:
        return None
    return getattr(raw, "value", str(raw))


def _is_admin(user: User | None) -> bool:
    if not user:
        return False
    return _enum_value(user.role) == UserRole.ADMIN.value


def can_manage_board(user: User | None) -> bool:
    """Create/edit/archive permission. Admin or the `assign` tab role."""
    if not user:
        return False
    if _is_admin(user):
        return True
    return has_tab_access(user, "assign")


def can_change_status(user: User | None, task: TaskBoardTask) -> bool:
    if not user:
        return False
    if can_manage_board(user):
        return True
    return task.assignee_id == user.id or task.assigner_id == user.id or task.created_by_id == user.id


class TaskBoardService:
    def __init__(self, db: Session):
        self.db = db

    # ─── helpers ──────────────────────────────────────────────────────────

    def _get_task(self, task_id: int) -> TaskBoardTask:
        task = (
            self.db.query(TaskBoardTask)
            .options(
                joinedload(TaskBoardTask.assignee),
                joinedload(TaskBoardTask.assigner),
                joinedload(TaskBoardTask.created_by),
                joinedload(TaskBoardTask.updated_by),
                joinedload(TaskBoardTask.software),
                joinedload(TaskBoardTask.major_version),
            )
            .filter(TaskBoardTask.id == task_id)
            .first()
        )
        if not task:
            raise ValidationFailed("任务不存在")
        return task

    def _validate_assignee(self, assignee_id: int | None) -> User | None:
        if assignee_id is None:
            return None
        user = self.db.query(User).filter(User.id == assignee_id).first()
        if not user:
            raise ValidationFailed("指派对象不存在")
        return user

    def _resolve_target_label(self, target_type: str | None, target_id: int | None) -> str | None:
        if not target_type or target_id is None:
            return None
        t = target_type.strip()
        if t == TaskBoardTargetType.REQUIREMENT.value:
            row = self.db.query(Requirement).filter(Requirement.id == target_id).first()
            return f"{row.zentao_req_id} {row.title}" if row else None
        if t == TaskBoardTargetType.BUG.value:
            row = self.db.query(BugTracking).filter(BugTracking.id == target_id).first()
            return row.bug_id if row else None
        if t == TaskBoardTargetType.FEEDBACK.value:
            row = self.db.query(FeedbackRecord).filter(FeedbackRecord.id == target_id).first()
            if not row:
                return None
            return f"{row.feedback_no or 'f#'+str(row.id)} {row.summary[:40] if row.summary else ''}".strip()
        if t == TaskBoardTargetType.FIELD_TEST.value:
            row = self.db.query(FieldTestRecord).filter(FieldTestRecord.id == target_id).first()
            return f"外业#{row.id}" if row else None
        if t == TaskBoardTargetType.BUILD_RECORD.value:
            row = self.db.query(BuildRecord).filter(BuildRecord.id == target_id).first()
            if not row:
                return None
            return f"{row.job_name} #{row.build_number}".strip()
        if t == TaskBoardTargetType.MANUAL.value:
            return None
        # unknown type: tolerate but don't cache
        return None

    def _validate_target(self, target_type: str | None, target_id: int | None) -> tuple[Optional[str], Optional[int], Optional[str]]:
        if not target_type or target_type == TaskBoardTargetType.MANUAL.value:
            return None, None, None
        if target_id is None:
            raise ValidationFailed("已选关联对象类型，需要同时填写对象 ID")
        allowed = {t.value for t in TaskBoardTargetType}
        if target_type not in allowed:
            raise ValidationFailed("不支持的关联对象类型")
        label = self._resolve_target_label(target_type, target_id)
        if label is None and target_type != TaskBoardTargetType.MANUAL.value:
            raise ValidationFailed("关联对象不存在")
        return target_type, target_id, label

    def serialize(self, task: TaskBoardTask, include_updates: bool = False) -> dict[str, Any]:
        data = {
            "id": task.id,
            "board_date": task.board_date.isoformat() if task.board_date else None,
            "title": task.title,
            "description": task.description,
            "status": _enum_value(task.status),
            "priority": _enum_value(task.priority),
            "assignee_id": task.assignee_id,
            "assignee_name": task.assignee.shown_name if task.assignee else None,
            "assigner_id": task.assigner_id,
            "assigner_name": task.assigner.shown_name if task.assigner else None,
            "created_by_id": task.created_by_id,
            "created_by_name": task.created_by.shown_name if task.created_by else None,
            "updated_by_id": task.updated_by_id,
            "updated_by_name": task.updated_by.shown_name if task.updated_by else None,
            "software_id": task.software_id,
            "software_name": task.software.name if task.software else None,
            "major_version_id": task.major_version_id,
            "major_version_name": task.major_version.version_no if task.major_version else None,
            "target_type": task.target_type,
            "target_id": task.target_id,
            "target_label": task.target_label_cache,
            "due_at": task.due_at.isoformat() if task.due_at else None,
            "started_at": task.started_at.isoformat() if task.started_at else None,
            "completed_at": task.completed_at.isoformat() if task.completed_at else None,
            "deferred_from_date": task.deferred_from_date.isoformat() if task.deferred_from_date else None,
            "sort_order": task.sort_order,
            "archived": bool(task.archived),
            "created_at": task.created_at.isoformat() if task.created_at else None,
            "updated_at": task.updated_at.isoformat() if task.updated_at else None,
        }
        # Overdue marker: due_at < now and not done/archived
        try:
            data["overdue"] = bool(
                task.due_at
                and not task.archived
                and _enum_value(task.status) != TaskBoardStatus.DONE.value
                and task.due_at < local_now()
            )
        except Exception:
            data["overdue"] = False

        if include_updates:
            updates = sorted(task.updates or [], key=lambda u: (u.created_at or datetime.min), reverse=True)
            data["updates"] = [
                {
                    "id": upd.id,
                    "content": upd.content,
                    "status_snapshot": upd.status_snapshot,
                    "author_id": upd.author_id,
                    "author_name": upd.author.shown_name if upd.author else None,
                    "created_at": upd.created_at.isoformat() if upd.created_at else None,
                }
                for upd in updates
            ]
        return data

    # ─── queries ──────────────────────────────────────────────────────────

    def list_board(
        self,
        *,
        board_date: date,
        software_id: int | None = None,
        major_version_id: int | None = None,
        assignee_id: int | None = None,
        status: str | None = None,
        mine_user_id: int | None = None,
        include_archived: bool = False,
    ) -> dict[str, Any]:
        q = (
            self.db.query(TaskBoardTask)
            .options(
                joinedload(TaskBoardTask.assignee),
                joinedload(TaskBoardTask.assigner),
                joinedload(TaskBoardTask.created_by),
                joinedload(TaskBoardTask.updated_by),
                joinedload(TaskBoardTask.software),
                joinedload(TaskBoardTask.major_version),
            )
            .filter(TaskBoardTask.board_date == board_date)
        )
        if software_id:
            q = q.filter(TaskBoardTask.software_id == software_id)
        if major_version_id:
            q = q.filter(TaskBoardTask.major_version_id == major_version_id)
        if assignee_id:
            q = q.filter(TaskBoardTask.assignee_id == assignee_id)
        if mine_user_id:
            q = q.filter(TaskBoardTask.assignee_id == mine_user_id)
        if status:
            q = q.filter(TaskBoardTask.status == status)
        if not include_archived:
            q = q.filter(TaskBoardTask.archived.is_(False))

        rows = q.order_by(TaskBoardTask.sort_order.asc(), TaskBoardTask.id.asc()).all()
        items = [self.serialize(row) for row in rows]

        columns: dict[str, list[dict[str, Any]]] = {s.value: [] for s in _COLUMN_ORDER}
        for item in items:
            col = item["status"] if item["status"] in columns else TaskBoardStatus.TODO.value
            columns[col].append(item)

        summary = {col: len(items_) for col, items_ in columns.items()}
        summary["total"] = len(items)

        assignee_summary: dict[int, dict[str, Any]] = {}
        for item in items:
            uid = item["assignee_id"] or 0
            bucket = assignee_summary.setdefault(
                uid,
                {
                    "assignee_id": uid,
                    "assignee_name": item["assignee_name"] or "未指派",
                    "open_count": 0,
                    "done_count": 0,
                    "total": 0,
                },
            )
            bucket["total"] += 1
            if item["status"] == TaskBoardStatus.DONE.value:
                bucket["done_count"] += 1
            else:
                bucket["open_count"] += 1

        return {
            "board_date": board_date.isoformat(),
            "summary": summary,
            "assignee_summary": sorted(assignee_summary.values(), key=lambda x: (-x["total"], x["assignee_name"])),
            "columns": columns,
        }

    def get_detail(self, task_id: int) -> dict[str, Any]:
        task = (
            self.db.query(TaskBoardTask)
            .options(
                joinedload(TaskBoardTask.assignee),
                joinedload(TaskBoardTask.assigner),
                joinedload(TaskBoardTask.created_by),
                joinedload(TaskBoardTask.updated_by),
                joinedload(TaskBoardTask.software),
                joinedload(TaskBoardTask.major_version),
                joinedload(TaskBoardTask.updates).joinedload(TaskBoardUpdate.author),
            )
            .filter(TaskBoardTask.id == task_id)
            .first()
        )
        if not task:
            raise ValidationFailed("任务不存在")
        return self.serialize(task, include_updates=True)

    def list_team_candidates(self) -> list[dict[str, Any]]:
        rows = (
            self.db.query(User)
            .filter(User.is_team_member.is_(True))
            .order_by(User.id.asc())
            .all()
        )
        return [{"id": u.id, "username": u.username, "display_name": u.shown_name} for u in rows]

    # ─── mutations ────────────────────────────────────────────────────────

    def create_task(
        self,
        *,
        actor: User,
        title: str,
        description: str | None = None,
        board_date: date | None = None,
        status: str | None = None,
        priority: str | None = None,
        assignee_id: int | None = None,
        software_id: int | None = None,
        major_version_id: int | None = None,
        target_type: str | None = None,
        target_id: int | None = None,
        due_at: datetime | None = None,
    ) -> dict[str, Any]:
        if not can_manage_board(actor):
            raise PermissionDenied("无任务派发权限")
        clean_title = (title or "").strip()
        if not clean_title:
            raise ValidationFailed("任务标题不能为空")
        if len(clean_title) > 200:
            raise ValidationFailed("任务标题过长（最多 200 字符）")

        self._validate_assignee(assignee_id)
        t_type, t_id, t_label = self._validate_target(target_type, target_id)

        try:
            status_enum = TaskBoardStatus(status) if status else TaskBoardStatus.TODO
        except ValueError as exc:
            raise ValidationFailed("非法任务状态") from exc
        try:
            priority_enum = TaskBoardPriority(priority) if priority else TaskBoardPriority.NORMAL
        except ValueError as exc:
            raise ValidationFailed("非法任务优先级") from exc

        row = TaskBoardTask(
            board_date=board_date or local_now().date(),
            title=clean_title,
            description=(description or None),
            status=status_enum,
            priority=priority_enum,
            assignee_id=assignee_id,
            assigner_id=actor.id if assignee_id else None,
            created_by_id=actor.id,
            updated_by_id=actor.id,
            software_id=software_id,
            major_version_id=major_version_id,
            target_type=t_type,
            target_id=t_id,
            target_label_cache=t_label,
            due_at=due_at,
        )
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)

        audit(
            self.db,
            action="task_board.create",
            target_type="task_board",
            actor_id=actor.id,
            target_id=str(row.id),
            detail=f"title={clean_title[:60]},assignee={assignee_id or ''},date={row.board_date.isoformat()}",
        )
        if assignee_id:
            audit(
                self.db,
                action="task_board.assign",
                target_type="task_board",
                actor_id=actor.id,
                target_id=str(row.id),
                detail=f"assignee=->{assignee_id}",
            )

        detail = self.get_detail(row.id)
        channels = ["global"]
        if assignee_id:
            channels.append(f"user:{assignee_id}")
        sse_publish("task_board_created", {"id": row.id, "task": detail}, channels=channels)
        return detail

    def update_task(self, *, actor: User, task_id: int, patch: dict[str, Any]) -> dict[str, Any]:
        if not can_manage_board(actor):
            raise PermissionDenied("无任务派发权限")
        task = self._get_task(task_id)

        old_assignee_id = task.assignee_id

        if "title" in patch:
            new_title = (patch["title"] or "").strip()
            if not new_title:
                raise ValidationFailed("任务标题不能为空")
            if len(new_title) > 200:
                raise ValidationFailed("任务标题过长（最多 200 字符）")
            task.title = new_title

        if "description" in patch:
            task.description = (patch["description"] or None)

        if "board_date" in patch and patch["board_date"]:
            task.board_date = patch["board_date"]

        if "priority" in patch and patch["priority"]:
            try:
                task.priority = TaskBoardPriority(patch["priority"])
            except ValueError as exc:
                raise ValidationFailed("非法任务优先级") from exc

        if "assignee_id" in patch:
            new_assignee = patch["assignee_id"]
            self._validate_assignee(new_assignee)
            task.assignee_id = new_assignee
            if new_assignee and new_assignee != old_assignee_id:
                task.assigner_id = actor.id

        if "due_at" in patch:
            task.due_at = patch["due_at"]
        if "software_id" in patch:
            task.software_id = patch["software_id"]
        if "major_version_id" in patch:
            task.major_version_id = patch["major_version_id"]
        if "sort_order" in patch and patch["sort_order"] is not None:
            task.sort_order = int(patch["sort_order"])

        if "target_type" in patch or "target_id" in patch:
            new_type = patch.get("target_type", task.target_type)
            new_id = patch.get("target_id", task.target_id)
            t_type, t_id, t_label = self._validate_target(new_type, new_id)
            task.target_type = t_type
            task.target_id = t_id
            task.target_label_cache = t_label

        task.updated_by_id = actor.id
        self.db.commit()
        self.db.refresh(task)

        audit(
            self.db,
            action="task_board.update",
            target_type="task_board",
            actor_id=actor.id,
            target_id=str(task.id),
            detail=f"fields={','.join(sorted(patch.keys()))}",
        )

        if "assignee_id" in patch and task.assignee_id != old_assignee_id:
            audit(
                self.db,
                action="task_board.assign",
                target_type="task_board",
                actor_id=actor.id,
                target_id=str(task.id),
                detail=f"assignee={old_assignee_id or ''}->{task.assignee_id or ''}",
            )

        detail = self.get_detail(task.id)
        channels = ["global"]
        if task.assignee_id:
            channels.append(f"user:{task.assignee_id}")
        if old_assignee_id and old_assignee_id != task.assignee_id:
            channels.append(f"user:{old_assignee_id}")
        sse_publish("task_board_updated", {"id": task.id, "task": detail}, channels=channels)
        return detail

    def update_status(
        self,
        *,
        actor: User,
        task_id: int,
        status: str,
        progress: str | None = None,
    ) -> dict[str, Any]:
        task = self._get_task(task_id)
        if not can_change_status(actor, task):
            raise PermissionDenied("仅创建人/派发人/被指派人或管理员可更新状态")
        try:
            status_enum = TaskBoardStatus(status)
        except ValueError as exc:
            raise ValidationFailed("非法任务状态") from exc

        old_status = _enum_value(task.status)
        now = local_now()

        task.status = status_enum
        if status_enum == TaskBoardStatus.IN_PROGRESS and task.started_at is None:
            task.started_at = now
        if status_enum == TaskBoardStatus.DONE:
            task.completed_at = now
        elif old_status == TaskBoardStatus.DONE.value and status_enum != TaskBoardStatus.DONE:
            task.completed_at = None
        task.updated_by_id = actor.id

        clean_progress = (progress or "").strip() or None
        if clean_progress:
            self.db.add(
                TaskBoardUpdate(
                    task_id=task.id,
                    author_id=actor.id,
                    content=clean_progress,
                    status_snapshot=status_enum.value,
                )
            )

        self.db.commit()
        self.db.refresh(task)

        audit(
            self.db,
            action="task_board.status",
            target_type="task_board",
            actor_id=actor.id,
            target_id=str(task.id),
            detail=f"{old_status}->{status_enum.value}",
        )
        if clean_progress:
            audit(
                self.db,
                action="task_board.progress",
                target_type="task_board",
                actor_id=actor.id,
                target_id=str(task.id),
                detail=clean_progress[:200],
            )

        detail = self.get_detail(task.id)
        channels = ["global"]
        if task.assignee_id:
            channels.append(f"user:{task.assignee_id}")
        sse_publish(
            "task_board_status_changed",
            {"id": task.id, "task": detail, "from": old_status, "to": status_enum.value},
            channels=channels,
        )
        return detail

    def add_progress(self, *, actor: User, task_id: int, content: str) -> dict[str, Any]:
        task = self._get_task(task_id)
        if not can_change_status(actor, task):
            raise PermissionDenied("仅创建人/派发人/被指派人或管理员可追加进展")
        clean = (content or "").strip()
        if not clean:
            raise ValidationFailed("进展内容不能为空")

        upd = TaskBoardUpdate(
            task_id=task.id,
            author_id=actor.id,
            content=clean,
            status_snapshot=_enum_value(task.status),
        )
        self.db.add(upd)
        task.updated_by_id = actor.id
        self.db.commit()

        audit(
            self.db,
            action="task_board.progress",
            target_type="task_board",
            actor_id=actor.id,
            target_id=str(task.id),
            detail=clean[:200],
        )

        detail = self.get_detail(task.id)
        channels = ["global"]
        if task.assignee_id:
            channels.append(f"user:{task.assignee_id}")
        sse_publish("task_board_updated", {"id": task.id, "task": detail}, channels=channels)
        return detail

    def archive_task(self, *, actor: User, task_id: int) -> dict[str, Any]:
        if not can_manage_board(actor):
            raise PermissionDenied("无任务派发权限")
        task = self._get_task(task_id)
        task.archived = True
        task.updated_by_id = actor.id
        self.db.commit()
        audit(
            self.db,
            action="task_board.archive",
            target_type="task_board",
            actor_id=actor.id,
            target_id=str(task.id),
        )
        detail = self.get_detail(task.id)
        sse_publish("task_board_archived", {"id": task.id, "task": detail}, channels=["global"])
        return detail

    def carry_over(self, *, actor: User, task_id: int, to_date: date) -> dict[str, Any]:
        """
        Push an unfinished task to a future day. Marks the row as DEFERRED on
        its current day's board context (via `deferred_from_date`) and moves
        `board_date` forward. Useful for the "把今天没做完的事推到明天" flow.
        """
        if not can_manage_board(actor):
            raise PermissionDenied("无任务派发权限")
        task = self._get_task(task_id)
        if _enum_value(task.status) == TaskBoardStatus.DONE.value:
            raise ValidationFailed("已完成的任务无需顺延")
        old_date = task.board_date
        task.deferred_from_date = old_date
        task.board_date = to_date
        task.status = TaskBoardStatus.DEFERRED
        task.updated_by_id = actor.id
        self.db.commit()
        self.db.refresh(task)

        audit(
            self.db,
            action="task_board.update",
            target_type="task_board",
            actor_id=actor.id,
            target_id=str(task.id),
            detail=f"defer={old_date.isoformat()}->{to_date.isoformat()}",
        )
        detail = self.get_detail(task.id)
        channels = ["global"]
        if task.assignee_id:
            channels.append(f"user:{task.assignee_id}")
        sse_publish("task_board_updated", {"id": task.id, "task": detail}, channels=channels)
        return detail


__all__ = ["TaskBoardService", "can_manage_board", "can_change_status"]
