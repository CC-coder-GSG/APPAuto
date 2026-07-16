from __future__ import annotations

from datetime import datetime

from fastapi import HTTPException
from sqlalchemy.orm import Session, joinedload

from app.models import BugSourceType, BugTracking, Requirement, RequirementRetestRecord, User, Version, VersionType
from app.services.audit_service import audit
from app.services.sse_service import sse_publish
from app.services.workbench_link_service import WorkbenchLinkService
from app.utils.time_utils import local_now


class RetestService:
    def __init__(self, db: Session):
        self.db = db

    def get_workbench(
        self,
        current_user: User,
        *,
        major_version_id: int | None = None,
        mode: str = "version",
        software_id: int | None = None,
    ) -> list[dict]:
        q = (
            self.db.query(Requirement)
            .options(joinedload(Requirement.owner), joinedload(Requirement.test_cases), joinedload(Requirement.retester), joinedload(Requirement.major_version))
            .filter(
                Requirement.test_completed.is_(True),
                Requirement.owner_id.isnot(None),
                Requirement.owner_id != current_user.id,
            )
        )
        if mode == "version":
            if not major_version_id:
                return []
            q = q.filter(Requirement.major_version_id == major_version_id)
        elif mode == "all_pending":
            q = q.filter(Requirement.retest_completed.is_(False))
            if software_id:
                q = q.join(Version, Requirement.major_version_id == Version.id).filter(Version.software_id == software_id)
        else:
            raise HTTPException(status_code=400, detail="mode only supports version/all_pending")

        reqs = q.order_by(Requirement.id.asc()).all()

        minors = {v.id: v.version_no for v in self.db.query(Version).filter(Version.version_type == VersionType.MINOR).all()}
        req_ids = [r.id for r in reqs]
        case_ids = [c.id for r in reqs for c in r.test_cases]

        case_bug_rows = self.db.query(BugTracking).filter(
            BugTracking.requirement_id.in_(req_ids),
            BugTracking.source_type == BugSourceType.CASE,
            BugTracking.source_ref.in_([str(x) for x in case_ids] if case_ids else ["-1"]),
        ).all() if req_ids else []
        free_bug_rows = self.db.query(BugTracking).filter(
            BugTracking.requirement_id.in_(req_ids),
            BugTracking.source_type.in_([BugSourceType.MANUAL, BugSourceType.REQUIREMENT]),
        ).all() if req_ids else []
        retest_bug_rows = self.db.query(BugTracking).filter(
            BugTracking.requirement_id.in_(req_ids),
            BugTracking.source_type == BugSourceType.RETEST,
        ).all() if req_ids else []

        case_bug_map: dict[str, list[dict]] = {}
        for bug in case_bug_rows:
            case_bug_map.setdefault(bug.source_ref or "", []).append(
                {
                    "id": bug.id,
                    "bug_id": bug.bug_id,
                    "zentao_bug_url": bug.zentao_bug_url,
                    "zentao_bug_title": bug.zentao_bug_title,
                    "found_minor_version_no": minors.get(bug.found_minor_version_id, "未知"),
                    "is_retest_failed": bug.is_retest_failed,
                }
            )

        free_bug_map: dict[int, list[dict]] = {}
        for bug in free_bug_rows:
            free_bug_map.setdefault(bug.requirement_id or -1, []).append(
                {
                    "id": bug.id,
                    "bug_id": bug.bug_id,
                    "zentao_bug_url": bug.zentao_bug_url,
                    "zentao_bug_title": bug.zentao_bug_title,
                    "found_minor_version_no": minors.get(bug.found_minor_version_id, "未知"),
                    "is_retest_failed": bug.is_retest_failed,
                }
            )

        retest_bug_map: dict[int, list[dict]] = {}
        for bug in retest_bug_rows:
            retest_bug_map.setdefault(bug.requirement_id or -1, []).append(
                {
                    "id": bug.id,
                    "bug_id": bug.bug_id,
                    "zentao_bug_url": bug.zentao_bug_url,
                    "zentao_bug_title": bug.zentao_bug_title,
                    "found_minor_version_no": minors.get(bug.found_minor_version_id, "未知"),
                    "is_retest_failed": bug.is_retest_failed,
                    "closed": bool(bug.closed),
                }
            )

        return [
            {
                "id": r.id,
                "zentao_req_id": r.zentao_req_id,
                "title": r.title,
                "major_version_id": r.major_version_id,
                "major_version_name": r.major_version.version_no if r.major_version else "未知",
                "owner": r.owner.shown_name if r.owner else None,
                "retest_completed": r.retest_completed,
                "retest_passed": r.retest_passed,
                "retest_minor_version_id": r.retest_minor_version_id,
                "retested_by": r.retester.shown_name if r.retester else None,
                "test_cases": [{"id": c.id, "zentao_case_id": c.zentao_case_id, "zentao_case_url": c.zentao_case_url, "bugs": case_bug_map.get(str(c.id), [])} for c in r.test_cases],
                "free_bugs": free_bug_map.get(r.id, []),
                "retest_bugs": retest_bug_map.get(r.id, []),
            }
            for r in reqs
        ]

    def compute_retest_problems(self, req: Requirement) -> list[dict]:
        """
        该需求当前的「复测问题 Bug」清单（2026-07 改版后的唯一口径）：

        1. 复测激活：复测人在复测工作台真实激活了禅道 Bug（retest_activated，
           按 retest_activated_req_id 归属需求）；
        2. 测后归集：需求测试完成后禅道新增并归集到该需求的 Bug，且禅道
           提出人 ≠ 原测试人（原测试人自己补录的不算复测问题）。

        每项形如 {bug_db_id, kind: 'activated'|'collected', actor_name, dismissed}；
        勾选「取消复测结论」（retest_dismissed）的问题保留在清单里但
        dismissed=True，不参与结论判定。
        """
        problems: list[dict] = []
        seen: set[int] = set()

        activated_rows = (
            self.db.query(BugTracking)
            .options(joinedload(BugTracking.retest_activated_by))
            .filter(
                BugTracking.retest_activated.is_(True),
                BugTracking.retest_activated_req_id == req.id,
            )
            .all()
        )
        for bug in activated_rows:
            problems.append(
                {
                    "bug_db_id": bug.id,
                    "kind": "activated",
                    "actor_name": bug.retest_activated_by.shown_name if bug.retest_activated_by else None,
                    "dismissed": bool(bug.retest_dismissed),
                }
            )
            seen.add(bug.id)

        # 测后归集问题：复用工作台的归集证据口径（测试完成时间之后禅道新增）
        owner_account = ""
        if req.owner and req.owner.zentao_account:
            owner_account = req.owner.zentao_account.strip().lower()
        link = WorkbenchLinkService(self.db)
        minors = link.minor_version_name_map()
        evidence = link.build_retest_evidence([req], minors).get(req.id, [])
        for bug in evidence:
            if bug["id"] in seen:
                continue
            opener = (bug.get("zentao_opened_by_account") or "").strip().lower()
            # 提出人可确认是原测试人本人 → 不算复测问题；提出人未知时保守计入
            #（误报有「取消复测结论」勾选兜底）
            if owner_account and opener and opener == owner_account:
                continue
            problems.append(
                {
                    "bug_db_id": bug["id"],
                    "kind": "collected",
                    "actor_name": bug.get("zentao_opened_by_name"),
                    "dismissed": bool(bug.get("retest_dismissed")),
                }
            )
            seen.add(bug["id"])
        return problems

    def compute_conclusion(self, req: Requirement, problems: list[dict] | None = None) -> str:
        """
        需求级复测结论（三态）：
        - failed ：存在未标记误报的复测问题（优先级最高，覆盖任何通过记录）
        - passed ：无有效问题，且有人点过通过 / 或问题全部被标记误报
        - pending：默认未处理
        """
        if problems is None:
            problems = self.compute_retest_problems(req)
        active = [p for p in problems if not p["dismissed"]]
        if active:
            return "failed"
        records = (
            self.db.query(RequirementRetestRecord)
            .filter(RequirementRetestRecord.requirement_id == req.id)
            .order_by(RequirementRetestRecord.updated_at.desc(), RequirementRetestRecord.id.desc())
            .all()
        )
        if records:
            # 历史打回记录（passed=False）保留原义
            return "passed" if records[0].passed else "failed"
        if problems:
            return "passed"  # 出现过问题但全部被标记误报 → 视为通过
        return "pending"

    def _recompute_aggregate(self, req: Requirement) -> None:
        """
        刷新 Requirement 行上的共享聚合字段（供需求状态机 / 报表 / 看板沿用）。

        改版后的优先级：有效复测问题 > 复测记录 > 全误报视为通过 > 未处理。
        「先通过后激活/归集到 Bug」的自动降级由此天然成立——聚合每次都按
        当前问题清单重算，通过记录仍保留但结论被问题覆盖。
        """
        problems = self.compute_retest_problems(req)
        active = [p for p in problems if not p["dismissed"]]
        records = (
            self.db.query(RequirementRetestRecord)
            .filter(RequirementRetestRecord.requirement_id == req.id)
            .order_by(RequirementRetestRecord.updated_at.desc(), RequirementRetestRecord.id.desc())
            .all()
        )
        latest = records[0] if records else None
        if active:
            # 复测未通过：任何人的问题都覆盖通过结论
            activated_by = next(
                (
                    bug.retest_activated_by_id
                    for bug in self.db.query(BugTracking)
                    .filter(BugTracking.id.in_([p["bug_db_id"] for p in active if p["kind"] == "activated"]))
                    .order_by(BugTracking.retest_activated_at.desc())
                    .all()
                    if bug.retest_activated_by_id
                ),
                None,
            ) if any(p["kind"] == "activated" for p in active) else None
            req.retest_completed = True
            req.retest_passed = False
            req.retest_minor_version_id = latest.minor_version_id if latest else None
            req.retested_by_id = activated_by or (latest.user_id if latest else None)
            req.retested_at = local_now()
        elif latest:
            req.retest_completed = True
            req.retest_passed = bool(latest.passed)
            req.retest_minor_version_id = latest.minor_version_id
            req.retested_by_id = latest.user_id
            req.retested_at = latest.updated_at or local_now()
        elif problems:
            # 出现过问题但全部被标记误报 → 系统判定通过
            req.retest_completed = True
            req.retest_passed = True
            req.retest_minor_version_id = None
            req.retested_by_id = None
            req.retested_at = local_now()
        else:
            req.retest_completed = False
            req.retest_passed = None
            req.retest_minor_version_id = None
            req.retested_by_id = None
            req.retested_at = None

    def submit_retest(
        self,
        requirement_id: int,
        *,
        retest_completed: bool,
        retest_passed: bool | None,
        retest_minor_version_id: int | None,
        current_user: User,
    ) -> dict:
        req = (
            self.db.query(Requirement)
            .options(joinedload(Requirement.owner))
            .filter(Requirement.id == requirement_id)
            .first()
        )
        if not req:
            raise HTTPException(status_code=404, detail="Requirement not found")
        if req.owner_id == current_user.id:
            raise HTTPException(status_code=403, detail="Self-tested requirement cannot be cross-retested by self")

        # 2026-07 改版：打回按钮已下线，复测问题只通过「激活 Bug / 测后归集」记录
        if retest_completed and retest_passed is False:
            raise HTTPException(
                status_code=400,
                detail="「打回」已下线：复测发现问题请直接激活对应禅道 Bug（或等待系统归集新 Bug），结论会自动判定为复测未通过。",
            )

        if retest_completed and retest_passed is True:
            problems = self.compute_retest_problems(req)
            if any(not p["dismissed"] for p in problems):
                raise HTTPException(
                    status_code=400,
                    detail="无法标记通过：该需求存在复测激活 / 新归集的问题 Bug。若确认属误报，请先勾选对应 Bug 的「取消复测结论」。",
                )

        # 写入"当前用户"的复测记录（per-user），不再直接共享给所有人
        record = (
            self.db.query(RequirementRetestRecord)
            .filter(
                RequirementRetestRecord.requirement_id == req.id,
                RequirementRetestRecord.user_id == current_user.id,
            )
            .first()
        )
        if retest_completed:
            if not record:
                record = RequirementRetestRecord(requirement_id=req.id, user_id=current_user.id)
                self.db.add(record)
            record.passed = bool(retest_passed)
            record.minor_version_id = retest_minor_version_id
            record.updated_at = local_now()
        elif record:
            # 撤销当前用户的复测结论
            self.db.delete(record)
        self.db.flush()

        # 刷新共享聚合字段，供需求状态机 / 报表 / 看板沿用
        self._recompute_aggregate(req)
        self.db.commit()
        audit(self.db, action="retest.submit", target_type="requirement", actor_id=current_user.id, target_id=str(req.id), detail=f"passed={retest_passed if retest_completed else None},minor={retest_minor_version_id if retest_completed else None}")
        sse_publish(
            "retest_requirement_status_changed",
            {
                "requirement_id": req.id,
                "retest_completed": retest_completed,
                "retest_passed": retest_passed if retest_completed else None,
                "retested_by_id": current_user.id if retest_completed else None,
                "retest_conclusion": self.compute_conclusion(req),
            },
            channels=["global"],
        )
        return {"message": "Retest status updated"}

    def _get_bug_and_requirement(self, bug_id: int, requirement_id: int) -> tuple[BugTracking, Requirement]:
        bug = self.db.query(BugTracking).filter(BugTracking.id == bug_id).first()
        if not bug:
            raise HTTPException(status_code=404, detail="Bug not found")
        req = (
            self.db.query(Requirement)
            .options(joinedload(Requirement.owner))
            .filter(Requirement.id == requirement_id)
            .first()
        )
        if not req:
            raise HTTPException(status_code=404, detail="Requirement not found")
        return bug, req

    def mark_bug_activated(self, bug_id: int, requirement_id: int, current_user: User) -> dict:
        """
        复测激活留痕：前端先走禅道真实激活链路（/zentao/bugs/{id}/active，
        带回读确认），成功后调本接口记录「复测激活」——该 Bug 即成为复测
        问题，需求结论自动转为复测未通过。
        """
        bug, req = self._get_bug_and_requirement(bug_id, requirement_id)
        bug.retest_activated = True
        bug.retest_activated_by_id = current_user.id
        bug.retest_activated_at = local_now()
        bug.retest_activated_req_id = req.id
        bug.retest_dismissed = False
        bug.retest_dismissed_by_id = None
        bug.closed = False  # 禅道已激活，本地闭环状态同步复位
        self.db.flush()  # 重算前先落盘：问题清单靠查询判定
        self._recompute_aggregate(req)
        self.db.commit()
        audit(
            self.db, action="retest.bug_activated", target_type="bug",
            actor_id=current_user.id, target_id=str(bug.id),
            detail=f"requirement_id={req.id},zentao_bug_id={bug.zentao_bug_id or ''}",
        )
        conclusion = self.compute_conclusion(req)
        sse_publish(
            "retest_requirement_status_changed",
            {"requirement_id": req.id, "retest_conclusion": conclusion, "retest_passed": req.retest_passed},
            channels=["global"],
        )
        return {"message": "已记录复测激活，该需求结论转为复测未通过", "retest_conclusion": conclusion}

    def toggle_bug_dismissed(self, bug_id: int, requirement_id: int, dismissed: bool, current_user: User) -> dict:
        """勾选/取消「取消复测结论」（误报标记）：全部问题被标记误报时结论回到复测通过。"""
        bug, req = self._get_bug_and_requirement(bug_id, requirement_id)
        bug.retest_dismissed = bool(dismissed)
        bug.retest_dismissed_by_id = current_user.id if dismissed else None
        self.db.flush()  # 重算前先落盘：问题清单靠查询判定
        self._recompute_aggregate(req)
        self.db.commit()
        audit(
            self.db, action="retest.bug_dismissed", target_type="bug",
            actor_id=current_user.id, target_id=str(bug.id),
            detail=f"requirement_id={req.id},dismissed={bool(dismissed)}",
        )
        conclusion = self.compute_conclusion(req)
        sse_publish(
            "retest_requirement_status_changed",
            {"requirement_id": req.id, "retest_conclusion": conclusion, "retest_passed": req.retest_passed},
            channels=["global"],
        )
        return {
            "message": "已标记误报，不再计入复测问题" if dismissed else "已恢复为复测问题",
            "retest_conclusion": conclusion,
        }

    def build_retest_push_message(self, major_version_id: int, current_user: User) -> tuple[str, int]:
        """
        复测结果通报（2026-07 改版）：按**需求级结论**汇总该大版本全部已测试
        完成的需求，与工作台展示同口径——未处理的不进通报；未通过的列出
        问题构成（复测激活/测后归集 + 责任人）；通过的列出复测人。
        """
        reqs = (
            self.db.query(Requirement)
            .options(joinedload(Requirement.owner))
            .filter(
                Requirement.major_version_id == major_version_id,
                Requirement.test_completed.is_(True),
            )
            .order_by(Requirement.id.asc())
            .all()
        )
        req_ids = [r.id for r in reqs]
        records_by_req: dict[int, list[RequirementRetestRecord]] = {}
        if req_ids:
            for rec in (
                self.db.query(RequirementRetestRecord)
                .options(joinedload(RequirementRetestRecord.user), joinedload(RequirementRetestRecord.minor_version))
                .filter(RequirementRetestRecord.requirement_id.in_(req_ids))
                .order_by(RequirementRetestRecord.updated_at.asc(), RequirementRetestRecord.id.asc())
                .all()
            ):
                records_by_req.setdefault(rec.requirement_id, []).append(rec)

        msg_lines = [f"### 复测结果专项通报 (发起人: @{current_user.shown_name})"]
        count = 0
        for req in reqs:
            problems = self.compute_retest_problems(req)
            conclusion = self.compute_conclusion(req, problems)
            if conclusion == "pending":
                continue
            count += 1
            owner_name = req.owner.shown_name if req.owner else "未知"
            recs = records_by_req.get(req.id, [])
            if conclusion == "passed":
                retesters = "/".join(dict.fromkeys(f"@{r.user.shown_name}" for r in recs if r.user)) or "系统判定(问题全部误报)"
                minor_ver = next((r.minor_version.version_no for r in reversed(recs) if r.minor_version), "未知")
                msg_lines.append(
                    f"> ✅ **[通过]** {req.zentao_req_id} (原测试: @{owner_name} | 复测: {retesters} | 验证发包: {minor_ver})"
                )
            else:
                active = [p for p in problems if not p["dismissed"]]
                parts = []
                activated = [p for p in active if p["kind"] == "activated"]
                collected = [p for p in active if p["kind"] == "collected"]
                if activated:
                    names = "/".join(dict.fromkeys(f"@{p['actor_name']}" for p in activated if p["actor_name"])) or "未知"
                    parts.append(f"复测激活 {len(activated)} 个Bug({names})")
                if collected:
                    names = "/".join(dict.fromkeys(f"@{p['actor_name']}" for p in collected if p["actor_name"])) or "未知"
                    parts.append(f"新归集 {len(collected)} 个Bug({names})")
                detail = "、".join(parts) or "存在历史打回记录"
                msg_lines.append(
                    f"> ❌ **[未通过]** <font color=\"warning\">{req.zentao_req_id}</font> (原测试: @{owner_name}) - *{detail}*"
                )
        if not count:
            raise HTTPException(status_code=400, detail="该大版本暂无复测结论可推送（需求均为未处理状态）")

        return "\n".join(msg_lines), count
