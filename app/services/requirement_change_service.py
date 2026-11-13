"""Dispatch change retests independently of personal final-test checkboxes."""
from __future__ import annotations

import json

from fastapi import HTTPException

from app.models import FinalTestRecord, Requirement, RequirementChangeRetest, User, ZentaoTaskMirror
from app.services.permission_service import get_allowed_tabs
from app.services.sync_lock_service import sync_lock
from app.services.zentao_task_schedule_service import _id
from app.services.zentao_task_status import effective_task_status, task_story_change_pending


def account(value):
    if isinstance(value, dict):
        value = value.get('account')
    return str(value or '').strip()


class RequirementChangeService:
    def __init__(self, db):
        self.db = db

    @staticmethod
    def can_manage(user):
        return 'assign' in get_allowed_tabs(user)

    def requirement(self, requirement_id):
        req = self.db.get(Requirement, requirement_id)
        if req is None:
            raise HTTPException(404, '需求不存在')
        if not req.major_version.final_test_enabled:
            raise HTTPException(400, '该需求未处于最终测试阶段')
        if not req.zentao_story_id or not req.major_version.zentao_execution_id:
            raise HTTPException(400, '请先关联研发需求及大版本禅道执行')
        return req

    def preview(self, requirement_id, user):
        req = self.requirement(requirement_id)
        receipts = self.db.query(RequirementChangeRetest).filter_by(requirement_id=req.id).all()
        retry_ids = {r.source_task_id for r in receipts if r.status != 'active'}
        from sqlalchemy import or_, and_
        rows = self.db.query(ZentaoTaskMirror).filter(
            ZentaoTaskMirror.story == req.zentao_story_id,
            ZentaoTaskMirror.type == 'test', ZentaoTaskMirror.is_parent == 0,
            or_(and_(ZentaoTaskMirror.status.in_(['done', 'closed']),
                     ZentaoTaskMirror.task_story_change_pending.is_(True)),
                ZentaoTaskMirror.task_id.in_(retry_ids)),
        ).order_by(ZentaoTaskMirror.task_id).all()
        users = self.db.query(User).filter(User.is_active.is_(True), User.zentao_account.isnot(None)).all()
        by_account = {u.zentao_account.strip().lower(): u for u in users if u.zentao_account.strip()}
        from app.services.zentao_system_client import get_system_zentao_client
        from app.services.zentao_task_mirror_service import ZentaoTaskMirrorService
        client = get_system_zentao_client(self.db) if rows else None
        tasks = []
        for row in rows:
            # Refresh acknowledgement versions without making any task lifecycle change.
            page = None
            if client:
                try:
                    live = client.get_task(row.task_id)
                    self.validate_task(live, req, row.task_id)
                    ZentaoTaskMirrorService(self.db)._apply_task_data(row, live)
                    if hasattr(client, 'get_page'):
                        page = client.get_page(f'task-view-{row.task_id}.json')
                except Exception:
                    pass  # Cached suggestions are advisory; dispatch always validates live state.
            existing = next((r for r in receipts if r.source_task_id == row.task_id
                             and r.story_version == row.task_latest_story_version), None)
            existing = existing or next((r for r in receipts if r.source_task_id == row.task_id
                                        and r.status != 'active'), None)
            if existing and existing.status == 'active':
                continue
            # Closed is a sentinel, never an owner. Completion is only a suggestion.
            owner = by_account.get((row.assigned_to or '').strip().lower())
            reason = '当前指派人' if owner else ''
            historic = self.previous_assignee(page)
            if historic and historic.lower() in by_account:
                owner = by_account[historic.lower()]
                reason = '关闭前的指派记录'
            if owner is None:
                owner = by_account.get((row.finished_by or '').strip().lower())
                reason = '上次完成者（请核对原负责人）' if owner else '未找到原负责人，请手动选择'
            if existing:
                owner = next((u for u in users if u.id == existing.owner_id), None)
                reason = '上次派发负责人（重试将复用原任务）'
            tasks.append({
                'task_id': row.task_id, 'name': row.name, 'status': row.status,
                'execution_id': row.execution_id,
                'execution_name': row.execution_name_cache or str(row.execution_id),
                'story_version': row.task_story_version, 'latest_story_version': row.task_latest_story_version,
                'mode': 'reactivate' if row.execution_id == req.major_version.zentao_execution_id else 'create',
                'suggested_owner_id': owner.id if owner else None, 'owner_reason': reason,
            })
        self.db.commit()
        return {'requirement_id': req.id, 'title': req.title, 'major_version_name': req.major_version.version_no,
                'can_manage': self.can_manage(user), 'tasks': tasks,
                'owners': [{'id': u.id, 'name': u.shown_name} for u in users if u.zentao_account.strip()],
                'rounds': self.summaries([req.id]).get(req.id, [])}

    @staticmethod
    def previous_assignee(page):
        """Use recorded assignment changes, never the actor who closed a task."""
        if not isinstance(page, dict):
            return None
        actions = page.get('actions') or {}
        if isinstance(actions, dict):
            actions = list(actions.values())
        if not isinstance(actions, list):
            return None
        for action in sorted((a for a in actions if isinstance(a, dict)),
                             key=lambda a: (str(a.get('date') or ''), _id(a.get('id'))), reverse=True):
            history = action.get('history') or []
            if isinstance(history, dict):
                history = list(history.values())
            for item in history if isinstance(history, list) else []:
                if not isinstance(item, dict) or item.get('field') != 'assignedTo':
                    continue
                for value in [item.get('new'), item.get('old')]:
                    candidate = account(value)
                    if candidate and candidate.lower() not in {'closed', 'deleted', 'guest'}:
                        return candidate
        return None

    def summaries(self, requirement_ids):
        if not requirement_ids:
            return {}
        from app.services.workbench_link_service import _load_matching
        rows = _load_matching(self.db.query(RequirementChangeRetest),
                              [(RequirementChangeRetest.requirement_id, set(requirement_ids))])
        tasks = {t.task_id: t for t in _load_matching(self.db.query(ZentaoTaskMirror),
                 [(ZentaoTaskMirror.task_id, {r.task_id for r in rows if r.task_id})], identity='task_id')}
        users = {u.id: u for u in _load_matching(self.db.query(User),
                 [(User.id, {r.owner_id for r in rows})])}
        records = {(r.requirement_id, r.user_id): r for r in _load_matching(self.db.query(FinalTestRecord),
                   [(FinalTestRecord.requirement_id, set(requirement_ids))])} if rows else {}
        result = {}
        for row in sorted(rows, key=lambda r: r.id):
            task = tasks.get(row.task_id)
            personal = records.get((row.requirement_id, row.owner_id))
            completed = bool(row.status == 'active' and task and task.status in {'done', 'closed'}
                             and task.task_story_change_pending is False
                             and (task.task_story_version or 0) >= row.story_version
                             and personal and personal.case_completed and personal.test_completed)
            previous = json.loads(row.previous_state) if row.previous_state else {}
            result.setdefault(row.requirement_id, []).append({
                'id': row.id, 'source_task_id': row.source_task_id, 'task_id': row.task_id,
                'story_version': row.story_version, 'owner_id': row.owner_id,
                'owner_name': users[row.owner_id].shown_name if row.owner_id in users else '未知',
                'status': 'completed' if completed else row.status,
                'previous_test_completed': bool(previous.get('test_completed')),
                'previous_test_completed_at': previous.get('test_completed_at'),
            })
        return result

    @staticmethod
    def validate_task(task, req, task_id):
        if (not isinstance(task, dict) or _id(task.get('id')) != task_id
                or _id(task.get('story')) != req.zentao_story_id or task.get('type') != 'test'
                or task.get('isParent') in (True, 1, '1') or task.get('deleted') in (True, 1, '1')
                or task.get('team') or task.get('mode') in {'linear', 'multi'}):
            raise HTTPException(409, '任务关联、类型或成员已变化，请刷新后处理；多人任务请在禅道处理')

    def dispatch(self, requirement_id, user, *, source_task_id, owner_id, expected_story_version):
        if not self.can_manage(user):
            raise HTTPException(403, '仅有需求分配权限的人员可派发变更重测')
        with sync_lock(self.db, f'change_retest:{requirement_id}', ttl_seconds=600) as acquired:
            if not acquired:
                raise HTTPException(409, '该需求正在派发变更重测，请稍后刷新')
            try:
                return self._dispatch(requirement_id, user, source_task_id, owner_id, expected_story_version)
            except HTTPException:
                raise
            except Exception as exc:
                raise HTTPException(502, '无法核对禅道任务或可指派人员，请稍后重试') from exc

    def _dispatch(self, requirement_id, user, source_task_id, owner_id, expected_story_version):
        from app.services.zentao_system_client import get_system_zentao_client
        from app.services.zentao_task_mirror_service import ZentaoTaskMirrorService
        from app.services.requirement_service import RequirementService
        from app.services.audit_service import audit
        from app.services.sse_service import sse_publish
        from app.utils.time_utils import local_now

        req = self.requirement(requirement_id)
        owner = self.db.get(User, owner_id)
        if not owner or not owner.is_active or not (owner.zentao_account or '').strip():
            raise HTTPException(400, '负责人未启用或未配置禅道账号')
        wanted = owner.zentao_account.strip()
        client = get_system_zentao_client(self.db)
        if client is None:
            raise HTTPException(502, '找不到可用的禅道管理账号')
        source = client.get_task(source_task_id)
        self.validate_task(source, req, source_task_id)
        if not _id(source.get('execution')):
            raise HTTPException(409, '无法确认源任务所属执行，请先核对禅道任务')
        if _id(source.get('latestStoryVersion')) != expected_story_version:
            raise HTTPException(409, '需求版本已变化，请重新查看变更后派发')
        execution = req.major_version.zentao_execution_id
        assignable = client.list_assignable_users(execution)
        if wanted.lower() not in {a.strip().lower() for a in assignable}:
            raise HTTPException(400, '所选负责人不在当前大版本的禅道可指派人员中')
        receipt = self.db.query(RequirementChangeRetest).filter_by(
            requirement_id=req.id, source_task_id=source_task_id, story_version=expected_story_version).first()
        receipt = receipt or self.db.query(RequirementChangeRetest).filter(
            RequirementChangeRetest.requirement_id == req.id,
            RequirementChangeRetest.source_task_id == source_task_id,
            RequirementChangeRetest.status != 'active').order_by(RequirementChangeRetest.id.desc()).first()
        if receipt and receipt.owner_id != owner_id:
            raise HTTPException(409, '该变更已派发给其他人员，请刷新查看')
        if receipt and receipt.status == 'active':
            return {'ok': True, 'task_id': receipt.task_id, 'already_dispatched': True}
        if receipt:
            receipt.story_version = expected_story_version
            self.db.commit()
        create = _id(source.get('execution')) != execution
        fresh = receipt is None
        if fresh:
            if effective_task_status(source) not in {'done', 'closed'} or task_story_change_pending(source) is not True:
                raise HTTPException(409, '仅可处理已完成或关闭且待确认变更的测试任务')
            receipt = RequirementChangeRetest(requirement_id=req.id, source_task_id=source_task_id,
                story_version=expected_story_version, owner_id=owner_id, actor_id=user.id,
                task_id=None if create else source_task_id, status='prepared')
            self.db.add(receipt)
            self.db.commit()  # Reserve before external writes; retries reuse this receipt.
        marker = f'[change-retest:{receipt.id}]'
        try:
            if create:
                # A timed-out create may have succeeded: recover by durable marker, never blindly create twice.
                matches = [t for t in client.list_execution_tasks(execution)
                           if marker in str(t.get('desc') or '') or marker in str(t.get('name') or '')]
                if len(matches) > 1:
                    raise HTTPException(409, '发现多个相同重测任务，请先在禅道核对')
                task = client.get_task(receipt.task_id) if receipt.task_id else (matches[0] if matches else None)
                if task is None:
                    if receipt.status != 'prepared':
                        raise HTTPException(409, '上次创建结果待核实，暂未查到重测任务；请核对禅道，勿重复创建')
                    receipt.status = 'unknown'
                    self.db.commit()
                    task = client.create_execution_task(execution,
                        name=f'[变更重测 v{expected_story_version}] {req.title[:160]} {marker}',
                        assigned_to=wanted, task_type='test', story=req.zentao_story_id,
                        estimate=req.estimated_test_hours or 4,
                        desc=f'{marker} 源任务 #{source_task_id}；需求 #{req.zentao_story_id} v{expected_story_version}。')
                receipt.task_id = _id(task.get('id')) or None
                self.db.commit()
                if not receipt.task_id:
                    raise HTTPException(502, '禅道未返回新任务编号，结果待核实')
            else:
                task = client.get_task(source_task_id)
                self.validate_task(task, req, source_task_id)
                if effective_task_status(task) in {'done', 'closed'}:
                    # This explicit management action alone allows reopening before acknowledgement.
                    # It never acknowledges a requirement on behalf of its next assignee.
                    client.restart_task(source_task_id, consumed=float(task.get('consumed') or 0),
                                        left=req.estimated_test_hours or 4, assigned_to=wanted)
            task = client.get_task(receipt.task_id)
            self.validate_task(task, req, receipt.task_id)
            if _id(task.get('execution')) != execution:
                raise HTTPException(409, '重测任务不属于当前大版本')
            if account(task.get('assignedTo')).lower() != wanted.lower():
                client.reassign_task(receipt.task_id, wanted)
                task = client.get_task(receipt.task_id)
                self.validate_task(task, req, receipt.task_id)
            if (effective_task_status(task) not in {'wait', 'doing', 'changed'}
                    or account(task.get('assignedTo')).lower() != wanted.lower()
                    or _id(task.get('execution')) != execution):
                raise HTTPException(502, '禅道任务未确认恢复或指派未生效，请刷新后重试')
            mirror = self.db.get(ZentaoTaskMirror, receipt.task_id)
            if mirror is None:
                mirror = ZentaoTaskMirror(task_id=receipt.task_id, execution_id=execution,
                    name=task.get('name') or req.title, story=req.zentao_story_id, type='test')
                self.db.add(mirror)
            ZentaoTaskMirrorService(self.db)._apply_task_data(mirror, task)
            # Old submitted effort stays in Zentao; only the new local timing segment starts here.
            mirror.local_started_at = local_now() if effective_task_status(task) in {'doing', 'changed'} else None
            mirror.consumed_accum = 0
            mirror.efforts_submitted = 0
            previous = self.db.query(FinalTestRecord).filter_by(requirement_id=req.id, user_id=owner_id).first()
            receipt.previous_state = json.dumps({key: (value.isoformat() if hasattr(value, 'isoformat') else value)
                for key in ['case_completed', 'test_completed', 'testing', 'test_completed_at']
                for value in [getattr(previous, key, None)]}, ensure_ascii=False)
            if previous:
                previous.case_completed = False
                previous.test_completed = False
                previous.testing = False
                previous.test_completed_at = None
            receipt.status = 'active'
            if req.zentao_task_id == receipt.task_id:
                req.zentao_task_status_cache = mirror.status
                req.zentao_task_assigned_to = wanted
                req.task_started_at = mirror.local_started_at
                req.task_consumed_accum = 0
                req.task_efforts_submitted = 0
            RequirementService(self.db).reopen_after_task_reactivation(req, actor_id=user.id)
            self.db.commit()
        except Exception as exc:
            self.db.rollback()
            receipt = self.db.get(RequirementChangeRetest, receipt.id)
            if receipt.status != 'active' and (not create or receipt.status != 'prepared'):
                receipt.status = 'unknown'
                self.db.commit()
            if isinstance(exc, HTTPException):
                raise
            raise HTTPException(502, '禅道处理结果待核实，请刷新后重试；已有任务将被复用') from exc
        audit(self.db, action='requirement.dispatch_change_retest', target_type='requirement',
              actor_id=user.id, target_id=str(req.id),
              detail=f'source_task={source_task_id},task={receipt.task_id},owner={owner_id},version={expected_story_version}')
        sse_publish('zentao_task_changed', {'task_id': receipt.task_id, 'requirement_ids': [req.id],
                    'action': 'change_retest'}, channels=['global'])
        return {'ok': True, 'task_id': receipt.task_id, 'created': create}
