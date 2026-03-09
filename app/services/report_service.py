from __future__ import annotations

from datetime import date, datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import BugSourceType, BugStage5Record, BugTracking, Requirement, TestCase, TestExecution, User, UserRole, Version, VersionType


class ReportService:
    def __init__(self, db: Session):
        self.db = db

    def summary(
        self,
        start_date: date,
        end_date: date,
        current_user: User,
        user_id: int | None = None,
        major_version_id: int | None = None,
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

        def metrics_for_user(uid: int) -> dict:
            executed_req_count = (
                self.db.query(func.count(func.distinct(TestExecution.requirement_id)))
                .join(Requirement, TestExecution.requirement_id == Requirement.id)
                .filter(TestExecution.executed_by_id == uid, TestExecution.executed_at >= sdt, TestExecution.executed_at <= edt)
                .filter(Requirement.major_version_id == major_version_id if major_version_id else True)
                .scalar()
                or 0
            )
            case_count = (
                self.db.query(func.count(TestCase.id))
                .join(Requirement, TestCase.requirement_id == Requirement.id)
                .filter(TestCase.creator_id == uid, TestCase.created_at >= sdt, TestCase.created_at <= edt)
                .filter(Requirement.major_version_id == major_version_id if major_version_id else True)
                .scalar()
                or 0
            )
            bug_count = (
                self.db.query(func.count(BugTracking.id))
                .filter(BugTracking.created_by_id == uid, BugTracking.created_at >= sdt, BugTracking.created_at <= edt)
                .filter(BugTracking.major_version_id == major_version_id if major_version_id else True)
                .scalar()
                or 0
            )
            retested_reqs = (
                self.db.query(func.count(Requirement.id))
                .filter(Requirement.retested_by_id == uid, Requirement.retested_at >= sdt, Requirement.retested_at <= edt)
                .filter(Requirement.major_version_id == major_version_id if major_version_id else True)
                .scalar()
                or 0
            )
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
                .filter(BugTracking.major_version_id == major_version_id if major_version_id else True)
                .scalar()
                or 0
            )
            return {
                "executed_requirements": executed_req_count,
                "created_cases": case_count,
                "created_bugs": bug_count,
                "retested_reqs": retested_reqs,
                "closed_bugs": closed_bugs,
            }

        if all_users_mode:
            overview = {
                "executed_requirements": (
                    self.db.query(func.count(func.distinct(TestExecution.requirement_id)))
                    .join(Requirement, TestExecution.requirement_id == Requirement.id)
                    .filter(TestExecution.executed_at >= sdt, TestExecution.executed_at <= edt, TestExecution.executed_by_id.in_(team_ids))
                    .filter(Requirement.major_version_id == major_version_id if major_version_id else True)
                    .scalar()
                    or 0
                ),
                "created_cases": (
                    self.db.query(func.count(TestCase.id))
                    .join(Requirement, TestCase.requirement_id == Requirement.id)
                    .filter(TestCase.created_at >= sdt, TestCase.created_at <= edt, TestCase.creator_id.in_(team_ids))
                    .filter(Requirement.major_version_id == major_version_id if major_version_id else True)
                    .scalar()
                    or 0
                ),
                "created_bugs": (
                    self.db.query(func.count(BugTracking.id))
                    .filter(BugTracking.created_at >= sdt, BugTracking.created_at <= edt, BugTracking.created_by_id.in_(team_ids))
                    .filter(BugTracking.major_version_id == major_version_id if major_version_id else True)
                    .scalar()
                    or 0
                ),
                "retested_reqs": (
                    self.db.query(func.count(Requirement.id))
                    .filter(Requirement.retested_at >= sdt, Requirement.retested_at <= edt, Requirement.retested_by_id.in_(team_ids))
                    .filter(Requirement.major_version_id == major_version_id if major_version_id else True)
                    .scalar()
                    or 0
                ),
                "closed_bugs": (
                    self.db.query(func.count(func.distinct(BugStage5Record.bug_tracking_id)))
                    .join(BugTracking, BugStage5Record.bug_tracking_id == BugTracking.id)
                    .filter(
                        BugStage5Record.updated_at >= sdt,
                        BugStage5Record.updated_at <= edt,
                        BugStage5Record.user_id.in_(team_ids),
                        BugStage5Record.test_done.is_(True),
                        BugStage5Record.newly_found_bug_id.is_(None),
                    )
                    .filter(BugTracking.major_version_id == major_version_id if major_version_id else True)
                    .scalar()
                    or 0
                ),
            }
        else:
            overview = metrics_for_user(target_user_id)

        trend = []
        cur = start_date
        while cur <= end_date:
            day_s = datetime.combine(cur, datetime.min.time())
            day_e = datetime.combine(cur, datetime.max.time())
            if all_users_mode:
                day_exec = (
                    self.db.query(func.count(func.distinct(TestExecution.requirement_id)))
                    .join(Requirement, TestExecution.requirement_id == Requirement.id)
                    .filter(TestExecution.executed_at >= day_s, TestExecution.executed_at <= day_e, TestExecution.executed_by_id.in_(team_ids))
                    .filter(Requirement.major_version_id == major_version_id if major_version_id else True)
                    .scalar()
                    or 0
                )
                day_case = (
                    self.db.query(func.count(TestCase.id))
                    .join(Requirement, TestCase.requirement_id == Requirement.id)
                    .filter(TestCase.created_at >= day_s, TestCase.created_at <= day_e, TestCase.creator_id.in_(team_ids))
                    .filter(Requirement.major_version_id == major_version_id if major_version_id else True)
                    .scalar()
                    or 0
                )
                day_bug = (
                    self.db.query(func.count(BugTracking.id))
                    .filter(BugTracking.created_at >= day_s, BugTracking.created_at <= day_e, BugTracking.created_by_id.in_(team_ids))
                    .filter(BugTracking.major_version_id == major_version_id if major_version_id else True)
                    .scalar()
                    or 0
                )
                day_retested = (
                    self.db.query(func.count(Requirement.id))
                    .filter(Requirement.retested_at >= day_s, Requirement.retested_at <= day_e, Requirement.retested_by_id.in_(team_ids))
                    .filter(Requirement.major_version_id == major_version_id if major_version_id else True)
                    .scalar()
                    or 0
                )
                day_closed = (
                    self.db.query(func.count(func.distinct(BugStage5Record.bug_tracking_id)))
                    .join(BugTracking, BugStage5Record.bug_tracking_id == BugTracking.id)
                    .filter(
                        BugStage5Record.updated_at >= day_s,
                        BugStage5Record.updated_at <= day_e,
                        BugStage5Record.user_id.in_(team_ids),
                        BugStage5Record.test_done.is_(True),
                        BugStage5Record.newly_found_bug_id.is_(None),
                    )
                    .filter(BugTracking.major_version_id == major_version_id if major_version_id else True)
                    .scalar()
                    or 0
                )
            else:
                day_exec = (
                    self.db.query(func.count(func.distinct(TestExecution.requirement_id)))
                    .join(Requirement, TestExecution.requirement_id == Requirement.id)
                    .filter(TestExecution.executed_by_id == target_user_id, TestExecution.executed_at >= day_s, TestExecution.executed_at <= day_e)
                    .filter(Requirement.major_version_id == major_version_id if major_version_id else True)
                    .scalar()
                    or 0
                )
                day_case = (
                    self.db.query(func.count(TestCase.id))
                    .join(Requirement, TestCase.requirement_id == Requirement.id)
                    .filter(TestCase.creator_id == target_user_id, TestCase.created_at >= day_s, TestCase.created_at <= day_e)
                    .filter(Requirement.major_version_id == major_version_id if major_version_id else True)
                    .scalar()
                    or 0
                )
                day_bug = (
                    self.db.query(func.count(BugTracking.id))
                    .filter(BugTracking.created_by_id == target_user_id, BugTracking.created_at >= day_s, BugTracking.created_at <= day_e)
                    .filter(BugTracking.major_version_id == major_version_id if major_version_id else True)
                    .scalar()
                    or 0
                )
                day_retested = (
                    self.db.query(func.count(Requirement.id))
                    .filter(Requirement.retested_by_id == target_user_id, Requirement.retested_at >= day_s, Requirement.retested_at <= day_e)
                    .filter(Requirement.major_version_id == major_version_id if major_version_id else True)
                    .scalar()
                    or 0
                )
                day_closed = (
                    self.db.query(func.count(func.distinct(BugStage5Record.bug_tracking_id)))
                    .join(BugTracking, BugStage5Record.bug_tracking_id == BugTracking.id)
                    .filter(
                        BugStage5Record.user_id == target_user_id,
                        BugStage5Record.updated_at >= day_s,
                        BugStage5Record.updated_at <= day_e,
                        BugStage5Record.test_done.is_(True),
                        BugStage5Record.newly_found_bug_id.is_(None),
                    )
                    .filter(BugTracking.major_version_id == major_version_id if major_version_id else True)
                    .scalar()
                    or 0
                )
            trend.append({
                "date": cur.isoformat(),
                "executed_requirements": day_exec,
                "created_cases": day_case,
                "created_bugs": day_bug,
                "retested_reqs": day_retested,
                "closed_bugs": day_closed,
            })
            cur += timedelta(days=1)

        bug_dist_query = self.db.query(BugTracking.source_type, func.count(BugTracking.id)).filter(BugTracking.created_at >= sdt, BugTracking.created_at <= edt)
        if major_version_id:
            bug_dist_query = bug_dist_query.filter(BugTracking.major_version_id == major_version_id)
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
                team.append({"user_id": u.id, "username": u.username, **m})
            result["team_comparison"] = team
        return result

    def advanced(self, start_date: date, end_date: date, major_version_id: int | None = None) -> dict:
        sdt = datetime.combine(start_date, datetime.min.time())
        edt = datetime.combine(end_date, datetime.max.time())
        req_bugs_query = self.db.query(Requirement.zentao_req_id, Requirement.title, func.count(BugTracking.id).label("bug_count")) \
            .join(BugTracking, BugTracking.requirement_id == Requirement.id) \
            .filter(BugTracking.created_at >= sdt, BugTracking.created_at <= edt)
        if major_version_id:
            req_bugs_query = req_bugs_query.filter(Requirement.major_version_id == major_version_id)
        req_bugs = req_bugs_query.group_by(Requirement.id).order_by(func.count(BugTracking.id).desc()).limit(7).all()
        top_reqs = [{"req_id": r[0], "title": r[1], "count": r[2]} for r in req_bugs]
        bug_base_query = self.db.query(BugTracking).filter(BugTracking.created_at >= sdt, BugTracking.created_at <= edt)
        if major_version_id:
            bug_base_query = bug_base_query.filter(BugTracking.major_version_id == major_version_id)
        retest_bugs = bug_base_query.filter(BugTracking.source_type == BugSourceType.RETEST).count() or 0
        normal_bugs = bug_base_query.filter(BugTracking.source_type.in_([BugSourceType.CASE, BugSourceType.MANUAL])).count() or 0
        total_bugs = bug_base_query.count() or 0
        # 方案A：以“已写入解决版本”作为开发处理完毕的判定标准。
        fixed_bugs = bug_base_query.filter(BugTracking.fixed_minor_version_id.isnot(None)).count() or 0
        closed_bugs = bug_base_query.filter(BugTracking.closed.is_(True)).count() or 0

        exec_query = self.db.query(TestExecution.result_status, func.count(TestExecution.id)).filter(TestExecution.executed_at >= sdt, TestExecution.executed_at <= edt)
        if major_version_id:
            exec_query = exec_query.join(Requirement, TestExecution.requirement_id == Requirement.id).filter(Requirement.major_version_id == major_version_id)
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
