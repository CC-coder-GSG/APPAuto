from __future__ import annotations

from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session, joinedload

from app.models import Requirement, TestCaseReview, User
from app.services.sse_service import sse_publish
from app.services.workbench_link_service import _case_key
from app.utils.time_utils import local_now


class CaseReviewService:
    """用例审查（审查工作台）：按人独立的审查记录 + 修改完成归档。"""

    def __init__(self, db: Session):
        self.db = db

    # ── 写操作 ────────────────────────────────────────────────────────────

    def submit_review(
        self,
        *,
        zentao_case_id: str,
        requirement_id: int | None,
        status: str,
        opinion: str | None,
        current_user: User,
    ) -> dict[str, Any]:
        status = (status or "").strip().lower()
        if status not in ("passed", "failed"):
            raise HTTPException(status_code=400, detail="status 仅支持 passed / failed")
        key = _case_key(zentao_case_id)
        if not key:
            raise HTTPException(status_code=400, detail="用例编号无效")
        opinion = (opinion or "").strip()
        if status == "failed" and not opinion:
            raise HTTPException(status_code=400, detail="审查不通过必须填写审查意见")

        # 同一审查人对同一用例只保留一条 active：旧记录归档
        self.db.query(TestCaseReview).filter(
            TestCaseReview.case_key == key,
            TestCaseReview.reviewer_id == current_user.id,
            TestCaseReview.active.is_(True),
        ).update({"active": False}, synchronize_session=False)

        row = TestCaseReview(
            case_key=key,
            requirement_id=requirement_id,
            reviewer_id=current_user.id,
            status=status,
            opinion=opinion or None,
            active=True,
        )
        self.db.add(row)
        self.db.commit()

        sse_publish("case_review_changed", {
            "case_key": key,
            "requirement_id": requirement_id,
            "status": status,
            "reviewer": current_user.shown_name,
        })
        return {"ok": True, "id": row.id, "case_key": key, "status": status}

    def submit_fix(
        self,
        *,
        zentao_case_id: str,
        requirement_id: int | None,
        content: str,
        current_user: User,
    ) -> dict[str, Any]:
        key = _case_key(zentao_case_id)
        if not key:
            raise HTTPException(status_code=400, detail="用例编号无效")
        content = (content or "").strip()
        if not content:
            raise HTTPException(status_code=400, detail="修改完成必须填写修改内容")

        has_failed = (
            self.db.query(TestCaseReview.id)
            .filter(
                TestCaseReview.case_key == key,
                TestCaseReview.active.is_(True),
                TestCaseReview.status == "failed",
            )
            .first()
        )
        if not has_failed:
            raise HTTPException(status_code=400, detail="该用例当前没有未通过的审查，无需修改完成")

        # 归档全部 active 审查 → 用例回到未审查状态；追加 fixed 历史记录
        self.db.query(TestCaseReview).filter(
            TestCaseReview.case_key == key,
            TestCaseReview.active.is_(True),
        ).update({"active": False}, synchronize_session=False)
        row = TestCaseReview(
            case_key=key,
            requirement_id=requirement_id,
            reviewer_id=current_user.id,
            status="fixed",
            opinion=content,
            active=False,
        )
        self.db.add(row)
        self.db.commit()

        sse_publish("case_review_changed", {
            "case_key": key,
            "requirement_id": requirement_id,
            "status": "fixed",
            "reviewer": current_user.shown_name,
        })
        return {"ok": True, "id": row.id, "case_key": key, "status": "fixed"}

    # ── 读/附着 ───────────────────────────────────────────────────────────

    def reviews_by_case_keys(self, keys: set[str]) -> dict[str, dict[str, Any]]:
        """{case_key: {"active": [...], "history": [...]}}，各按时间倒序。"""
        if not keys:
            return {}
        rows = (
            self.db.query(TestCaseReview)
            .options(joinedload(TestCaseReview.reviewer))
            .filter(TestCaseReview.case_key.in_(list(keys)))
            .order_by(TestCaseReview.created_at.desc(), TestCaseReview.id.desc())
            .all()
        )
        result: dict[str, dict[str, Any]] = {}
        for r in rows:
            slot = result.setdefault(r.case_key, {"active": [], "history": []})
            item = {
                "id": r.id,
                "reviewer_id": r.reviewer_id,
                "reviewer_name": r.reviewer.shown_name if r.reviewer else "未知",
                "status": r.status,
                "opinion": r.opinion,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            slot["history"].append(item)
            if r.active:
                slot["active"].append(item)
        return result

    def attach_reviews_to_case_view(self, case_view_map: dict[int, list[dict[str, Any]]]) -> None:
        """给工作台用例视图就地附上 reviews/review_history（需求/审查工作台共用）。"""
        keys = {
            _case_key(c.get("zentao_case_id"))
            for cases in case_view_map.values()
            for c in cases
        }
        keys.discard("")
        review_map = self.reviews_by_case_keys(keys)
        for cases in case_view_map.values():
            for c in cases:
                key = _case_key(c.get("zentao_case_id"))
                slot = review_map.get(key) or {"active": [], "history": []}
                c["case_key"] = key
                c["reviews"] = slot["active"]
                c["review_history"] = slot["history"]

    def wecom_context(self, *, zentao_case_id: str, requirement_id: int | None, current_user: User) -> dict[str, str]:
        """审查不通过的企微播报文案上下文（需求标题/负责人）。"""
        req_title = ""
        owner_name = "未分配"
        if requirement_id:
            req = (
                self.db.query(Requirement)
                .options(joinedload(Requirement.owner))
                .filter(Requirement.id == requirement_id)
                .first()
            )
            if req:
                req_title = f"{req.zentao_req_id or ''} {req.title or ''}".strip()
                owner_name = req.owner.shown_name if req.owner else "未分配"
        return {
            "reviewer": current_user.shown_name,
            "owner": owner_name,
            "requirement": req_title or "（未关联需求）",
            "case_id": str(zentao_case_id),
            "time": local_now().strftime("%Y-%m-%d %H:%M"),
        }
