from __future__ import annotations

from datetime import date, datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import and_, case, func, or_
from sqlalchemy.orm import Session

from app.models import (
    AuditLog,
    BugSourceType,
    BugStage5Record,
    BugTracking,
    FeedbackBugLink,
    FeedbackRecord,
    FeedbackStatus,
    FinalTestRecord,
    Requirement,
    RequirementStatus,
    RequirementStatusHistory,
    TestCase,
    TestExecution,
    User,
    UserRole,
    UserZentaoBinding,
    Version,
    VersionType,
)
from app.utils.time_utils import local_now


def _bug_time_col():
    """
    Time axis used for every "created in date range" bug stat.

    Prefers the real Zentao openedDate (`zentao_opened_at`) and falls back to
    the local insert time only when Zentao data is missing. Before this
    change, bulk-syncing a year of Zentao bugs would stack them all on the
    sync day in reports; now they land in the right buckets.
    """
    return func.coalesce(BugTracking.zentao_opened_at, BugTracking.created_at)


def _bug_time_value(bug: BugTracking) -> datetime | None:
    """Python-side equivalent of `_bug_time_col()` for in-memory rows."""
    return bug.zentao_opened_at or bug.created_at


def _bug_close_time_col():
    """
    Time axis used for every "closed in date range" bug stat.

    Report-center "关闭 Bug" is defined as Zentao's real closure event, so we
    use the cached Zentao close timestamp instead of local Stage5 update time.
    """
    return BugTracking.zentao_close_date


def _not_deleted_clause():
    """
    SQLAlchemy expression excluding Zentao-tombstoned rows from any bug query.
    Deleted bugs were previously counted in summary / advanced / governance
    but hidden in the overall-test list, producing mismatched totals.
    """
    return or_(BugTracking.zentao_deleted.is_(False), BugTracking.zentao_deleted.is_(None))


class ReportService:
    def __init__(self, db: Session):
        self.db = db

    def _parse_feedback_status_from_audit_detail(self, detail: str | None) -> FeedbackStatus | None:
        if not detail:
            return None
        text = (detail or "").strip().lower()
        if text.startswith("status="):
            text = text.split("=", 1)[1].strip().lower()
        try:
            return FeedbackStatus(text)
        except Exception:
            return None

    def _feedback_processing_event_stats(
        self,
        sdt: datetime,
        edt: datetime,
        major_ids: list[int] | None = None,
        actor_ids: list[int] | None = None,
    ) -> dict:
        """
        处理反馈统计口径（统一）：
        仅当状态从“非终态”切换到“已处理/已关闭”时计 1 次。
        终态定义：resolved / closed
        """
        if major_ids is not None and len(major_ids) == 0:
            return {"total": 0, "by_actor": {}, "by_day": {}}

        feedback_ids: list[int] | None = None
        if major_ids is not None:
            feedback_ids = [x[0] for x in self.db.query(FeedbackRecord.id).filter(FeedbackRecord.major_version_id.in_(major_ids)).all()]
            if not feedback_ids:
                return {"total": 0, "by_actor": {}, "by_day": {}}

        q = (
            self.db.query(AuditLog)
            .filter(
                AuditLog.target_type == "feedback",
                AuditLog.action.in_(["feedback.status", "feedback.handle"]),
                AuditLog.created_at <= edt,
                AuditLog.target_id.isnot(None),
                AuditLog.actor_id.isnot(None),
            )
            .order_by(AuditLog.target_id.asc(), AuditLog.created_at.asc(), AuditLog.id.asc())
        )
        if actor_ids:
            q = q.filter(AuditLog.actor_id.in_(actor_ids))
        if feedback_ids is not None:
            q = q.filter(AuditLog.target_id.in_([str(fid) for fid in feedback_ids]))

        logs = q.all()
        terminal = {FeedbackStatus.RESOLVED, FeedbackStatus.CLOSED}
        prev_status_map: dict[str, FeedbackStatus] = {}
        by_actor: dict[int, int] = {}
        by_day: dict[str, int] = {}
        total = 0

        for log in logs:
            fid = (log.target_id or "").strip()
            if not fid:
                continue
            new_status = self._parse_feedback_status_from_audit_detail(log.detail)
            if not new_status:
                continue
            prev_status = prev_status_map.get(fid, FeedbackStatus.PENDING)
            if log.created_at >= sdt and new_status in terminal and prev_status not in terminal:
                total += 1
                if log.actor_id:
                    by_actor[log.actor_id] = by_actor.get(log.actor_id, 0) + 1
                day_key = log.created_at.date().isoformat()
                by_day[day_key] = by_day.get(day_key, 0) + 1
            prev_status_map[fid] = new_status

        return {"total": total, "by_actor": by_actor, "by_day": by_day}

    def _parse_retest_passed_from_detail(self, detail: str | None) -> bool | None:
        if not detail:
            return None
        text = (detail or "").strip().lower()
        # 兼容审计 detail 形如: passed=True,minor=12
        for part in text.split(","):
            p = part.strip()
            if p.startswith("passed="):
                val = p.split("=", 1)[1].strip()
                if val in {"true", "1", "yes", "y"}:
                    return True
                if val in {"false", "0", "no", "n"}:
                    return False
        return None

    def _retest_transition_stats(
        self,
        sdt: datetime,
        edt: datetime,
        major_ids: list[int] | None = None,
        actor_ids: list[int] | None = None,
    ) -> dict:
        """
        复测计次口径（统一）：
        - 基于审计事件 retest.submit
        - 同一需求仅在“复测结论(passed)发生变化”时计次
        - 首次有效提交计 1 次
        """
        if major_ids is not None and len(major_ids) == 0:
            return {"total": 0, "by_actor": {}, "by_day": {}}

        req_ids: list[int] | None = None
        if major_ids is not None:
            req_ids = [x[0] for x in self.db.query(Requirement.id).filter(Requirement.major_version_id.in_(major_ids)).all()]
            if not req_ids:
                return {"total": 0, "by_actor": {}, "by_day": {}}

        q = (
            self.db.query(AuditLog)
            .filter(
                AuditLog.target_type == "requirement",
                AuditLog.action == "retest.submit",
                AuditLog.created_at <= edt,
                AuditLog.target_id.isnot(None),
                AuditLog.actor_id.isnot(None),
            )
            .order_by(AuditLog.target_id.asc(), AuditLog.created_at.asc(), AuditLog.id.asc())
        )
        if actor_ids:
            q = q.filter(AuditLog.actor_id.in_(actor_ids))
        if req_ids is not None:
            q = q.filter(AuditLog.target_id.in_([str(rid) for rid in req_ids]))

        logs = q.all()
        prev_passed: dict[str, bool] = {}
        by_actor: dict[int, int] = {}
        by_day: dict[str, int] = {}
        total = 0

        for log in logs:
            rid = (log.target_id or "").strip()
            if not rid:
                continue
            passed = self._parse_retest_passed_from_detail(log.detail)
            if passed is None:
                continue
            prev = prev_passed.get(rid)
            changed = prev is None or prev != passed
            if changed and log.created_at >= sdt:
                total += 1
                if log.actor_id:
                    by_actor[log.actor_id] = by_actor.get(log.actor_id, 0) + 1
                day_key = log.created_at.date().isoformat()
                by_day[day_key] = by_day.get(day_key, 0) + 1
            prev_passed[rid] = passed

        return {"total": total, "by_actor": by_actor, "by_day": by_day}

    def _linked_requirement_ids(self, major_ids: list[int] | None = None) -> set[int]:
        """
        取“关联版本复制”产生的目标需求ID（audit: requirement.link_major）。
        这些需求下的用例不参与“创建用例”统计。
        """
        q = (
            self.db.query(AuditLog.target_id)
            .filter(
                AuditLog.action == "requirement.link_major",
                AuditLog.target_type == "requirement",
                AuditLog.target_id.isnot(None),
            )
        )
        ids: set[int] = set()
        for (tid,) in q.all():
            try:
                ids.add(int(str(tid)))
            except Exception:
                continue
        if not ids:
            return set()
        if major_ids is None:
            return ids
        scoped = {
            r[0]
            for r in self.db.query(Requirement.id)
            .filter(Requirement.id.in_(list(ids)), Requirement.major_version_id.in_(major_ids))
            .all()
        }
        return scoped

    def _execution_req_ids(
        self,
        sdt: datetime,
        edt: datetime,
        actor_ids: list[int] | None,
        scoped_major_ids: list[int] | None,
    ) -> set[int]:
        """Distinct requirement ids with a TestExecution by the actors in range."""
        if actor_ids is not None and not actor_ids:
            return set()
        if scoped_major_ids is not None and not scoped_major_ids:
            return set()
        q = (
            self.db.query(TestExecution.requirement_id)
            .join(Requirement, TestExecution.requirement_id == Requirement.id)
            .filter(TestExecution.executed_at >= sdt, TestExecution.executed_at <= edt)
        )
        if actor_ids is not None:
            q = q.filter(TestExecution.executed_by_id.in_(actor_ids))
        if scoped_major_ids is not None:
            q = q.filter(Requirement.major_version_id.in_(scoped_major_ids))
        return {row[0] for row in q.distinct().all()}

    def _final_test_req_ids(
        self,
        sdt: datetime,
        edt: datetime,
        actor_ids: list[int] | None,
        scoped_major_ids: list[int] | None,
    ) -> set[int]:
        """
        Distinct requirement ids the actors ticked「测试完成」in the final-test
        phase within the range. Counted regardless of whether final test is
        currently enabled, so toggling the phase off does not retroactively
        change historical report numbers (the records are preserved).
        """
        if actor_ids is not None and not actor_ids:
            return set()
        if scoped_major_ids is not None and not scoped_major_ids:
            return set()
        q = self.db.query(FinalTestRecord.requirement_id).filter(
            FinalTestRecord.test_completed.is_(True),
            FinalTestRecord.test_completed_at.isnot(None),
            FinalTestRecord.test_completed_at >= sdt,
            FinalTestRecord.test_completed_at <= edt,
        )
        if actor_ids is not None:
            q = q.filter(FinalTestRecord.user_id.in_(actor_ids))
        if scoped_major_ids is not None:
            q = q.join(Requirement, FinalTestRecord.requirement_id == Requirement.id).filter(
                Requirement.major_version_id.in_(scoped_major_ids)
            )
        return {row[0] for row in q.distinct().all()}

    def _executed_requirements_count(
        self,
        sdt: datetime,
        edt: datetime,
        actor_ids: list[int] | None,
        scoped_major_ids: list[int] | None,
    ) -> int:
        """执行需求数 = 普通执行 ∪ 最终测试勾选完成（按需求去重）。"""
        normal_ids = self._execution_req_ids(sdt, edt, actor_ids, scoped_major_ids)
        final_ids = self._final_test_req_ids(sdt, edt, actor_ids, scoped_major_ids)
        return len(normal_ids | final_ids)

    def summary(
        self,
        start_date: date,
        end_date: date,
        current_user: User,
        user_id: int | None = None,
        major_version_id: int | None = None,
        software_id: int | None = None,
    ) -> dict:
        all_users_mode = user_id in (None, 0)
        if all_users_mode and current_user.role != UserRole.ADMIN:
            target_user_id = current_user.id
            all_users_mode = False
        else:
            target_user_id = current_user.id if user_id is None else user_id

        if (not all_users_mode) and target_user_id != current_user.id and current_user.role != UserRole.ADMIN:
            raise HTTPException(status_code=403, detail="无权限查看其他人的报表")

        sdt = datetime.combine(start_date, datetime.min.time())
        edt = datetime.combine(end_date, datetime.max.time())
        team_ids: list[int] = []
        if all_users_mode:
            team_ids = [u.id for u in self.db.query(User).filter(User.is_team_member.is_(True)).all()]
        scoped_major_ids: list[int] | None = None
        if major_version_id:
            scoped_major_ids = [major_version_id]
        elif software_id:
            scoped_major_ids = [
                v.id for v in self.db.query(Version.id).filter(Version.version_type == VersionType.MAJOR, Version.software_id == software_id).all()
            ]
            scoped_major_ids = [x[0] if isinstance(x, tuple) else x for x in scoped_major_ids]

        actor_scope_ids = team_ids if all_users_mode else [target_user_id]
        processed_feedback_stats = self._feedback_processing_event_stats(
            sdt=sdt,
            edt=edt,
            major_ids=scoped_major_ids,
            actor_ids=actor_scope_ids,
        )
        retest_transition_stats = self._retest_transition_stats(
            sdt=sdt,
            edt=edt,
            major_ids=scoped_major_ids,
            actor_ids=actor_scope_ids,
        )
        linked_req_ids = self._linked_requirement_ids(scoped_major_ids)

        def filter_by_major_ids(query, column):
            if scoped_major_ids is None:
                return query
            if len(scoped_major_ids) == 0:
                return query.filter(False)
            return query.filter(column.in_(scoped_major_ids))

        actor_scope_seed = team_ids if all_users_mode else [target_user_id]
        actor_scope_seed = [uid for uid in actor_scope_seed if uid]
        zentao_accounts_by_user: dict[int, set[str]] = {}
        zentao_names_by_user: dict[int, set[str]] = {}
        if actor_scope_seed:
            user_rows = (
                self.db.query(User.id, User.username, User.display_name)
                .filter(User.id.in_(actor_scope_seed))
                .all()
            )
            for uid, username, display_name in user_rows:
                names: set[str] = set()
                for raw_name in (username, display_name):
                    text = str(raw_name or "").strip().lower()
                    if text:
                        names.add(text)
                zentao_names_by_user[int(uid)] = names

            binding_rows = (
                self.db.query(UserZentaoBinding.user_id, UserZentaoBinding.zentao_account)
                .filter(UserZentaoBinding.user_id.in_(actor_scope_seed))
                .all()
            )
            for uid, account in binding_rows:
                account_text = str(account or "").strip().lower()
                if not account_text:
                    continue
                zentao_accounts_by_user.setdefault(int(uid), set()).add(account_text)

        bulk_sync_message = "通过整体测试版本全量同步"
        opened_by_account_present = func.trim(func.coalesce(BugTracking.zentao_opened_by_account, "")) != ""
        opened_by_name_present = func.trim(func.coalesce(BugTracking.zentao_opened_by_name, "")) != ""
        reliable_zentao_creator_absent = and_(
            func.trim(func.coalesce(BugTracking.zentao_opened_by_account, "")) == "",
            func.trim(func.coalesce(BugTracking.zentao_opened_by_name, "")) == "",
        )

        def apply_created_bug_actor_filter(query, actor_ids: list[int] | None):
            """
            Attribute bug creators using Zentao truth first, local created_by second.

            Rules:
            - If a bug has Zentao opener info (`zentao_opened_by_*`), use that.
            - If a bug has no Zentao opener info and was not bulk-imported by the
              overall-test full-sync path, fall back to local `created_by_id`.
            - Bulk-imported Zentao bugs with missing opener info are left
              unattributed rather than credited to the sync operator.
            """
            if actor_ids is None:
                return query

            actor_ids = [int(uid) for uid in actor_ids if uid]
            if not actor_ids:
                return query.filter(False)

            account_pool = sorted({
                account
                for uid in actor_ids
                for account in zentao_accounts_by_user.get(int(uid), set())
            })
            name_pool = sorted({
                name
                for uid in actor_ids
                for name in zentao_names_by_user.get(int(uid), set())
            })

            actor_clauses = []
            if account_pool or name_pool:
                remote_clauses = []
                if account_pool:
                    remote_clauses.append(
                        func.lower(func.coalesce(BugTracking.zentao_opened_by_account, "")).in_(account_pool)
                    )
                if name_pool:
                    remote_clauses.append(
                        func.lower(func.coalesce(BugTracking.zentao_opened_by_name, "")).in_(name_pool)
                    )
                actor_clauses.append(
                    and_(
                        BugTracking.zentao_bug_id.isnot(None),
                        or_(*remote_clauses),
                    )
                )

            actor_clauses.append(
                and_(
                    BugTracking.created_by_id.in_(actor_ids),
                    or_(
                        BugTracking.zentao_bug_id.is_(None),
                        and_(
                            reliable_zentao_creator_absent,
                            or_(
                                BugTracking.zentao_sync_message.is_(None),
                                BugTracking.zentao_sync_message != bulk_sync_message,
                            ),
                        ),
                    ),
                )
            )

            return query.filter(or_(*actor_clauses))

        def closed_bug_query(actor_ids: list[int] | None, start_at: datetime, end_at: datetime):
            q = self.db.query(func.count(func.distinct(BugTracking.id))).filter(
                _not_deleted_clause(),
                _bug_close_time_col().isnot(None),
                _bug_close_time_col() >= start_at,
                _bug_close_time_col() <= end_at,
                func.lower(func.coalesce(BugTracking.zentao_live_status, "")) == "closed",
            )

            if actor_ids is None:
                return q

            actor_ids = [int(uid) for uid in actor_ids if uid]
            account_pool = sorted({
                account
                for uid in actor_ids
                for account in zentao_accounts_by_user.get(int(uid), set())
            })
            actor_clauses = []
            if actor_ids:
                actor_clauses.append(BugTracking.closed_by_id.in_(actor_ids))
            if account_pool:
                actor_clauses.append(
                    func.lower(func.coalesce(BugTracking.zentao_closed_by_account, "")).in_(account_pool)
                )
            if actor_clauses:
                return q.filter(or_(*actor_clauses))
            return q.filter(False)

        def metrics_for_user(uid: int) -> dict:
            executed_req_count = self._executed_requirements_count(sdt, edt, [uid], scoped_major_ids)
            case_count = (
                self.db.query(func.count(TestCase.id))
                .join(Requirement, TestCase.requirement_id == Requirement.id)
                .filter(TestCase.creator_id == uid, TestCase.created_at >= sdt, TestCase.created_at <= edt)
            )
            if linked_req_ids:
                case_count = case_count.filter(~TestCase.requirement_id.in_(list(linked_req_ids)))
            case_count = filter_by_major_ids(case_count, Requirement.major_version_id).scalar() or 0
            bug_count = self.db.query(func.count(BugTracking.id)).filter(
                _bug_time_col() >= sdt,
                _bug_time_col() <= edt,
                _not_deleted_clause(),
            )
            bug_count = apply_created_bug_actor_filter(bug_count, [uid])
            bug_count = filter_by_major_ids(bug_count, BugTracking.major_version_id).scalar() or 0
            retested_reqs = retest_transition_stats["by_actor"].get(uid, 0)
            closed_bugs = closed_bug_query([uid], sdt, edt)
            closed_bugs = filter_by_major_ids(closed_bugs, BugTracking.major_version_id).scalar() or 0
            created_feedbacks = self.db.query(func.count(FeedbackRecord.id)).filter(
                FeedbackRecord.creator_id == uid,
                FeedbackRecord.created_at >= sdt,
                FeedbackRecord.created_at <= edt,
            )
            created_feedbacks = filter_by_major_ids(created_feedbacks, FeedbackRecord.major_version_id).scalar() or 0
            processed_feedbacks = processed_feedback_stats["by_actor"].get(uid, 0)
            return {
                "executed_requirements": executed_req_count,
                "created_cases": case_count,
                "created_bugs": bug_count,
                "retested_reqs": retested_reqs,
                "closed_bugs": closed_bugs,
                "created_feedbacks": created_feedbacks,
                "processed_feedbacks": processed_feedbacks,
            }

        if all_users_mode:
            q_case = (
                self.db.query(func.count(TestCase.id))
                .join(Requirement, TestCase.requirement_id == Requirement.id)
                .filter(TestCase.created_at >= sdt, TestCase.created_at <= edt, TestCase.creator_id.in_(team_ids))
            )
            if linked_req_ids:
                q_case = q_case.filter(~TestCase.requirement_id.in_(list(linked_req_ids)))
            q_bug = self.db.query(func.count(BugTracking.id)).filter(
                _bug_time_col() >= sdt,
                _bug_time_col() <= edt,
                _not_deleted_clause(),
            )
            q_bug = apply_created_bug_actor_filter(q_bug, team_ids)
            q_closed = closed_bug_query(team_ids, sdt, edt)
            q_fb_created = self.db.query(func.count(FeedbackRecord.id)).filter(
                FeedbackRecord.created_at >= sdt,
                FeedbackRecord.created_at <= edt,
                FeedbackRecord.creator_id.in_(team_ids),
            )
            overview = {
                "executed_requirements": self._executed_requirements_count(sdt, edt, team_ids, scoped_major_ids),
                "created_cases": filter_by_major_ids(q_case, Requirement.major_version_id).scalar() or 0,
                "created_bugs": filter_by_major_ids(q_bug, BugTracking.major_version_id).scalar() or 0,
                "retested_reqs": retest_transition_stats["total"],
                "closed_bugs": filter_by_major_ids(q_closed, BugTracking.major_version_id).scalar() or 0,
                "created_feedbacks": filter_by_major_ids(q_fb_created, FeedbackRecord.major_version_id).scalar() or 0,
                "processed_feedbacks": processed_feedback_stats["total"],
            }
        else:
            overview = metrics_for_user(target_user_id)

        trend = []
        cur = start_date
        while cur <= end_date:
            day_s = datetime.combine(cur, datetime.min.time())
            day_e = datetime.combine(cur, datetime.max.time())
            if all_users_mode:
                q_case = (
                    self.db.query(func.count(TestCase.id))
                    .join(Requirement, TestCase.requirement_id == Requirement.id)
                    .filter(TestCase.created_at >= day_s, TestCase.created_at <= day_e, TestCase.creator_id.in_(team_ids))
                )
                if linked_req_ids:
                    q_case = q_case.filter(~TestCase.requirement_id.in_(list(linked_req_ids)))
                q_bug = self.db.query(func.count(BugTracking.id)).filter(
                    _bug_time_col() >= day_s,
                    _bug_time_col() <= day_e,
                    _not_deleted_clause(),
                )
                q_bug = apply_created_bug_actor_filter(q_bug, team_ids)
                q_closed = closed_bug_query(team_ids, day_s, day_e)
                q_fb_created = self.db.query(func.count(FeedbackRecord.id)).filter(
                    FeedbackRecord.created_at >= day_s,
                    FeedbackRecord.created_at <= day_e,
                    FeedbackRecord.creator_id.in_(team_ids),
                )
                day_exec = self._executed_requirements_count(day_s, day_e, team_ids, scoped_major_ids)
                day_case = filter_by_major_ids(q_case, Requirement.major_version_id).scalar() or 0
                day_bug = filter_by_major_ids(q_bug, BugTracking.major_version_id).scalar() or 0
                day_retested = retest_transition_stats["by_day"].get(cur.isoformat(), 0)
                day_closed = filter_by_major_ids(q_closed, BugTracking.major_version_id).scalar() or 0
                day_fb_created = filter_by_major_ids(q_fb_created, FeedbackRecord.major_version_id).scalar() or 0
                day_fb_processed = processed_feedback_stats["by_day"].get(cur.isoformat(), 0)
            else:
                q_case = (
                    self.db.query(func.count(TestCase.id))
                    .join(Requirement, TestCase.requirement_id == Requirement.id)
                    .filter(TestCase.creator_id == target_user_id, TestCase.created_at >= day_s, TestCase.created_at <= day_e)
                )
                if linked_req_ids:
                    q_case = q_case.filter(~TestCase.requirement_id.in_(list(linked_req_ids)))
                q_bug = self.db.query(func.count(BugTracking.id)).filter(
                    _bug_time_col() >= day_s,
                    _bug_time_col() <= day_e,
                    _not_deleted_clause(),
                )
                q_bug = apply_created_bug_actor_filter(q_bug, [target_user_id])
                q_closed = closed_bug_query([target_user_id], day_s, day_e)
                q_fb_created = self.db.query(func.count(FeedbackRecord.id)).filter(
                    FeedbackRecord.created_at >= day_s,
                    FeedbackRecord.created_at <= day_e,
                    FeedbackRecord.creator_id == target_user_id,
                )
                day_exec = self._executed_requirements_count(day_s, day_e, [target_user_id], scoped_major_ids)
                day_case = filter_by_major_ids(q_case, Requirement.major_version_id).scalar() or 0
                day_bug = filter_by_major_ids(q_bug, BugTracking.major_version_id).scalar() or 0
                day_retested = retest_transition_stats["by_day"].get(cur.isoformat(), 0)
                day_closed = filter_by_major_ids(q_closed, BugTracking.major_version_id).scalar() or 0
                day_fb_created = filter_by_major_ids(q_fb_created, FeedbackRecord.major_version_id).scalar() or 0
                day_fb_processed = processed_feedback_stats["by_day"].get(cur.isoformat(), 0)
            trend.append({
                "date": cur.isoformat(),
                "executed_requirements": day_exec,
                "created_cases": day_case,
                "created_bugs": day_bug,
                "retested_reqs": day_retested,
                "closed_bugs": day_closed,
                "created_feedbacks": day_fb_created,
                "processed_feedbacks": day_fb_processed,
            })
            cur += timedelta(days=1)

        bug_dist_query = self.db.query(BugTracking.source_type, func.count(BugTracking.id)).filter(
            _bug_time_col() >= sdt,
            _bug_time_col() <= edt,
            _not_deleted_clause(),
        )
        bug_dist_query = filter_by_major_ids(bug_dist_query, BugTracking.major_version_id)
        if all_users_mode:
            bug_dist_query = apply_created_bug_actor_filter(bug_dist_query, team_ids)
        else:
            bug_dist_query = apply_created_bug_actor_filter(bug_dist_query, [target_user_id])
        bug_source_dist = [{"source_type": (k.value if hasattr(k, 'value') else str(k)), "count": v} for k, v in bug_dist_query.group_by(BugTracking.source_type).all()]

        # 大版本 Bug 分布：与来源分布完全同口径（时间/删除/人员/大版本过滤），仅分组维度不同
        major_dist_query = self.db.query(BugTracking.major_version_id, func.count(BugTracking.id)).filter(
            _bug_time_col() >= sdt,
            _bug_time_col() <= edt,
            _not_deleted_clause(),
        )
        major_dist_query = filter_by_major_ids(major_dist_query, BugTracking.major_version_id)
        if all_users_mode:
            major_dist_query = apply_created_bug_actor_filter(major_dist_query, team_ids)
        else:
            major_dist_query = apply_created_bug_actor_filter(major_dist_query, [target_user_id])
        major_rows = major_dist_query.group_by(BugTracking.major_version_id).all()
        found_major_ids = [mid for mid, _ in major_rows if mid]
        major_name_map = (
            {v.id: v.version_no for v in self.db.query(Version).filter(Version.id.in_(found_major_ids)).all()}
            if found_major_ids else {}
        )
        bug_major_dist = sorted(
            (
                {
                    "major_version_id": mid,
                    "major_version_no": (major_name_map.get(mid, "未知大版本") if mid else "未关联大版本"),
                    "count": cnt,
                }
                for mid, cnt in major_rows
            ),
            key=lambda r: r["count"],
            reverse=True,
        )

        result = {
            "overview": overview,
            "trend": trend,
            "bug_source_dist": bug_source_dist,
            "bug_major_dist": bug_major_dist,
            "target_user_id": None if all_users_mode else target_user_id,
        }
        if all_users_mode:
            team = []
            all_users = self.db.query(User).filter(User.is_team_member.is_(True)).order_by(User.id.asc()).all()
            for u in all_users:
                m = metrics_for_user(u.id)
                team.append({"user_id": u.id, "username": u.shown_name, **m})

            # Aggregate row for bugs owned by zentao_sync_bot or any other
            # non-team-member account (mostly from historical Zentao data
            # whose opener cannot be mapped to a local user).
            from app.db.seed import ZENTAO_SYNC_BOT_USERNAME
            non_team_users = (
                self.db.query(User)
                .filter(
                    or_(User.is_team_member.is_(False), User.is_team_member.is_(None)),
                    User.username != "admin",
                )
                .all()
            )
            nt_ids = [u.id for u in non_team_users]
            if nt_ids:
                # Use a single aggregated pseudo-row so the report stays compact.
                def _agg(uid_list):
                    created_bug_query = self.db.query(func.count(BugTracking.id)).filter(
                        _bug_time_col() >= sdt,
                        _bug_time_col() <= edt,
                        _not_deleted_clause(),
                    )
                    created_bug_query = apply_created_bug_actor_filter(created_bug_query, uid_list)
                    return {
                        "executed_requirements": 0,
                        "created_cases": 0,
                        "created_bugs": created_bug_query.scalar() or 0,
                        "retested_reqs": 0,
                        "closed_bugs": 0,
                        "created_feedbacks": 0,
                        "processed_feedbacks": 0,
                    }
                team.append({
                    "user_id": 0,
                    "username": "未归属 / 禅道同步",
                    **_agg(nt_ids),
                })
            result["team_comparison"] = team
        return result

    def advanced(self, start_date: date, end_date: date, major_version_id: int | None = None, software_id: int | None = None) -> dict:
        sdt = datetime.combine(start_date, datetime.min.time())
        edt = datetime.combine(end_date, datetime.max.time())
        req_bugs_query = self.db.query(Requirement.zentao_req_id, Requirement.title, func.count(BugTracking.id).label("bug_count")) \
            .join(BugTracking, BugTracking.requirement_id == Requirement.id) \
            .filter(_bug_time_col() >= sdt, _bug_time_col() <= edt, _not_deleted_clause())
        if major_version_id:
            req_bugs_query = req_bugs_query.filter(Requirement.major_version_id == major_version_id)
        elif software_id:
            req_bugs_query = req_bugs_query.join(Version, Requirement.major_version_id == Version.id).filter(Version.software_id == software_id)
        req_bugs = req_bugs_query.group_by(Requirement.id).order_by(func.count(BugTracking.id).desc()).limit(7).all()
        top_reqs = [{"req_id": r[0], "title": r[1], "count": r[2]} for r in req_bugs]
        bug_base_query = self.db.query(BugTracking).filter(_bug_time_col() >= sdt, _bug_time_col() <= edt, _not_deleted_clause())
        if major_version_id:
            bug_base_query = bug_base_query.filter(BugTracking.major_version_id == major_version_id)
        elif software_id:
            major_ids = [m.id for m in self.db.query(Version).filter(Version.version_type == VersionType.MAJOR, Version.software_id == software_id).all()]
            bug_base_query = bug_base_query.filter(BugTracking.major_version_id.in_(major_ids if major_ids else [-1]))
        # Leakage (漏测率): 原测发现 vs 复测新增。分母只算归属到需求/用例的
        # bug，MANUAL (多数来自禅道全量同步的未挂需求 bug) 不参与，避免稀释。
        retest_bugs = bug_base_query.filter(BugTracking.source_type == BugSourceType.RETEST).count() or 0
        case_bugs = bug_base_query.filter(BugTracking.source_type == BugSourceType.CASE).count() or 0
        requirement_bugs = bug_base_query.filter(BugTracking.source_type == BugSourceType.REQUIREMENT).count() or 0
        normal_bugs = case_bugs + requirement_bugs
        manual_bugs = bug_base_query.filter(BugTracking.source_type == BugSourceType.MANUAL).count() or 0
        total_bugs = bug_base_query.count() or 0

        # 4-级漏斗（取代原来的 total/fixed/closed 三级）：
        # total    = 发现 Bug 总数（期间内所有未删除 bug）
        # resolved = 开发侧已解决（禅道 live_status ∈ {resolved, closed}）
        # closed   = 禅道已关闭（live_status == closed）
        # verified = 本地 Stage5 完成验收（closed=True）
        resolved_bugs = bug_base_query.filter(
            func.lower(func.coalesce(BugTracking.zentao_live_status, "")).in_(["resolved", "closed"])
        ).count() or 0
        closed_bugs = bug_base_query.filter(
            func.lower(func.coalesce(BugTracking.zentao_live_status, "")) == "closed"
        ).count() or 0
        verified_bugs = bug_base_query.filter(BugTracking.closed.is_(True)).count() or 0

        exec_query = self.db.query(TestExecution.result_status, func.count(TestExecution.id)).filter(TestExecution.executed_at >= sdt, TestExecution.executed_at <= edt)
        if major_version_id:
            exec_query = exec_query.join(Requirement, TestExecution.requirement_id == Requirement.id).filter(Requirement.major_version_id == major_version_id)
        elif software_id:
            exec_query = exec_query.join(Requirement, TestExecution.requirement_id == Requirement.id).join(Version, Requirement.major_version_id == Version.id).filter(Version.software_id == software_id)
        exec_results = exec_query.group_by(TestExecution.result_status).all()
        executions = [{"status": r[0], "count": r[1]} for r in exec_results]
        return {
            "top_reqs": top_reqs,
            "leakage": {
                "retest": retest_bugs,
                "normal": normal_bugs,
                "case": case_bugs,
                "requirement": requirement_bugs,
                "manual": manual_bugs,
            },
            "funnel": {
                "total": total_bugs,
                "resolved": resolved_bugs,
                "closed": closed_bugs,
                "verified": verified_bugs,
                # Backward-compat keys for any old frontend still reading these
                "fixed": resolved_bugs,
            },
            "executions": executions,
        }

    def version_bugs(self, major_version_id: int | None = None) -> list[dict]:
        minor_query = self.db.query(Version).filter(Version.version_type == VersionType.MINOR)
        if major_version_id:
            minor_query = minor_query.filter(Version.parent_id == major_version_id)
        minor_versions = minor_query.all()
        result = []
        for mv in minor_versions:
            bug_count = self.db.query(BugTracking).filter(BugTracking.found_minor_version_id == mv.id).count()
            if bug_count > 0:
                parent = self.db.query(Version).filter(Version.id == mv.parent_id).first()
                parent_name = parent.version_no if parent else "未知大版本"
                result.append({
                    # version_name 保留旧的两行格式做向后兼容
                    "version_name": f"{parent_name}\n{mv.version_no}",
                    "major_name": parent_name,
                    "minor_name": mv.version_no,
                    "bug_count": bug_count,
                })
        # 按检出 Bug 数降序：前端横向条形图「哪个发包最多」一眼可见
        result.sort(key=lambda r: r["bug_count"], reverse=True)
        return result

    def governance(
        self,
        start_date: date,
        end_date: date,
        major_version_id: int | None = None,
        software_id: int | None = None,
        req_overdue_days: int = 14,
        feedback_overdue_days: int = 7,
        bug_overdue_days: int = 7,
        stale_bug_days: int = 14,
    ) -> dict:
        sdt = datetime.combine(start_date, datetime.min.time())
        edt = datetime.combine(end_date, datetime.max.time())
        now = local_now()

        req_overdue_dt = now - timedelta(days=req_overdue_days)
        fb_overdue_dt = now - timedelta(days=feedback_overdue_days)
        bug_overdue_dt = now - timedelta(days=bug_overdue_days)
        stale_bug_dt = now - timedelta(days=stale_bug_days)

        majors = {v.id: v.version_no for v in self.db.query(Version).filter(Version.version_type == VersionType.MAJOR).all()}
        minors = {v.id: v.version_no for v in self.db.query(Version).filter(Version.version_type == VersionType.MINOR).all()}
        users = {u.id: u.shown_name for u in self.db.query(User).all()}

        def _days_since(dt: datetime | None) -> int:
            if not dt:
                return 0
            return max(0, (now - dt).days)

        def _build_aging(days_list: list[int]) -> dict:
            bands = {"0-3天": 0, "4-7天": 0, "8-14天": 0, "15-30天": 0, "30天以上": 0}
            for d in days_list:
                if d <= 3:
                    bands["0-3天"] += 1
                elif d <= 7:
                    bands["4-7天"] += 1
                elif d <= 14:
                    bands["8-14天"] += 1
                elif d <= 30:
                    bands["15-30天"] += 1
                else:
                    bands["30天以上"] += 1
            ordered = [{"bucket": k, "count": v} for k, v in bands.items()]
            sorted_days = sorted(days_list)
            median = 0
            if sorted_days:
                n = len(sorted_days)
                if n % 2 == 1:
                    median = float(sorted_days[n // 2])
                else:
                    median = (sorted_days[n // 2 - 1] + sorted_days[n // 2]) / 2
            avg = round(sum(days_list) / len(days_list), 2) if days_list else 0
            return {"bands": ordered, "avg_days": avg, "median_days": median, "samples": len(days_list)}

        scoped_major_ids: list[int] | None = None
        if major_version_id:
            scoped_major_ids = [major_version_id]
        elif software_id:
            scoped_major_ids = [v.id for v in self.db.query(Version).filter(Version.version_type == VersionType.MAJOR, Version.software_id == software_id).all()]
        req_query = self.db.query(Requirement).filter(Requirement.created_at >= sdt, Requirement.created_at <= edt)
        if scoped_major_ids is not None:
            req_query = req_query.filter(Requirement.major_version_id.in_(scoped_major_ids if scoped_major_ids else [-1]))
        req_rows = req_query.all()

        overdue_requirements = []
        for r in req_rows:
            is_done = (r.status == RequirementStatus.RETEST_DONE)
            if (not is_done) and r.created_at <= req_overdue_dt:
                overdue_requirements.append({
                    "id": r.id,
                    "zentao_req_id": r.zentao_req_id,
                    "title": r.title,
                    "major_version_no": majors.get(r.major_version_id, "未知"),
                    "status": r.status.value if hasattr(r.status, "value") else str(r.status),
                    "owner_name": users.get(r.owner_id, "未分配"),
                    "created_at": r.created_at.isoformat(),
                    "age_days": _days_since(r.created_at),
                })
        overdue_requirements.sort(key=lambda x: x["age_days"], reverse=True)
        req_closed_days = []
        for r in req_rows:
            if r.status == RequirementStatus.RETEST_DONE and r.updated_at:
                req_closed_days.append(max(0, (r.updated_at - r.created_at).days))

        req_status_stay_distribution: list[dict] = []
        req_ids = [r.id for r in req_rows]
        if req_ids:
            hist_rows = (
                self.db.query(RequirementStatusHistory)
                .filter(RequirementStatusHistory.requirement_id.in_(req_ids))
                .order_by(RequirementStatusHistory.requirement_id.asc(), RequirementStatusHistory.changed_at.asc())
                .all()
            )
            hist_map: dict[int, list[RequirementStatusHistory]] = {}
            for h in hist_rows:
                hist_map.setdefault(h.requirement_id, []).append(h)

            stay_bucket: dict[str, list[float]] = {}
            req_map = {r.id: r for r in req_rows}
            for rid, items in hist_map.items():
                req = req_map.get(rid)
                if not req:
                    continue
                for idx, cur_h in enumerate(items):
                    end_time = items[idx + 1].changed_at if idx + 1 < len(items) else (req.updated_at or now)
                    dur_days = max(0.0, (end_time - cur_h.changed_at).total_seconds() / 86400.0)
                    key = cur_h.to_status.value if hasattr(cur_h.to_status, "value") else str(cur_h.to_status)
                    stay_bucket.setdefault(key, []).append(dur_days)

            if stay_bucket:
                for status_key, values in stay_bucket.items():
                    req_status_stay_distribution.append({
                        "status": status_key,
                        "avg_days": round(sum(values) / len(values), 2),
                        "max_days": round(max(values), 2),
                        "samples": len(values),
                    })
                req_status_stay_distribution.sort(key=lambda x: x["avg_days"], reverse=True)

        fb_query = self.db.query(FeedbackRecord).filter(FeedbackRecord.created_at >= sdt, FeedbackRecord.created_at <= edt)
        if scoped_major_ids is not None:
            fb_query = fb_query.filter(FeedbackRecord.major_version_id.in_(scoped_major_ids if scoped_major_ids else [-1]))
        fb_rows = fb_query.all()

        feedback_overdue_rows = []
        for f in fb_rows:
            is_done = f.status in [FeedbackStatus.RESOLVED, FeedbackStatus.CLOSED]
            if (not is_done) and f.created_at <= fb_overdue_dt:
                feedback_overdue_rows.append({
                    "id": f.id,
                    "feedback_no": f.feedback_no or "-",
                    "summary": f.summary,
                    "major_version_no": majors.get(f.major_version_id, "未知"),
                    "minor_version_no": minors.get(f.minor_version_id, "未知"),
                    "status": f.status.value if hasattr(f.status, "value") else str(f.status),
                    "assignee_name": users.get(f.assignee_id, "未指派"),
                    "created_at": f.created_at.isoformat(),
                    "age_days": _days_since(f.created_at),
                })
        feedback_overdue_rows.sort(key=lambda x: x["age_days"], reverse=True)

        fb_closed_days = []
        for f in fb_rows:
            if f.status in [FeedbackStatus.RESOLVED, FeedbackStatus.CLOSED]:
                end_dt = f.handled_at or f.updated_at
                if end_dt:
                    fb_closed_days.append(max(0, (end_dt - f.created_at).days))

        feedback_version_top_query = self.db.query(
            FeedbackRecord.major_version_id,
            FeedbackRecord.minor_version_id,
            func.count(FeedbackRecord.id),
            func.sum(case((FeedbackRecord.status.in_([FeedbackStatus.RESOLVED, FeedbackStatus.CLOSED]), 1), else_=0)),
        ).filter(FeedbackRecord.created_at >= sdt, FeedbackRecord.created_at <= edt)
        if scoped_major_ids is not None:
            feedback_version_top_query = feedback_version_top_query.filter(FeedbackRecord.major_version_id.in_(scoped_major_ids if scoped_major_ids else [-1]))
        feedback_version_top = []
        for maj_id, min_id, total_cnt, done_cnt in (
            feedback_version_top_query.group_by(FeedbackRecord.major_version_id, FeedbackRecord.minor_version_id)
            .order_by(func.count(FeedbackRecord.id).desc())
            .limit(10)
            .all()
        ):
            done = int(done_cnt or 0)
            total = int(total_cnt or 0)
            feedback_version_top.append({
                "major_version_no": majors.get(maj_id, "未知"),
                "minor_version_no": minors.get(min_id, "未知"),
                "total": total,
                "done": done,
                "undone": max(0, total - done),
            })

        bug_query = self.db.query(BugTracking).filter(_bug_time_col() >= sdt, _bug_time_col() <= edt, _not_deleted_clause())
        if scoped_major_ids is not None:
            bug_query = bug_query.filter(BugTracking.major_version_id.in_(scoped_major_ids if scoped_major_ids else [-1]))
        bug_rows = bug_query.all()

        def _is_open(bug: BugTracking) -> bool:
            """
            A bug is "open" (subject to overdue/stale/unassigned checks)
            only if it is neither locally verified nor closed on Zentao.
            """
            if bug.closed:
                return False
            live = (bug.zentao_live_status or "").strip().lower()
            if live == "closed":
                return False
            return True

        def _is_unassigned(bug: BugTracking) -> bool:
            """
            Both the local dispatched_to and the cached Zentao assignee must
            be empty for "无人处理". Previously only the local field was
            checked, flagging every Zentao-synced bug as unassigned.
            """
            if bug.dispatched_to_id is not None:
                return False
            zt_account = (bug.zentao_assigned_to_account or "").strip()
            return not zt_account or zt_account.lower() == "closed"

        unassigned_bugs = []
        overdue_bugs = []
        stale_bugs = []
        assigned_no_progress = []
        for b in bug_rows:
            open_time = _bug_time_value(b) or b.created_at
            update_time = b.zentao_remote_updated_at or b.updated_at
            b_item = {
                "id": b.id,
                "bug_id": b.bug_id,
                "zentao_bug_url": b.zentao_bug_url,
                "zentao_bug_title": b.zentao_bug_title,
                "major_version_no": majors.get(b.major_version_id, "未知"),
                "status": "closed" if (b.closed or (b.zentao_live_status or "").lower() == "closed") else "open",
                "dispatched_to_name": (
                    users.get(b.dispatched_to_id)
                    or b.zentao_assigned_to_name
                    or "未指派"
                ),
                "created_at": (open_time or b.created_at).isoformat(),
                "updated_at": update_time.isoformat() if update_time else None,
                "age_days": _days_since(open_time),
                "stale_days": _days_since(update_time),
            }
            open_flag = _is_open(b)
            if open_flag and _is_unassigned(b):
                unassigned_bugs.append(b_item)
            if open_flag and open_time and open_time <= bug_overdue_dt:
                overdue_bugs.append(b_item)
            if open_flag and update_time and update_time <= stale_bug_dt:
                stale_bugs.append(b_item)
            if open_flag and (not _is_unassigned(b)) and update_time and update_time <= bug_overdue_dt:
                assigned_no_progress.append(b_item)

        unassigned_bugs.sort(key=lambda x: x["age_days"], reverse=True)
        overdue_bugs.sort(key=lambda x: x["age_days"], reverse=True)
        stale_bugs.sort(key=lambda x: x["stale_days"], reverse=True)
        assigned_no_progress.sort(key=lambda x: x["stale_days"], reverse=True)

        # Close aging now uses real Zentao close_date - opened_at when
        # available, so a bug that was filed 90d ago in Zentao and synced
        # yesterday no longer shows as "closed in 0 days".
        bug_closed_days = []
        for b in bug_rows:
            is_closed = b.closed or (b.zentao_live_status or "").lower() == "closed"
            if not is_closed:
                continue
            close_dt = b.zentao_close_date or b.updated_at
            open_dt = b.zentao_opened_at or b.created_at
            if close_dt and open_dt:
                bug_closed_days.append(max(0, (close_dt - open_dt).days))

        req_bug_top_query = self.db.query(
            Requirement.id,
            Requirement.zentao_req_id,
            Requirement.title,
            Requirement.major_version_id,
            Requirement.status,
            func.count(BugTracking.id).label("bug_count"),
        ).join(BugTracking, BugTracking.requirement_id == Requirement.id).filter(_bug_time_col() >= sdt, _bug_time_col() <= edt, _not_deleted_clause())
        if scoped_major_ids is not None:
            req_bug_top_query = req_bug_top_query.filter(Requirement.major_version_id.in_(scoped_major_ids if scoped_major_ids else [-1]))
        req_bug_top = []
        for rid, req_id, title, maj_id, status, bug_count in (
            req_bug_top_query.group_by(Requirement.id).order_by(func.count(BugTracking.id).desc()).limit(10).all()
        ):
            req_bug_top.append({
                "id": rid,
                "zentao_req_id": req_id,
                "title": title,
                "major_version_no": majors.get(maj_id, "未知"),
                "status": status.value if hasattr(status, "value") else str(status),
                "bug_count": int(bug_count or 0),
            })

        req_feedback_top_query = self.db.query(
            Requirement.id,
            Requirement.zentao_req_id,
            Requirement.title,
            Requirement.major_version_id,
            Requirement.status,
            func.count(func.distinct(FeedbackBugLink.feedback_id)).label("feedback_count"),
        ).join(BugTracking, BugTracking.requirement_id == Requirement.id).join(FeedbackBugLink, FeedbackBugLink.bug_id == BugTracking.id).join(
            FeedbackRecord, FeedbackRecord.id == FeedbackBugLink.feedback_id
        ).filter(FeedbackRecord.created_at >= sdt, FeedbackRecord.created_at <= edt)
        if scoped_major_ids is not None:
            req_feedback_top_query = req_feedback_top_query.filter(Requirement.major_version_id.in_(scoped_major_ids if scoped_major_ids else [-1]))
        req_feedback_top = []
        for rid, req_id, title, maj_id, status, feedback_count in (
            req_feedback_top_query.group_by(Requirement.id).order_by(func.count(func.distinct(FeedbackBugLink.feedback_id)).desc()).limit(10).all()
        ):
            req_feedback_top.append({
                "id": rid,
                "zentao_req_id": req_id,
                "title": title,
                "major_version_no": majors.get(maj_id, "未知"),
                "status": status.value if hasattr(status, "value") else str(status),
                "feedback_count": int(feedback_count or 0),
            })

        feedback_with_bug = (
            self.db.query(func.count(func.distinct(FeedbackBugLink.feedback_id)))
            .join(FeedbackRecord, FeedbackRecord.id == FeedbackBugLink.feedback_id)
            .filter(FeedbackRecord.created_at >= sdt, FeedbackRecord.created_at <= edt)
        )
        if scoped_major_ids is not None:
            feedback_with_bug = feedback_with_bug.filter(FeedbackRecord.major_version_id.in_(scoped_major_ids if scoped_major_ids else [-1]))
        feedback_with_bug_count = int(feedback_with_bug.scalar() or 0)
        feedback_total = len(fb_rows)
        feedback_to_bug_ratio = round((feedback_with_bug_count / feedback_total) * 100, 2) if feedback_total else 0.0

        return {
            "meta": {
                "req_overdue_days": req_overdue_days,
                "feedback_overdue_days": feedback_overdue_days,
                "bug_overdue_days": bug_overdue_days,
                "stale_bug_days": stale_bug_days,
                "degraded_state_duration": len(req_status_stay_distribution) == 0,
                "degraded_reason": (
                    "当前缺少完整状态流转历史，阶段一用关闭耗时分布替代状态停留时长分布。"
                    if len(req_status_stay_distribution) == 0
                    else "已基于需求状态流转历史计算停留时长分布。"
                ),
            },
            "kpis": {
                "overdue_requirements": len(overdue_requirements),
                "overdue_feedbacks": len(feedback_overdue_rows),
                "unassigned_bugs": len(unassigned_bugs),
                "overdue_bugs": len(overdue_bugs),
                "stale_bugs": len(stale_bugs),
                "assigned_no_progress_bugs": len(assigned_no_progress),
                "feedback_total": feedback_total,
                "feedback_pending": len([f for f in fb_rows if f.status == FeedbackStatus.PENDING]),
                "feedback_processing": len([f for f in fb_rows if f.status == FeedbackStatus.PROCESSING]),
                "feedback_resolved": len([f for f in fb_rows if f.status == FeedbackStatus.RESOLVED]),
                "feedback_closed": len([f for f in fb_rows if f.status == FeedbackStatus.CLOSED]),
                "feedback_with_bug_count": feedback_with_bug_count,
                "feedback_to_bug_ratio": feedback_to_bug_ratio,
            },
            "requirements": {
                "overdue_list": overdue_requirements[:100],
                "close_aging": _build_aging(req_closed_days),
                "status_stay_distribution": req_status_stay_distribution,
                "top_feedback_reqs": req_feedback_top,
                "top_bug_reqs": req_bug_top,
            },
            "feedback": {
                "overdue_list": feedback_overdue_rows[:100],
                "close_aging": _build_aging(fb_closed_days),
                "version_top10": feedback_version_top,
            },
            "bugs": {
                "unassigned_list": unassigned_bugs[:100],
                "overdue_list": overdue_bugs[:100],
                "stale_list": stale_bugs[:100],
                "assigned_no_progress_list": assigned_no_progress[:100],
                "close_aging": _build_aging(bug_closed_days),
                "top_overdue": overdue_bugs[:10],
                "top_stale": stale_bugs[:10],
                "top_assigned_no_progress": assigned_no_progress[:10],
            },
        }

    def zentao_sync_stats(
        self,
        major_version_id: int | None = None,
        software_id: int | None = None,
        stale_minutes: int = 60,
    ) -> dict:
        """
        Return Zentao sync health stats:
        - stale_sync: bugs not synced for > stale_minutes
        - zentao_closed_no_local: bugs with zentao_live_status=closed but no local closure record
        - local_closed_no_zentao: bugs with a local closure record but zentao status != closed
        - zentao_deleted_count: bugs soft-deleted in Zentao
        """
        now = local_now()
        stale_cutoff = now - timedelta(minutes=stale_minutes)

        # Build scope filter
        scoped_major_ids: list[int] | None = None
        if major_version_id:
            scoped_major_ids = [major_version_id]
        elif software_id:
            scoped_major_ids = [
                v.id
                for v in self.db.query(Version)
                .filter(Version.version_type == VersionType.MAJOR, Version.software_id == software_id)
                .all()
            ]

        base_q = self.db.query(BugTracking).filter(
            BugTracking.zentao_bug_id.isnot(None),
            BugTracking.zentao_deleted.isnot(True),
        )
        if scoped_major_ids is not None:
            base_q = base_q.filter(
                BugTracking.major_version_id.in_(scoped_major_ids if scoped_major_ids else [-1])
            )

        all_zentao_bugs = base_q.all()

        # 1. Stale sync: synced but outdated
        stale_sync = [
            {
                "id": b.id,
                "bug_id": b.bug_id,
                "zentao_bug_id": b.zentao_bug_id,
                "title": b.zentao_bug_title or "",
                "last_synced_at": b.last_zentao_synced_at.strftime("%Y-%m-%d %H:%M") if b.last_zentao_synced_at else "从未同步",
                "status": b.zentao_live_status or "",
            }
            for b in all_zentao_bugs
            if (b.last_zentao_synced_at is None or b.last_zentao_synced_at < stale_cutoff)
        ]

        # 2. Zentao closed but no local closure record (any user)
        closed_zentao_ids = {b.id for b in all_zentao_bugs if (b.zentao_live_status or "").lower() == "closed"}
        local_closure_bug_ids = {
            r.bug_tracking_id
            for r in self.db.query(BugStage5Record)
            .filter(BugStage5Record.bug_tracking_id.in_(closed_zentao_ids), BugStage5Record.test_done.is_(True))
            .all()
        } if closed_zentao_ids else set()
        zentao_closed_no_local = [
            {
                "id": b.id,
                "bug_id": b.bug_id,
                "zentao_bug_id": b.zentao_bug_id,
                "title": b.zentao_bug_title or "",
                "closed_by": b.zentao_closed_by_name or b.zentao_closed_by_account or "",
                "close_date": b.zentao_close_date.strftime("%Y-%m-%d") if b.zentao_close_date else "",
            }
            for b in all_zentao_bugs
            if b.id in closed_zentao_ids and b.id not in local_closure_bug_ids
        ]

        # 3. Local closure but Zentao not closed
        local_closed_ids = {
            r.bug_tracking_id
            for r in self.db.query(BugStage5Record)
            .filter(BugStage5Record.test_done.is_(True))
            .all()
        }
        local_closed_no_zentao = [
            {
                "id": b.id,
                "bug_id": b.bug_id,
                "zentao_bug_id": b.zentao_bug_id,
                "title": b.zentao_bug_title or "",
                "zentao_status": b.zentao_live_status or "",
                "last_synced_at": b.last_zentao_synced_at.strftime("%Y-%m-%d %H:%M") if b.last_zentao_synced_at else "从未同步",
            }
            for b in all_zentao_bugs
            if b.id in local_closed_ids and (b.zentao_live_status or "").lower() not in ("closed", "")
        ]

        # 4. Deleted count
        deleted_q = self.db.query(BugTracking).filter(BugTracking.zentao_deleted.is_(True))
        if scoped_major_ids is not None:
            deleted_q = deleted_q.filter(
                BugTracking.major_version_id.in_(scoped_major_ids if scoped_major_ids else [-1])
            )
        zentao_deleted_count = deleted_q.count()

        return {
            "stale_sync_count": len(stale_sync),
            "zentao_closed_no_local_count": len(zentao_closed_no_local),
            "local_closed_no_zentao_count": len(local_closed_no_zentao),
            "zentao_deleted_count": zentao_deleted_count,
            "stale_sync": stale_sync[:50],
            "zentao_closed_no_local": zentao_closed_no_local[:50],
            "local_closed_no_zentao": local_closed_no_zentao[:50],
            "stale_minutes": stale_minutes,
        }
