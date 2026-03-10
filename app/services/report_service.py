from __future__ import annotations

from datetime import date, datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import case, func
from sqlalchemy.orm import Session

from app.models import (
    AuditLog,
    BugSourceType,
    BugStage5Record,
    BugTracking,
    FeedbackBugLink,
    FeedbackRecord,
    FeedbackStatus,
    Requirement,
    RequirementStatus,
    RequirementStatusHistory,
    TestCase,
    TestExecution,
    User,
    UserRole,
    Version,
    VersionType,
)


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

        def filter_by_major_ids(query, column):
            if scoped_major_ids is None:
                return query
            if len(scoped_major_ids) == 0:
                return query.filter(False)
            return query.filter(column.in_(scoped_major_ids))

        def metrics_for_user(uid: int) -> dict:
            executed_req_count = (
                self.db.query(func.count(func.distinct(TestExecution.requirement_id)))
                .join(Requirement, TestExecution.requirement_id == Requirement.id)
                .filter(TestExecution.executed_by_id == uid, TestExecution.executed_at >= sdt, TestExecution.executed_at <= edt)
            )
            executed_req_count = filter_by_major_ids(executed_req_count, Requirement.major_version_id).scalar() or 0
            case_count = (
                self.db.query(func.count(TestCase.id))
                .join(Requirement, TestCase.requirement_id == Requirement.id)
                .filter(TestCase.creator_id == uid, TestCase.created_at >= sdt, TestCase.created_at <= edt)
            )
            case_count = filter_by_major_ids(case_count, Requirement.major_version_id).scalar() or 0
            bug_count = self.db.query(func.count(BugTracking.id)).filter(BugTracking.created_by_id == uid, BugTracking.created_at >= sdt, BugTracking.created_at <= edt)
            bug_count = filter_by_major_ids(bug_count, BugTracking.major_version_id).scalar() or 0
            retested_reqs = self.db.query(func.count(Requirement.id)).filter(
                Requirement.retested_by_id == uid, Requirement.retested_at >= sdt, Requirement.retested_at <= edt
            )
            retested_reqs = filter_by_major_ids(retested_reqs, Requirement.major_version_id).scalar() or 0
            closed_bugs = (
                self.db.query(func.count(func.distinct(BugStage5Record.bug_tracking_id)))
                .join(BugTracking, BugStage5Record.bug_tracking_id == BugTracking.id)
                .filter(
                    BugStage5Record.user_id == uid,
                    BugStage5Record.updated_at >= sdt,
                    BugStage5Record.updated_at <= edt,
                    BugStage5Record.test_done.is_(True),
                    BugStage5Record.newly_found_bug_id.is_(None),
                )
            )
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
            q_exec = (
                self.db.query(func.count(func.distinct(TestExecution.requirement_id)))
                .join(Requirement, TestExecution.requirement_id == Requirement.id)
                .filter(TestExecution.executed_at >= sdt, TestExecution.executed_at <= edt, TestExecution.executed_by_id.in_(team_ids))
            )
            q_case = (
                self.db.query(func.count(TestCase.id))
                .join(Requirement, TestCase.requirement_id == Requirement.id)
                .filter(TestCase.created_at >= sdt, TestCase.created_at <= edt, TestCase.creator_id.in_(team_ids))
            )
            q_bug = self.db.query(func.count(BugTracking.id)).filter(BugTracking.created_at >= sdt, BugTracking.created_at <= edt, BugTracking.created_by_id.in_(team_ids))
            q_retest = self.db.query(func.count(Requirement.id)).filter(
                Requirement.retested_at >= sdt, Requirement.retested_at <= edt, Requirement.retested_by_id.in_(team_ids)
            )
            q_closed = (
                self.db.query(func.count(func.distinct(BugStage5Record.bug_tracking_id)))
                .join(BugTracking, BugStage5Record.bug_tracking_id == BugTracking.id)
                .filter(
                    BugStage5Record.updated_at >= sdt,
                    BugStage5Record.updated_at <= edt,
                    BugStage5Record.user_id.in_(team_ids),
                    BugStage5Record.test_done.is_(True),
                    BugStage5Record.newly_found_bug_id.is_(None),
                )
            )
            q_fb_created = self.db.query(func.count(FeedbackRecord.id)).filter(
                FeedbackRecord.created_at >= sdt,
                FeedbackRecord.created_at <= edt,
                FeedbackRecord.creator_id.in_(team_ids),
            )
            overview = {
                "executed_requirements": filter_by_major_ids(q_exec, Requirement.major_version_id).scalar() or 0,
                "created_cases": filter_by_major_ids(q_case, Requirement.major_version_id).scalar() or 0,
                "created_bugs": filter_by_major_ids(q_bug, BugTracking.major_version_id).scalar() or 0,
                "retested_reqs": filter_by_major_ids(q_retest, Requirement.major_version_id).scalar() or 0,
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
                q_exec = (
                    self.db.query(func.count(func.distinct(TestExecution.requirement_id)))
                    .join(Requirement, TestExecution.requirement_id == Requirement.id)
                    .filter(TestExecution.executed_at >= day_s, TestExecution.executed_at <= day_e, TestExecution.executed_by_id.in_(team_ids))
                )
                q_case = (
                    self.db.query(func.count(TestCase.id))
                    .join(Requirement, TestCase.requirement_id == Requirement.id)
                    .filter(TestCase.created_at >= day_s, TestCase.created_at <= day_e, TestCase.creator_id.in_(team_ids))
                )
                q_bug = self.db.query(func.count(BugTracking.id)).filter(
                    BugTracking.created_at >= day_s, BugTracking.created_at <= day_e, BugTracking.created_by_id.in_(team_ids)
                )
                q_retested = self.db.query(func.count(Requirement.id)).filter(
                    Requirement.retested_at >= day_s, Requirement.retested_at <= day_e, Requirement.retested_by_id.in_(team_ids)
                )
                q_closed = (
                    self.db.query(func.count(func.distinct(BugStage5Record.bug_tracking_id)))
                    .join(BugTracking, BugStage5Record.bug_tracking_id == BugTracking.id)
                    .filter(
                        BugStage5Record.updated_at >= day_s,
                        BugStage5Record.updated_at <= day_e,
                        BugStage5Record.user_id.in_(team_ids),
                        BugStage5Record.test_done.is_(True),
                        BugStage5Record.newly_found_bug_id.is_(None),
                    )
                )
                q_fb_created = self.db.query(func.count(FeedbackRecord.id)).filter(
                    FeedbackRecord.created_at >= day_s,
                    FeedbackRecord.created_at <= day_e,
                    FeedbackRecord.creator_id.in_(team_ids),
                )
                day_exec = filter_by_major_ids(q_exec, Requirement.major_version_id).scalar() or 0
                day_case = filter_by_major_ids(q_case, Requirement.major_version_id).scalar() or 0
                day_bug = filter_by_major_ids(q_bug, BugTracking.major_version_id).scalar() or 0
                day_retested = filter_by_major_ids(q_retested, Requirement.major_version_id).scalar() or 0
                day_closed = filter_by_major_ids(q_closed, BugTracking.major_version_id).scalar() or 0
                day_fb_created = filter_by_major_ids(q_fb_created, FeedbackRecord.major_version_id).scalar() or 0
                day_fb_processed = processed_feedback_stats["by_day"].get(cur.isoformat(), 0)
            else:
                q_exec = (
                    self.db.query(func.count(func.distinct(TestExecution.requirement_id)))
                    .join(Requirement, TestExecution.requirement_id == Requirement.id)
                    .filter(TestExecution.executed_by_id == target_user_id, TestExecution.executed_at >= day_s, TestExecution.executed_at <= day_e)
                )
                q_case = (
                    self.db.query(func.count(TestCase.id))
                    .join(Requirement, TestCase.requirement_id == Requirement.id)
                    .filter(TestCase.creator_id == target_user_id, TestCase.created_at >= day_s, TestCase.created_at <= day_e)
                )
                q_bug = self.db.query(func.count(BugTracking.id)).filter(
                    BugTracking.created_by_id == target_user_id, BugTracking.created_at >= day_s, BugTracking.created_at <= day_e
                )
                q_retested = self.db.query(func.count(Requirement.id)).filter(
                    Requirement.retested_by_id == target_user_id, Requirement.retested_at >= day_s, Requirement.retested_at <= day_e
                )
                q_closed = (
                    self.db.query(func.count(func.distinct(BugStage5Record.bug_tracking_id)))
                    .join(BugTracking, BugStage5Record.bug_tracking_id == BugTracking.id)
                    .filter(
                        BugStage5Record.user_id == target_user_id,
                        BugStage5Record.updated_at >= day_s,
                        BugStage5Record.updated_at <= day_e,
                        BugStage5Record.test_done.is_(True),
                        BugStage5Record.newly_found_bug_id.is_(None),
                    )
                )
                q_fb_created = self.db.query(func.count(FeedbackRecord.id)).filter(
                    FeedbackRecord.created_at >= day_s,
                    FeedbackRecord.created_at <= day_e,
                    FeedbackRecord.creator_id == target_user_id,
                )
                day_exec = filter_by_major_ids(q_exec, Requirement.major_version_id).scalar() or 0
                day_case = filter_by_major_ids(q_case, Requirement.major_version_id).scalar() or 0
                day_bug = filter_by_major_ids(q_bug, BugTracking.major_version_id).scalar() or 0
                day_retested = filter_by_major_ids(q_retested, Requirement.major_version_id).scalar() or 0
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

        bug_dist_query = self.db.query(BugTracking.source_type, func.count(BugTracking.id)).filter(BugTracking.created_at >= sdt, BugTracking.created_at <= edt)
        bug_dist_query = filter_by_major_ids(bug_dist_query, BugTracking.major_version_id)
        if all_users_mode:
            bug_dist_query = bug_dist_query.filter(BugTracking.created_by_id.in_(team_ids))
        else:
            bug_dist_query = bug_dist_query.filter(BugTracking.created_by_id == target_user_id)
        bug_source_dist = [{"source_type": (k.value if hasattr(k, 'value') else str(k)), "count": v} for k, v in bug_dist_query.group_by(BugTracking.source_type).all()]

        result = {
            "overview": overview,
            "trend": trend,
            "bug_source_dist": bug_source_dist,
            "target_user_id": None if all_users_mode else target_user_id,
        }
        if all_users_mode:
            team = []
            all_users = self.db.query(User).filter(User.is_team_member.is_(True)).order_by(User.id.asc()).all()
            for u in all_users:
                m = metrics_for_user(u.id)
                team.append({"user_id": u.id, "username": u.shown_name, **m})
            result["team_comparison"] = team
        return result

    def advanced(self, start_date: date, end_date: date, major_version_id: int | None = None, software_id: int | None = None) -> dict:
        sdt = datetime.combine(start_date, datetime.min.time())
        edt = datetime.combine(end_date, datetime.max.time())
        req_bugs_query = self.db.query(Requirement.zentao_req_id, Requirement.title, func.count(BugTracking.id).label("bug_count")) \
            .join(BugTracking, BugTracking.requirement_id == Requirement.id) \
            .filter(BugTracking.created_at >= sdt, BugTracking.created_at <= edt)
        if major_version_id:
            req_bugs_query = req_bugs_query.filter(Requirement.major_version_id == major_version_id)
        elif software_id:
            req_bugs_query = req_bugs_query.join(Version, Requirement.major_version_id == Version.id).filter(Version.software_id == software_id)
        req_bugs = req_bugs_query.group_by(Requirement.id).order_by(func.count(BugTracking.id).desc()).limit(7).all()
        top_reqs = [{"req_id": r[0], "title": r[1], "count": r[2]} for r in req_bugs]
        bug_base_query = self.db.query(BugTracking).filter(BugTracking.created_at >= sdt, BugTracking.created_at <= edt)
        if major_version_id:
            bug_base_query = bug_base_query.filter(BugTracking.major_version_id == major_version_id)
        elif software_id:
            major_ids = [m.id for m in self.db.query(Version).filter(Version.version_type == VersionType.MAJOR, Version.software_id == software_id).all()]
            bug_base_query = bug_base_query.filter(BugTracking.major_version_id.in_(major_ids if major_ids else [-1]))
        retest_bugs = bug_base_query.filter(BugTracking.source_type == BugSourceType.RETEST).count() or 0
        normal_bugs = bug_base_query.filter(BugTracking.source_type.in_([BugSourceType.CASE, BugSourceType.MANUAL])).count() or 0
        total_bugs = bug_base_query.count() or 0
        # 方案A：以“已写入解决版本”作为开发处理完毕的判定标准。
        fixed_bugs = bug_base_query.filter(BugTracking.fixed_minor_version_id.isnot(None)).count() or 0
        closed_bugs = bug_base_query.filter(BugTracking.closed.is_(True)).count() or 0

        exec_query = self.db.query(TestExecution.result_status, func.count(TestExecution.id)).filter(TestExecution.executed_at >= sdt, TestExecution.executed_at <= edt)
        if major_version_id:
            exec_query = exec_query.join(Requirement, TestExecution.requirement_id == Requirement.id).filter(Requirement.major_version_id == major_version_id)
        elif software_id:
            exec_query = exec_query.join(Requirement, TestExecution.requirement_id == Requirement.id).join(Version, Requirement.major_version_id == Version.id).filter(Version.software_id == software_id)
        exec_results = exec_query.group_by(TestExecution.result_status).all()
        executions = [{"status": r[0], "count": r[1]} for r in exec_results]
        return {
            "top_reqs": top_reqs,
            "leakage": {"retest": retest_bugs, "normal": normal_bugs},
            "funnel": {"total": total_bugs, "fixed": fixed_bugs, "closed": closed_bugs},
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
                display_name = f"{parent_name}\n{mv.version_no}"
                result.append({"version_name": display_name, "bug_count": bug_count})
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
        now = datetime.utcnow()

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

        bug_query = self.db.query(BugTracking).filter(BugTracking.created_at >= sdt, BugTracking.created_at <= edt)
        if scoped_major_ids is not None:
            bug_query = bug_query.filter(BugTracking.major_version_id.in_(scoped_major_ids if scoped_major_ids else [-1]))
        bug_rows = bug_query.all()

        unassigned_bugs = []
        overdue_bugs = []
        stale_bugs = []
        assigned_no_progress = []
        for b in bug_rows:
            b_item = {
                "id": b.id,
                "bug_id": b.bug_id,
                "major_version_no": majors.get(b.major_version_id, "未知"),
                "status": "closed" if b.closed else "open",
                "dispatched_to_name": users.get(b.dispatched_to_id, "未指派"),
                "created_at": b.created_at.isoformat(),
                "updated_at": b.updated_at.isoformat() if b.updated_at else None,
                "age_days": _days_since(b.created_at),
                "stale_days": _days_since(b.updated_at),
            }
            if (not b.closed) and (b.dispatched_to_id is None):
                unassigned_bugs.append(b_item)
            if (not b.closed) and b.created_at <= bug_overdue_dt:
                overdue_bugs.append(b_item)
            if (not b.closed) and b.updated_at and b.updated_at <= stale_bug_dt:
                stale_bugs.append(b_item)
            if (not b.closed) and b.dispatched_to_id is not None and b.updated_at and b.updated_at <= bug_overdue_dt:
                assigned_no_progress.append(b_item)

        unassigned_bugs.sort(key=lambda x: x["age_days"], reverse=True)
        overdue_bugs.sort(key=lambda x: x["age_days"], reverse=True)
        stale_bugs.sort(key=lambda x: x["stale_days"], reverse=True)
        assigned_no_progress.sort(key=lambda x: x["stale_days"], reverse=True)

        bug_closed_days = []
        for b in bug_rows:
            if b.closed and b.updated_at:
                bug_closed_days.append(max(0, (b.updated_at - b.created_at).days))

        req_bug_top_query = self.db.query(
            Requirement.id,
            Requirement.zentao_req_id,
            Requirement.title,
            Requirement.major_version_id,
            Requirement.status,
            func.count(BugTracking.id).label("bug_count"),
        ).join(BugTracking, BugTracking.requirement_id == Requirement.id).filter(BugTracking.created_at >= sdt, BugTracking.created_at <= edt)
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
