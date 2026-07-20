from __future__ import annotations

from datetime import datetime

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from app.models import BugSourceType, BugTracking, Requirement, RequirementStatus, RequirementStatusHistory, TestCase, TestExecution, TestResultStatus, User, UserRole, Version, VersionType, ZentaoTestCaseMirror
from app.models.user_zentao_binding import UserZentaoBinding
from app.services.audit_service import audit
from app.services.sse_service import sse_publish
from app.services.zentao_auth_service import get_valid_token
from app.services.zentao_client_service import ZentaoAPIError, ZentaoClient
from app.utils.time_utils import local_now
from app.utils.state_machine import ensure_requirement_transition
from app.utils.validators import validate_req_id


class RequirementService:
    def __init__(self, db: Session):
        self.db = db

    def ensure_major_version_exists(self, major_version_id: int) -> Version:
        version = self.db.query(Version).filter(Version.id == major_version_id).first()
        if not version:
            raise HTTPException(status_code=400, detail="关联的大版本不存在")
        if version.version_type != VersionType.MAJOR:
            raise HTTPException(status_code=400, detail="major_version_id 必须指向大版本")
        return version

    def _get_zentao_client(self, user_id: int) -> ZentaoClient | None:
        binding = self.db.query(UserZentaoBinding).filter(UserZentaoBinding.user_id == user_id).first()
        if not binding or not binding.base_url:
            return None
        token = get_valid_token(user_id, self.db)
        if not token:
            return None
        return ZentaoClient(base_url=binding.base_url.rstrip("/"), token=token)

    def _fetch_execution_story_rows(self, client: ZentaoClient, execution_id: int) -> list[dict]:
        candidate_paths = [
            (f"executions/{execution_id}/stories", {"limit": 500}),
            (f"executions/{execution_id}/requirements", {"limit": 500}),
            (f"executions/{execution_id}", {"limit": 500}),
        ]
        for path, params in candidate_paths:
            try:
                data = client.get(path, params=params) or {}
            except ZentaoAPIError:
                continue
            rows = None
            if isinstance(data, dict):
                for key in ("stories", "requirements", "data", "items"):
                    bucket = data.get(key)
                    if isinstance(bucket, list):
                        rows = bucket
                        break
                    if isinstance(bucket, dict):
                        rows = [v for v in bucket.values() if isinstance(v, dict)]
                        break
            elif isinstance(data, list):
                rows = [v for v in data if isinstance(v, dict)]
            if rows:
                return rows
        return []

    def sync_zentao_major_requirements(self, major_version_id: int, current_user: User) -> dict:
        major = self.ensure_major_version_exists(major_version_id)
        if not major.zentao_execution_id:
            raise HTTPException(status_code=400, detail="当前大版本尚未绑定禅道 execution，无法同步需求")

        client = self._get_zentao_client(current_user.id)
        if client is None:
            raise HTTPException(status_code=400, detail="当前用户未配置可用的禅道绑定")

        story_rows = self._fetch_execution_story_rows(client, major.zentao_execution_id)
        if not story_rows:
            return {"remote_total": 0, "created": 0, "updated": 0}

        existing_rows = (
            self.db.query(Requirement)
            .filter(Requirement.major_version_id == major_version_id)
            .all()
        )
        existing_by_story_id = {int(r.zentao_story_id): r for r in existing_rows if r.zentao_story_id}
        existing_by_req_id = {r.zentao_req_id: r for r in existing_rows}
        account_to_user_id = {
            (b.zentao_account or "").strip(): b.user_id
            for b in self.db.query(UserZentaoBinding).filter(UserZentaoBinding.zentao_account.isnot(None)).all()
        }

        created = 0
        updated = 0

        for story in story_rows:
            story_id = story.get("id")
            if not story_id:
                continue
            try:
                story_id_int = int(story_id)
            except Exception:
                continue

            title = str(story.get("title") or "").strip()
            if not title:
                continue

            req_no = f"r#{story_id_int}"
            assigned_to = story.get("assignedTo") or {}
            if isinstance(assigned_to, dict):
                assigned_account = str(assigned_to.get("account") or "").strip()
            else:
                assigned_account = str(assigned_to or "").strip()
            mapped_owner_id = account_to_user_id.get(assigned_account) if assigned_account else None

            plan = story.get("plan")
            plan_id = None
            plan_title = None
            if isinstance(plan, dict) and plan:
                first_key = next(iter(plan.keys()))
                try:
                    plan_id = int(first_key)
                except Exception:
                    plan_id = None
                plan_title = str(plan.get(first_key) or "").strip() or None

            row = existing_by_story_id.get(story_id_int) or existing_by_req_id.get(req_no)
            if row:
                changed = False
                if row.title != title:
                    row.title = title
                    changed = True
                if row.zentao_story_id != story_id_int:
                    row.zentao_story_id = story_id_int
                    changed = True
                if row.zentao_plan_id != plan_id:
                    row.zentao_plan_id = plan_id
                    changed = True
                if row.zentao_plan_title_cache != plan_title:
                    row.zentao_plan_title_cache = plan_title
                    changed = True
                if mapped_owner_id and row.owner_id is None:
                    row.owner_id = mapped_owner_id
                    self.recalculate_requirement_status(row, actor_id=current_user.id)
                    changed = True
                if changed:
                    updated += 1
                continue

            new_req = Requirement(
                zentao_req_id=req_no,
                title=title,
                major_version_id=major_version_id,
                owner_id=mapped_owner_id,
                status=RequirementStatus.PENDING,
                zentao_story_id=story_id_int,
                zentao_plan_id=plan_id,
                zentao_plan_title_cache=plan_title,
            )
            self.db.add(new_req)
            self.db.flush()
            self.recalculate_requirement_status(new_req, actor_id=current_user.id)
            self._record_status_history(new_req, None, new_req.status, actor_id=current_user.id)
            existing_by_story_id[story_id_int] = new_req
            existing_by_req_id[req_no] = new_req
            created += 1

        self.db.commit()
        audit(
            self.db,
            action="requirement.sync_zentao_major",
            target_type="version",
            actor_id=current_user.id,
            target_id=str(major_version_id),
            detail=f"remote_total={len(story_rows)},created={created},updated={updated}",
        )
        return {"remote_total": len(story_rows), "created": created, "updated": updated}

    def create_requirement(self, zentao_req_id: str, title: str, major_version_id: int, actor_id: int | None = None) -> dict:
        self.ensure_major_version_exists(major_version_id)
        if not validate_req_id(zentao_req_id):
            raise HTTPException(status_code=400, detail="zentao_req_id must be like r#xxxx")
        if self.db.query(Requirement).filter(Requirement.major_version_id == major_version_id, Requirement.zentao_req_id == zentao_req_id).first():
            raise HTTPException(status_code=400, detail="Requirement already exists")

        requirement = Requirement(
            zentao_req_id=zentao_req_id,
            title=title,
            major_version_id=major_version_id,
            status=RequirementStatus.PENDING,
        )
        self.db.add(requirement)
        self.db.commit()
        self.db.refresh(requirement)
        self._record_status_history(requirement, None, RequirementStatus.PENDING, actor_id=actor_id)
        self.db.commit()
        audit(
            self.db,
            action="requirement.create",
            target_type="requirement",
            actor_id=actor_id,
            target_id=str(requirement.id),
            detail=requirement.zentao_req_id,
        )
        sse_publish(
            "workbench_requirement_created",
            {
                "id": requirement.id,
                "zentao_req_id": requirement.zentao_req_id,
                "title": requirement.title,
                "major_version_id": requirement.major_version_id,
                "owner_id": requirement.owner_id,
            },
            channels=["global", f"user:{requirement.owner_id}"] if requirement.owner_id else ["global"],
        )
        return {
            "id": requirement.id,
            "zentao_req_id": requirement.zentao_req_id,
            "title": requirement.title,
            "status": requirement.status,
        }

    def batch_create_requirements(self, major_version_id: int, items: list[dict], actor_id: int | None = None) -> dict:
        self.ensure_major_version_exists(major_version_id)
        existing_reqs = {
            r[0] for r in self.db.query(Requirement.zentao_req_id).filter(Requirement.major_version_id == major_version_id).all()
        }

        new_reqs: list[Requirement] = []
        for item in items:
            req_id = item["zentao_req_id"]
            title = item["title"]
            if not validate_req_id(req_id) or req_id in existing_reqs:
                continue
            new_reqs.append(
                Requirement(
                    zentao_req_id=req_id,
                    title=title,
                    major_version_id=major_version_id,
                    status=RequirementStatus.PENDING,
                )
            )
            existing_reqs.add(req_id)

        if new_reqs:
            self.db.bulk_save_objects(new_reqs)
            self.db.commit()
            created_rows = (
                self.db.query(Requirement)
                .filter(Requirement.major_version_id == major_version_id, Requirement.zentao_req_id.in_([r.zentao_req_id for r in new_reqs]))
                .all()
            )
            for row in created_rows:
                self._record_status_history(row, None, RequirementStatus.PENDING, actor_id=actor_id)
            self.db.commit()
            audit(
                self.db,
                action="requirement.batch_create",
                target_type="version",
                actor_id=actor_id,
                target_id=str(major_version_id),
                detail=f"count={len(new_reqs)}",
            )

        return {"message": "导入成功", "count": len(new_reqs)}

    def list_requirements_for_link(self, source_major_version_id: int, target_major_version_id: int) -> list[dict]:
        self.ensure_major_version_exists(source_major_version_id)
        self.ensure_major_version_exists(target_major_version_id)
        source_rows = (
            self.db.query(Requirement)
            .options(joinedload(Requirement.owner), joinedload(Requirement.test_cases))
            .filter(Requirement.major_version_id == source_major_version_id)
            .order_by(Requirement.id.asc())
            .all()
        )
        target_ids = {
            r[0]
            for r in self.db.query(Requirement.zentao_req_id).filter(Requirement.major_version_id == target_major_version_id).all()
        }
        return [
            {
                "id": r.id,
                "zentao_req_id": r.zentao_req_id,
                "title": r.title,
                "owner_id": r.owner_id,
                "owner_name": r.owner.shown_name if r.owner else "未分配",
                "case_count": len(r.test_cases or []),
                "already_linked": r.zentao_req_id in target_ids,
            }
            for r in source_rows
        ]

    def link_requirements_from_major(
        self,
        *,
        target_major_version_id: int,
        source_major_version_id: int,
        source_requirement_ids: list[int],
        copy_status: bool = True,
        actor_id: int | None = None,
    ) -> dict:
        if target_major_version_id == source_major_version_id:
            raise HTTPException(status_code=400, detail="来源大版本与目标大版本不能相同")
        self.ensure_major_version_exists(target_major_version_id)
        self.ensure_major_version_exists(source_major_version_id)

        source_rows = (
            self.db.query(Requirement)
            .options(joinedload(Requirement.test_cases))
            .filter(Requirement.major_version_id == source_major_version_id, Requirement.id.in_(source_requirement_ids))
            .all()
        )
        if not source_rows:
            return {"message": "没有可关联的需求", "created_count": 0, "skipped_count": 0}

        existing_ids = {
            r[0]
            for r in self.db.query(Requirement.zentao_req_id).filter(Requirement.major_version_id == target_major_version_id).all()
        }
        valid_user_ids = {u[0] for u in self.db.query(User.id).all()}

        created_count = 0
        skipped_count = 0
        conflict_count = 0
        for src in source_rows:
            if src.zentao_req_id in existing_ids:
                skipped_count += 1
                continue

            try:
                with self.db.begin_nested():
                    owner_id = src.owner_id if (src.owner_id in valid_user_ids) else None
                    new_req = Requirement(
                        zentao_req_id=src.zentao_req_id,
                        title=src.title,
                        major_version_id=target_major_version_id,
                        owner_id=owner_id,
                        case_completed=src.case_completed if copy_status else False,
                        test_completed=src.test_completed if copy_status else False,
                        retest_completed=False,
                        retested_by_id=None,
                        retested_at=None,
                        retest_minor_version_id=None,
                        retest_passed=None,
                        status=RequirementStatus.PENDING,
                    )
                    self.db.add(new_req)
                    self.db.flush()
                    # 必须在 flush 后再重算状态；否则状态历史 requirement_id 可能为 None 导致写入失败
                    self.recalculate_requirement_status(new_req, actor_id=actor_id)

                    for c in (src.test_cases or []):
                        self.db.add(
                            TestCase(
                                requirement_id=new_req.id,
                                zentao_case_id=c.zentao_case_id,
                                # 跳转/预览/展示所需的禅道字段一并复制，否则合并出的
                                # 用例只有编号、没有 url，前端会退化成不可点击的纯文本。
                                # 注意：zentao_client_record_id 有唯一约束，不能复制。
                                zentao_case_url=c.zentao_case_url,
                                zentao_case_numeric_id=c.zentao_case_numeric_id,
                                zentao_case_title=c.zentao_case_title,
                                zentao_source=c.zentao_source,
                                zentao_top_href=c.zentao_top_href,
                                zentao_product_id=c.zentao_product_id,
                                zentao_product_name=c.zentao_product_name,
                                zentao_requirement_id=c.zentao_requirement_id,
                                zentao_requirement_name=c.zentao_requirement_name,
                                zentao_creator_name=c.zentao_creator_name,
                                creator_id=c.creator_id,
                            )
                        )
                    audit(
                        self.db,
                        action="requirement.link_major",
                        target_type="requirement",
                        actor_id=actor_id,
                        target_id=str(new_req.id),
                        detail=f"from_major={source_major_version_id},from_req={src.id},copy_status={copy_status}",
                    )
                created_count += 1
                existing_ids.add(src.zentao_req_id)
            except IntegrityError as e:
                err_text = ""
                try:
                    err_text = str(e).lower()
                except Exception:
                    err_text = ""
                # 若仍是旧库全局唯一约束，会导致跨版本同需求号全部冲突
                # 给出明确错误，避免用户看到“都被冲突拦截”但不知道原因。
                if "requirements.zentao_req_id" in err_text:
                    raise HTTPException(
                        status_code=400,
                        detail="检测到数据库仍使用旧唯一约束（requirements.zentao_req_id 全局唯一），请先重启服务触发自动迁移后再执行关联。",
                    )
                # 容错：单条冲突跳过，避免整批失败
                conflict_count += 1
                skipped_count += 1
                continue
            except Exception as e:
                # 单条兜底容错，避免整批失败；同时将这类错误计入冲突数供前端提示
                conflict_count += 1
                skipped_count += 1
                continue

        self.db.commit()
        msg = f"关联完成：新增 {created_count} 条，跳过 {skipped_count} 条"
        if conflict_count > 0:
            msg += f"（其中 {conflict_count} 条因数据约束冲突被跳过）"
        return {
            "message": msg,
            "created_count": created_count,
            "skipped_count": skipped_count,
            "conflict_count": conflict_count,
        }

    def _mark_test_completed_transition(self, requirement: Requirement, test_completed: bool, acting_user: User | None = None) -> None:
        """
        Set test_completed plus its timestamp anchor.

        The cutoff is what build_retest_evidence uses to tell "Bug filed
        before this requirement was finished" apart from "Bug filed during
        retest" — without it the auto-collected evidence would silently fall
        back to the legacy "no candidates" behavior.
        """
        was_completed = bool(requirement.test_completed)
        requirement.test_completed = bool(test_completed)
        if test_completed and not was_completed:
            requirement.test_completed_at = local_now()
            self._sync_zentao_task_on_test_completed(requirement, finished=True, acting_user=acting_user)
        elif not test_completed and was_completed:
            requirement.test_completed_at = None
            self._sync_zentao_task_on_test_completed(requirement, finished=False, acting_user=acting_user)

    def _sync_zentao_task_on_test_completed(self, requirement: Requirement, *, finished: bool, acting_user: User | None = None) -> None:
        """测试完成勾选/取消 → 禅道子任务 完成 / 重新激活。

        仅在该需求绑定了禅道子任务、且操作者是子任务指派人时才联动禅道；否则只保留
        本地记录（对应「非指派人勾选完成只做本地记录」）。任何异常都吞掉，不影响本地
        状态流转（禅道为辅，本地为主）。
        """
        if not self._may_drive_zentao_task(requirement, acting_user):
            return
        try:
            from app.services.zentao_task_sync_service import ZentaoTaskSyncService
            svc = ZentaoTaskSyncService(self.db)
            if finished:
                svc.finish_requirement_task(requirement, acting_user=acting_user)
            else:
                svc.reactivate_requirement_task(requirement, acting_user=acting_user)
        except Exception as exc:  # noqa: BLE001 — 禅道侧失败不阻断本地
            import logging
            logging.getLogger(__name__).warning(
                "zentao task sync on test_completed=%s req=%s failed: %s",
                finished, requirement.id, exc,
            )

    def recalculate_requirement_status(self, requirement: Requirement, actor_id: int | None = None) -> Requirement:
        old_status_obj = requirement.status
        current_status = old_status_obj.value if hasattr(old_status_obj, "value") else str(old_status_obj)
        if requirement.retest_completed:
            next_status = RequirementStatus.RETEST_DONE
        elif requirement.test_completed:
            next_status = RequirementStatus.TEST_DONE
        elif requirement.case_completed:
            next_status = RequirementStatus.CASE_DONE
        elif requirement.owner_id:
            next_status = RequirementStatus.ASSIGNED
        else:
            next_status = RequirementStatus.PENDING

        next_status_value = next_status.value if hasattr(next_status, "value") else str(next_status)
        try:
            ensure_requirement_transition(current_status, next_status_value)
        except Exception:
            # 保守迁移：历史数据存在旧状态时，不阻断线上流程，先统一回写到目标状态。
            pass
        requirement.status = next_status
        if old_status_obj != next_status:
            self._record_status_history(requirement, old_status_obj, next_status, actor_id=actor_id)
        return requirement

    def list_requirements(self, major_version_id: int) -> list[dict]:
        rows = (
            self.db.query(Requirement)
            .options(
                joinedload(Requirement.owner),
                joinedload(Requirement.retester),
                joinedload(Requirement.test_cases),
                joinedload(Requirement.test_notes_updated_by),
            )
            .filter(Requirement.major_version_id == major_version_id)
            .order_by(Requirement.id.asc())
            .all()
        )
        return [
            {
                "id": r.id,
                "zentao_req_id": r.zentao_req_id,
                "title": r.title,
                "owner": r.owner.shown_name if r.owner else None,
                "owner_id": r.owner_id,
                "case_completed": r.case_completed,
                "test_completed": r.test_completed,
                "retest_completed": r.retest_completed,
                "retested_by": r.retester.shown_name if r.retester else None,
                "status": r.status,
                "case_ids": [c.zentao_case_id for c in r.test_cases],
                "test_notes": r.test_notes,
                "test_notes_html": r.test_notes_html,
                "test_notes_updated_at": r.test_notes_updated_at.isoformat() if r.test_notes_updated_at else None,
                "test_notes_updated_by_id": r.test_notes_updated_by_id,
                "test_notes_updated_by_name": r.test_notes_updated_by.shown_name if r.test_notes_updated_by else None,
            }
            for r in rows
        ]

    def _get_owned_requirement(self, requirement_id: int, current_user: User) -> Requirement:
        req = self.db.query(Requirement).filter(Requirement.id == requirement_id).first()
        if not req:
            raise HTTPException(status_code=404, detail="需求不存在")
        if current_user.role != UserRole.ADMIN and req.owner_id != current_user.id:
            raise HTTPException(status_code=403, detail="只有负责人可以操作该需求的任务")
        return req

    @staticmethod
    def _is_task_assignee(requirement: Requirement, user: User) -> bool:
        """当前用户的禅道账号是否等于该子任务的指派人账号。"""
        assignee = (requirement.zentao_task_assigned_to or "").strip().lower()
        acc = (getattr(user, "zentao_account", None) or "").strip().lower()
        return bool(assignee) and bool(acc) and assignee == acc

    def _may_drive_zentao_task(self, requirement: Requirement, acting_user: User | None) -> bool:
        """是否允许由本次操作联动禅道子任务（开始/完成/重新激活）。

        规则：需求已绑定禅道子任务，且（未记录指派人[历史兼容] 或 操作者就是指派人）。
        acting_user 为 None 时视为不做指派人限制（内部/自动化调用，保持向后兼容）。
        """
        if not requirement.zentao_task_id:
            return False
        assignee = (requirement.zentao_task_assigned_to or "").strip()
        if not assignee:
            return True  # 历史数据未同步指派人：保持旧行为
        if acting_user is None:
            return True
        return self._is_task_assignee(requirement, acting_user)

    def _get_task_actionable_requirement(self, requirement_id: int, current_user: User) -> Requirement:
        """开始 / 设置预计用时的权限：管理员，或（已建任务时）子任务指派人本人，
        或（未建任务时/未记录指派人时）需求负责人。非指派人操作已建任务一律拒绝，
        对应「任务指派人和当前账号相同时才允许点击开始、设置时间」。"""
        req = self.db.query(Requirement).filter(Requirement.id == requirement_id).first()
        if not req:
            raise HTTPException(status_code=404, detail="需求不存在")
        if current_user.role == UserRole.ADMIN:
            return req
        if req.zentao_task_id and (req.zentao_task_assigned_to or "").strip():
            if self._is_task_assignee(req, current_user):
                return req
            raise HTTPException(status_code=403, detail="该禅道任务未指派给你，无法操作")
        if req.owner_id == current_user.id:
            return req
        raise HTTPException(status_code=403, detail="只有负责人或任务指派人可以操作该任务")

    def update_estimated_test_hours(self, requirement_id: int, hours: float, current_user: User) -> dict:
        """更新需求的预计测试用时（小时）。仅任务指派人本人（或负责人/管理员）可改。"""
        req = self._get_task_actionable_requirement(requirement_id, current_user)
        try:
            h = float(hours)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="预计用时必须是数字")
        if h <= 0 or h > 999:
            raise HTTPException(status_code=400, detail="预计用时需在 0~999 小时之间")
        req.estimated_test_hours = round(h, 2)
        self.db.commit()
        return {"message": "预计测试用时已更新", "estimated_test_hours": req.estimated_test_hours}

    def start_requirement_task(self, requirement_id: int, current_user: User, hours: float | None = None) -> dict:
        """点击「开始」：记录开始时刻并让禅道子任务开始。仅任务指派人本人可开始。"""
        req = self._get_task_actionable_requirement(requirement_id, current_user)
        from app.services.zentao_task_sync_service import ZentaoTaskSyncService
        result = ZentaoTaskSyncService(self.db).start_requirement_task(req, hours=hours, acting_user=current_user)
        self.db.refresh(req)
        return {
            "message": "任务已开始" if result.get("ok") else "任务已开始（禅道侧部分失败）",
            "task_started_at": req.task_started_at.isoformat() if req.task_started_at else None,
            "zentao_task_id": req.zentao_task_id,
            "zentao_task_status": req.zentao_task_status_cache,
            "errors": result.get("errors", []),
        }

    def pause_requirement_task(self, requirement_id: int, current_user: User) -> dict:
        """点击「暂停」：暂停禅道子任务。仅任务指派人本人可操作。之后可再「开始」继续。"""
        req = self._get_task_actionable_requirement(requirement_id, current_user)
        from app.services.zentao_task_sync_service import ZentaoTaskSyncService
        result = ZentaoTaskSyncService(self.db).pause_requirement_task(req, acting_user=current_user)
        self.db.refresh(req)
        return {
            "message": "任务已暂停" if result.get("ok") else "任务已暂停（禅道侧部分失败）",
            "zentao_task_id": req.zentao_task_id,
            "zentao_task_status": req.zentao_task_status_cache,
            "errors": result.get("errors", []),
        }

    def update_test_notes(
        self,
        requirement_id: int,
        test_notes: str | None,
        current_user: User,
        test_notes_html: str | None = None,
    ) -> dict:
        req = (
            self.db.query(Requirement)
            .options(joinedload(Requirement.test_notes_updated_by))
            .filter(Requirement.id == requirement_id)
            .first()
        )
        if not req:
            raise HTTPException(status_code=404, detail="需求不存在")

        old_len = len((req.test_notes or "").strip())
        req.test_notes = (test_notes or "").strip() or None
        # 网页端富文本传 HTML；移动端等只传纯文本的调用方不带此参数，
        # 此时 HTML 版以纯文本为准清空，避免两个版本内容漂移。
        req.test_notes_html = (test_notes_html or "").strip() or None
        req.test_notes_updated_at = local_now()
        req.test_notes_updated_by_id = current_user.id
        self.db.commit()
        self.db.refresh(req)

        audit(
            self.db,
            action="requirement.test_notes.update",
            target_type="requirement",
            actor_id=current_user.id,
            target_id=str(req.id),
            detail=f"before_len={old_len},after_len={len((req.test_notes or '').strip())}",
        )

        # 测试要点是需求、审查、复测工作台共用的协作内容。广播变更后，
        # 其他已打开工作台的用户可以立即刷新到同一份数据。
        sse_publish(
            "requirement_test_notes_updated",
            {
                "requirement_id": req.id,
                "major_version_id": req.major_version_id,
                "updated_by_id": current_user.id,
            },
            channels=["global"],
        )

        return {
            "message": "测试要点保存成功",
            "requirement_id": req.id,
            "test_notes": req.test_notes,
            "test_notes_html": req.test_notes_html,
            "test_notes_updated_at": req.test_notes_updated_at.isoformat() if req.test_notes_updated_at else None,
            "test_notes_updated_by_id": req.test_notes_updated_by_id,
            "test_notes_updated_by_name": current_user.shown_name,
        }

    def update_requirement(self, requirement_id: int, zentao_req_id: str, title: str, major_version_id: int, actor_id: int | None = None) -> dict:
        req = self.db.query(Requirement).filter(Requirement.id == requirement_id).first()
        if not req:
            raise HTTPException(status_code=404, detail="Requirement not found")
        self.ensure_major_version_exists(major_version_id)
        if not validate_req_id(zentao_req_id):
            raise HTTPException(status_code=400, detail="zentao_req_id must be like r#xxxx")
        req.zentao_req_id = zentao_req_id
        req.title = title
        req.major_version_id = major_version_id
        self.db.commit()
        audit(self.db, action="requirement.update", target_type="requirement", actor_id=actor_id, target_id=str(req.id), detail=req.zentao_req_id)
        return {"message": "Requirement updated"}

    def preview_story_binding(self, requirement_id: int, story_id: int | None = None) -> dict:
        req = (
            self.db.query(Requirement)
            .options(joinedload(Requirement.major_version))
            .filter(Requirement.id == requirement_id)
            .first()
        )
        if not req:
            raise HTTPException(status_code=404, detail="Requirement not found")

        effective_story_id = story_id if story_id is not None else req.zentao_story_id
        testcase_rows = []
        bug_rows = []
        duplicate_requirements = []
        if effective_story_id:
            testcase_rows = (
                self.db.query(ZentaoTestCaseMirror)
                .filter(
                    ZentaoTestCaseMirror.zentao_story_id == effective_story_id,
                    ZentaoTestCaseMirror.deleted.isnot(True),
                )
                .order_by(ZentaoTestCaseMirror.zentao_case_numeric_id.asc())
                .limit(10)
                .all()
            )
            bug_rows = (
                self.db.query(BugTracking)
                .filter(
                    BugTracking.zentao_story_id == effective_story_id,
                    BugTracking.major_version_id == req.major_version_id,
                    BugTracking.zentao_deleted.isnot(True),
                    BugTracking.source_type.in_([BugSourceType.MANUAL, BugSourceType.REQUIREMENT]),
                )
                .order_by(BugTracking.id.desc())
                .limit(10)
                .all()
            )
            duplicate_requirements = (
                self.db.query(Requirement)
                .options(joinedload(Requirement.major_version))
                .filter(
                    Requirement.zentao_story_id == effective_story_id,
                    Requirement.id != req.id,
                )
                .order_by(Requirement.id.asc())
                .limit(10)
                .all()
            )

        testcase_total = (
            self.db.query(func.count(ZentaoTestCaseMirror.id))
            .filter(
                ZentaoTestCaseMirror.zentao_story_id == effective_story_id,
                ZentaoTestCaseMirror.deleted.isnot(True),
            )
            .scalar()
            if effective_story_id
            else 0
        ) or 0
        bug_total = (
            self.db.query(func.count(BugTracking.id))
            .filter(
                BugTracking.zentao_story_id == effective_story_id,
                BugTracking.major_version_id == req.major_version_id,
                BugTracking.zentao_deleted.isnot(True),
                BugTracking.source_type.in_([BugSourceType.MANUAL, BugSourceType.REQUIREMENT]),
            )
            .scalar()
            if effective_story_id
            else 0
        ) or 0

        return {
            "requirement_id": req.id,
            "zentao_req_id": req.zentao_req_id,
            "title": req.title,
            "major_version_id": req.major_version_id,
            "major_version_name": req.major_version.version_no if req.major_version else "",
            "current_story_id": req.zentao_story_id,
            "preview_story_id": effective_story_id,
            "testcase_total": int(testcase_total),
            "bug_total": int(bug_total),
            "sample_testcases": [
                {
                    "zentao_case_id": row.zentao_case_id,
                    "title": row.title,
                }
                for row in testcase_rows
            ],
            "sample_bugs": [
                {
                    "bug_id": row.bug_id,
                    "title": row.zentao_bug_title,
                }
                for row in bug_rows
            ],
            "duplicate_requirements": [
                {
                    "id": row.id,
                    "zentao_req_id": row.zentao_req_id,
                    "title": row.title,
                    "major_version_name": row.major_version.version_no if row.major_version else "",
                }
                for row in duplicate_requirements
            ],
        }

    def update_story_binding(self, requirement_id: int, story_id: int | None, actor_id: int | None = None) -> dict:
        req = self.db.query(Requirement).filter(Requirement.id == requirement_id).first()
        if not req:
            raise HTTPException(status_code=404, detail="Requirement not found")

        old_story_id = req.zentao_story_id
        req.zentao_story_id = story_id
        self.db.commit()
        audit(
            self.db,
            action="requirement.update_story_binding",
            target_type="requirement",
            actor_id=actor_id,
            target_id=str(req.id),
            detail=f"{old_story_id or ''}->{story_id or ''}",
        )
        return {
            "message": "Requirement story binding updated",
            "requirement_id": req.id,
            "old_story_id": old_story_id,
            "zentao_story_id": req.zentao_story_id,
        }

    def delete_requirement(self, requirement_id: int, actor_id: int | None = None) -> dict:
        req = self.db.query(Requirement).filter(Requirement.id == requirement_id).first()
        if not req:
            raise HTTPException(status_code=404, detail="Requirement not found")
        req_id = req.id
        req_no = req.zentao_req_id
        self.db.delete(req)
        self.db.commit()
        audit(self.db, action="requirement.delete", target_type="requirement", actor_id=actor_id, target_id=str(req_id), detail=req_no)
        return {"message": "Requirement deleted"}

    def assign_and_publish(self, major_version_id: int, assignments: list[dict], users_map: dict[int, str], actor_id: int | None = None) -> dict:
        req_map = {r.id: r for r in self.db.query(Requirement).filter(Requirement.major_version_id == major_version_id).all()}
        change_msgs: list[str] = []

        for item in assignments:
            req = req_map.get(item["requirement_id"])
            if not req:
                continue

            old_owner_id = req.owner_id
            new_owner_id = item.get("owner_id")
            if old_owner_id == new_owner_id:
                continue

            req.owner_id = new_owner_id
            req.case_completed = False
            req.test_completed = False
            req.retest_completed = False
            self.recalculate_requirement_status(req, actor_id=actor_id)

            if new_owner_id:
                old_name = users_map.get(old_owner_id, "未分配")
                new_name = users_map.get(new_owner_id, "未知")
                if old_owner_id:
                    change_msgs.append(f"> **{req.zentao_req_id}** ({req.title}) 已从 @{old_name} 移交给了 @{new_name}")

            audit(
                self.db,
                action="requirement.assign",
                target_type="requirement",
                actor_id=actor_id,
                target_id=str(req.id),
                detail=f"{old_owner_id}->{new_owner_id}",
            )

        self.db.commit()
        return {"message": "Assignments updated", "change_msgs": change_msgs}

    def patch_requirement_status(
        self,
        requirement_id: int,
        current_user: User,
        case_completed: bool | None = None,
        test_completed: bool | None = None,
    ) -> dict:
        requirement = self.db.query(Requirement).filter(Requirement.id == requirement_id).first()
        if not requirement:
            raise HTTPException(status_code=404, detail="Requirement not found")
        if requirement.owner_id and requirement.owner_id != current_user.id and current_user.role.value != "admin":
            raise HTTPException(status_code=403, detail="Only owner can update status")

        if case_completed is not None:
            requirement.case_completed = case_completed
        if test_completed is not None:
            self._mark_test_completed_transition(requirement, test_completed, acting_user=current_user)

        self.recalculate_requirement_status(requirement, actor_id=current_user.id)
        self.db.commit()
        audit(
            self.db,
            action="requirement.patch_status",
            target_type="requirement",
            actor_id=current_user.id,
            target_id=str(requirement.id),
            detail=f"case={requirement.case_completed},test={requirement.test_completed},status={requirement.status.value}",
        )
        return {
            "message": "Requirement status updated",
            "case_completed": requirement.case_completed,
            "test_completed": requirement.test_completed,
            "status": requirement.status,
        }

    def update_requirement_cases(self, requirement_id: int, case_ids: list[str], case_completed: bool, actor_id: int | None = None) -> dict:
        requirement = self.db.query(Requirement).filter(Requirement.id == requirement_id).first()
        if not requirement:
            raise HTTPException(status_code=404, detail="Requirement not found")
        self.db.query(TestCase).filter(TestCase.requirement_id == requirement_id).delete()
        for cid in case_ids:
            self.db.add(TestCase(requirement_id=requirement.id, zentao_case_id=cid, creator_id=actor_id))
        requirement.case_completed = case_completed
        self.recalculate_requirement_status(requirement, actor_id=actor_id)
        self.db.commit()
        audit(
            self.db,
            action="requirement.update_cases",
            target_type="requirement",
            actor_id=actor_id,
            target_id=str(requirement.id),
            detail=f"case_count={len(case_ids)}",
        )
        return {"message": "Cases updated", "case_ids": case_ids}

    def update_test_execution_completion(self, requirement_id: int, test_completed: bool, actor_id: int | None = None) -> Requirement:
        requirement = self.db.query(Requirement).filter(Requirement.id == requirement_id).first()
        if not requirement:
            raise HTTPException(status_code=404, detail="Requirement not found")
        acting_user = self.db.query(User).filter(User.id == actor_id).first() if actor_id else None
        self._mark_test_completed_transition(requirement, test_completed, acting_user=acting_user)
        self.recalculate_requirement_status(requirement, actor_id=actor_id)
        self.db.commit()
        audit(
            self.db,
            action="requirement.update_test_completion",
            target_type="requirement",
            actor_id=actor_id,
            target_id=str(requirement.id),
            detail=f"test_completed={test_completed}",
        )
        sse_publish(
            "retest_requirement_status_changed",
            {
                "requirement_id": requirement.id,
                "test_completed": requirement.test_completed,
                "status": requirement.status.value if hasattr(requirement.status, "value") else str(requirement.status),
                "owner_id": requirement.owner_id,
            },
            channels=["global", f"user:{requirement.owner_id}"] if requirement.owner_id else ["global"],
        )
        if test_completed:
            sse_publish(
                "retest_requirement_created",
                {
                    "id": requirement.id,
                    "title": requirement.title,
                    "owner_id": requirement.owner_id,
                    "major_version_id": requirement.major_version_id,
                },
                channels=["global"],
            )
        return requirement

    def add_case_to_requirement(self, req_id: int, zentao_case_id: str, actor_id: int | None = None) -> dict:
        req = self.db.query(Requirement).filter(Requirement.id == req_id).first()
        if not req:
            raise HTTPException(status_code=404, detail="Requirement not found")
        if req.case_completed:
            raise HTTPException(status_code=400, detail="用例已封板（已勾选完成），无法新增！请先取消勾选。")

        case = TestCase(requirement_id=req_id, zentao_case_id=zentao_case_id, creator_id=actor_id)
        self.db.add(case)
        self.db.commit()
        self.db.refresh(case)
        audit(self.db, action="requirement.add_case", target_type="requirement", actor_id=actor_id, target_id=str(req_id), detail=zentao_case_id)
        sse_publish(
            "workbench_testcase_created",
            {
                "requirement_id": req_id,
                "test_case_id": case.id,
                "zentao_case_id": case.zentao_case_id,
                "owner_id": req.owner_id,
            },
            channels=["global", f"user:{req.owner_id}"] if req.owner_id else ["global"],
        )
        return {"id": case.id, "zentao_case_id": case.zentao_case_id}

    def update_case_identifier(self, case_id: int, zentao_case_id: str, actor_id: int | None = None) -> dict:
        case = self.db.query(TestCase).filter(TestCase.id == case_id).first()
        if not case:
            raise HTTPException(status_code=404, detail="Case not found")
        req = self.db.query(Requirement).filter(Requirement.id == case.requirement_id).first()
        if req and req.case_completed:
            raise HTTPException(status_code=400, detail="用例已封板（已勾选完成），无法修改！请先取消勾选。")

        old_id = case.zentao_case_id
        case.zentao_case_id = zentao_case_id
        self.db.commit()
        audit(self.db, action="requirement.update_case", target_type="test_case", actor_id=actor_id, target_id=str(case.id), detail=f"{old_id}->{zentao_case_id}")
        return {"message": "Case updated"}

    def delete_case(self, case_id: int, actor_id: int | None = None) -> dict:
        case = self.db.query(TestCase).filter(TestCase.id == case_id).first()
        if not case:
            raise HTTPException(status_code=404, detail="Case not found")
        req = self.db.query(Requirement).filter(Requirement.id == case.requirement_id).first()
        if req and req.case_completed:
            raise HTTPException(status_code=400, detail="用例已封板（已勾选完成），无法删除！请先取消勾选。")

        case_no = case.zentao_case_id
        self.db.delete(case)
        self.db.commit()
        audit(self.db, action="requirement.delete_case", target_type="test_case", actor_id=actor_id, target_id=str(case_id), detail=case_no)
        return {"message": "Case deleted"}

    def upsert_test_execution(
        self,
        requirement_id: int,
        minor_version_id: int,
        bug_id: str | None,
        source_case_id: str | None,
        result_status: str,
        notes: str | None,
        test_completed: bool,
        actor_id: int | None = None,
    ) -> dict:
        allowed_result_status = {e.value for e in TestResultStatus}
        requirement = self.db.query(Requirement).filter(Requirement.id == requirement_id).first()
        if not requirement:
            raise HTTPException(status_code=404, detail="需求不存在")
        actor = self.db.query(User).filter(User.id == actor_id).first() if actor_id else None
        if requirement.owner_id and actor and requirement.owner_id != actor.id and actor.role.value != "admin":
            raise HTTPException(status_code=403, detail="仅需求负责人或管理员可提交测试执行记录")

        minor_version = self.db.query(Version).filter(Version.id == minor_version_id).first()
        if not minor_version:
            raise HTTPException(status_code=400, detail="小版本不存在")
        if minor_version.version_type != VersionType.MINOR:
            raise HTTPException(status_code=400, detail="minor_version_id 必须是小版本")
        if minor_version.parent_id != requirement.major_version_id:
            raise HTTPException(status_code=400, detail="小版本与需求所属大版本不匹配")

        if result_status not in allowed_result_status:
            raise HTTPException(status_code=400, detail="测试结果非法，仅支持：passed/failed/blocked/partial/untested")

        execution = self.db.query(TestExecution).filter(
            TestExecution.requirement_id == requirement_id,
            TestExecution.minor_version_id == minor_version_id,
        ).first()
        is_create = execution is None
        if not execution:
            execution = TestExecution(requirement_id=requirement_id, minor_version_id=minor_version_id)
            self.db.add(execution)
        execution.bug_id = bug_id
        execution.source_case_id = source_case_id
        execution.result_status = TestResultStatus(result_status)
        execution.notes = notes
        execution.executed_by_id = actor_id
        execution.executed_at = local_now()
        acting_user = self.db.query(User).filter(User.id == actor_id).first() if actor_id else None
        self._mark_test_completed_transition(requirement, test_completed, acting_user=acting_user)
        self.recalculate_requirement_status(requirement, actor_id=actor_id)
        self.db.commit()
        self.db.refresh(execution)
        audit(
            self.db,
            action="requirement.upsert_test_execution",
            target_type="requirement",
            actor_id=actor_id,
            target_id=str(requirement.id),
            detail=f"minor={minor_version_id},result={result_status},test_completed={test_completed}",
        )
        return {
            "message": "测试执行记录已保存" if is_create else "测试执行记录更新成功",
            "execution_id": execution.id,
            "requirement_id": requirement.id,
            "minor_version_id": execution.minor_version_id,
            "result_status": execution.result_status.value if hasattr(execution.result_status, "value") else str(execution.result_status),
            "test_completed": requirement.test_completed,
            "executed_at": execution.executed_at.isoformat() if execution.executed_at else None,
        }

    def build_case_progress_message(self, major_version_id: int, current_user: User) -> str:
        major = self.db.query(Version).filter(Version.id == major_version_id).first()
        major_name = major.version_no if major else f"ID:{major_version_id}"

        created_cases = (
            self.db.query(func.count(TestCase.id))
            .join(Requirement, TestCase.requirement_id == Requirement.id)
            .filter(Requirement.major_version_id == major_version_id, TestCase.creator_id == current_user.id)
            .scalar()
            or 0
        )
        req_case_pending = (
            self.db.query(func.count(Requirement.id))
            .filter(Requirement.major_version_id == major_version_id, Requirement.owner_id == current_user.id, Requirement.case_completed.is_(False))
            .scalar()
            or 0
        )
        req_case_done = (
            self.db.query(func.count(Requirement.id))
            .filter(Requirement.major_version_id == major_version_id, Requirement.owner_id == current_user.id, Requirement.case_completed.is_(True))
            .scalar()
            or 0
        )
        return "\n".join(
            [
                "### 需求测试进度",
                f"> 大版本：{major_name}",
                f"> 提交人：@{current_user.shown_name}",
                f"> 当前人员填写用例：{created_cases} 个",
                f"> 需求用例未完成：{req_case_pending} 个",
                f"> 需求用例已完成：{req_case_done} 个",
            ]
        )

    def build_test_progress_message(self, major_version_id: int, minor_version_id: int, current_user: User) -> str:
        major = self.db.query(Version).filter(Version.id == major_version_id).first()
        minor = self.db.query(Version).filter(Version.id == minor_version_id).first()
        major_name = major.version_no if major else f"ID:{major_version_id}"
        minor_name = minor.version_no if minor else f"ID:{minor_version_id}"

        req_test_done = (
            self.db.query(func.count(Requirement.id))
            .filter(Requirement.major_version_id == major_version_id, Requirement.owner_id == current_user.id, Requirement.test_completed.is_(True))
            .scalar()
            or 0
        )
        req_test_pending = (
            self.db.query(func.count(Requirement.id))
            .filter(Requirement.major_version_id == major_version_id, Requirement.owner_id == current_user.id, Requirement.test_completed.is_(False))
            .scalar()
            or 0
        )
        return "\n".join(
            [
                "### 需求测试进度",
                f"> 大版本：{major_name} | 当前发包：{minor_name}",
                f"> 提交人：@{current_user.shown_name}",
                f"> 当前人员已完成测试需求：{req_test_done} 个",
                f"> 需求测试未完成：{req_test_pending} 个",
                f"> 需求测试已完成：{req_test_done} 个",
            ]
        )
    def _record_status_history(
        self,
        requirement: Requirement,
        from_status: RequirementStatus | None,
        to_status: RequirementStatus,
        actor_id: int | None = None,
    ) -> None:
        self.db.add(
            RequirementStatusHistory(
                requirement_id=requirement.id,
                from_status=from_status,
                to_status=to_status,
                changed_by_id=actor_id,
                changed_at=local_now(),
            )
        )
