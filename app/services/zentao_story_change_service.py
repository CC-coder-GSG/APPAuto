"""Personal, version-checked acknowledgement of Zentao task requirements."""
from __future__ import annotations

import re

import httpx
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models import Requirement, User
from app.models.zentao_task_mirror import ZentaoTaskMirror
from app.services.audit_service import audit
from app.services.sse_service import sse_publish
from app.services.zentao_normalizer import normalize_story_detail
from app.services.zentao_system_client import get_user_zentao_web_login
from app.services.zentao_task_status import (
    apply_task_story_change, effective_task_status, task_story_change_pending,
)
from app.services.zentao_web_session import (
    ZentaoStoryChangeError, ZentaoWebSessionError,
    confirm_task_story_change_via_web, get_task_story_change_via_web,
)

_FILE_URL_RE = re.compile(r'(?:https?://)?[^"\'>\s]*/file-(?:read|download|preview)-(\d+)\.[a-zA-Z0-9]+')


class ZentaoStoryChangeService:
    def __init__(self, db: Session):
        self.db = db

    def _login(self, user: User):
        login = get_user_zentao_web_login(user.id, self.db)
        if not login:
            raise HTTPException(400, "请先绑定本人禅道账号或重新保存禅道登录凭据")
        return login

    def _validate_binding(self, task_id: int, story_id: int) -> None:
        mismatch = self.db.query(Requirement.id).filter(
            Requirement.zentao_task_id == task_id,
            Requirement.zentao_story_id.isnot(None),
            Requirement.zentao_story_id != story_id,
        ).first()
        if mismatch:
            raise HTTPException(409, "本地需求与禅道任务的需求关联不一致，请先修正关联后再确认")

    def _sync(self, task_id: int, task: dict) -> tuple[list[Requirement], dict]:
        reqs = self.db.query(Requirement).filter(Requirement.zentao_task_id == task_id).all()
        mirror = self.db.query(ZentaoTaskMirror).filter(ZentaoTaskMirror.task_id == task_id).first()
        account = task.get("assignedTo")
        account = account.get("account") if isinstance(account, dict) else account
        state = effective_task_status(task)
        # Confirmation changes acknowledgement only, never test results or clocks.
        for req in reqs:
            apply_task_story_change(req, task)
            req.zentao_task_status_cache = state
            req.zentao_task_assigned_to = account
        if mirror:
            from app.services.zentao_task_mirror_service import ZentaoTaskMirrorService
            ZentaoTaskMirrorService(self.db)._apply_task_data(mirror, task)
        return reqs, {
            "task_id": task_id, "status": state,
            "task_story_change_pending": task_story_change_pending(task),
            "task_story_version": int(task.get("storyVersion") or 0) or None,
            "task_latest_story_version": int(task.get("latestStoryVersion") or 0) or None,
        }

    @staticmethod
    def _story(raw: dict | None, version: int, base: str) -> dict | None:
        if raw is None:
            return None
        story = normalize_story_detail(raw, base_url=base)
        story["version"] = version
        story["changed_date"] = raw.get("changedDate") or ""
        story["changed_by"] = raw.get("changedBy") or ""
        for field in ("spec", "verify"):
            story[field] = _FILE_URL_RE.sub(lambda m: f"/zentao/files/{m.group(1)}", story.get(field) or "")
        # Attachments use the same authenticated file proxy as rich-text images.
        files = raw.get("files") or []
        if isinstance(files, dict):
            files = list(files.values())
        story["files"] = [
            {"id": f.get("id"), "title": f.get("title") or f.get("name") or "附件",
             "url": f"/zentao/files/{int(f['id'])}"}
            for f in files if isinstance(f, dict) and str(f.get("id") or "").isdigit()
        ]
        return story

    @staticmethod
    def _translate_error(exc: Exception) -> HTTPException:
        if isinstance(exc, ZentaoStoryChangeError):
            return HTTPException(exc.status_code, str(exc))
        return HTTPException(502, "无法连接禅道或本人登录失效，请刷新或重新绑定禅道账号")

    def preview(self, task_id: int, user: User) -> dict:
        login = self._login(user)
        try:
            result = get_task_story_change_via_web(login, task_id)
        except (ZentaoWebSessionError, httpx.HTTPError, ValueError, TypeError) as exc:
            raise self._translate_error(exc) from exc
        task = result["task"]
        self._validate_binding(task_id, int(task["story"]))
        reqs, state = self._sync(task_id, task)
        self.db.commit()
        sse_publish("zentao_task_changed", {
            **state, "action": "story_change_checked", "requirement_ids": [r.id for r in reqs],
        }, channels=["global"])
        return {
            **state, "task_name": task.get("name"), "story_id": int(task["story"]),
            "assigned_to": task.get("assignedTo"),
            "can_confirm": bool(result["can_confirm"] and state["task_story_change_pending"]),
            "confirm_disabled_reason": "" if result["can_confirm"] else "该任务未指派给你，请由当前指派人确认",
            "latest_story": self._story(result["story"], result["latest_story_version"], login.base_url),
            "previous_story": self._story(result["previous_story"], result["story_version"], login.base_url),
            "previous_error": result["previous_error"],
            "task_url": f"{login.base_url.rstrip('/')}/task-view-{task_id}.html",
        }

    def confirm(self, task_id: int, user: User, *, story_id: int,
                expected_story_version: int, expected_latest_story_version: int) -> dict:
        login = self._login(user)
        self._validate_binding(task_id, story_id)
        try:
            result = confirm_task_story_change_via_web(
                login, task_id, story_id=story_id, expected_story_version=expected_story_version,
                expected_latest_story_version=expected_latest_story_version,
            )
        except (ZentaoWebSessionError, httpx.HTTPError, ValueError, TypeError) as exc:
            raise self._translate_error(exc) from exc
        reqs, state = self._sync(task_id, result["task"])
        warning = bool(result["version_changed_during_confirm"])
        if not result["already_confirmed"]:
            audit(self.db, action="zentao_task.confirm_story_change", target_type="zentao_task",
                  actor_id=user.id, target_id=str(task_id),
                  detail=f"story={story_id},viewed={expected_latest_story_version},"
                         f"confirmed={state['task_story_version']},version_race={warning}")
        else:
            self.db.commit()
        sse_publish("zentao_task_changed", {
            **state, "action": "confirm_story_change", "requirement_ids": [r.id for r in reqs],
        }, channels=["global"])
        return {
            **state, "already_confirmed": result["already_confirmed"],
            "version_changed_during_confirm": warning,
            "message": "确认期间需求又发生变更，请重新查看最新内容" if warning
                       else f"已确认需求版本 v{state['task_story_version']}",
        }
