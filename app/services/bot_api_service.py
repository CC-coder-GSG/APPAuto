"""
企业微信 AI 机器人 / 第三方只读+轻量写 API 的服务层。

设计原则：
- 只做「快速、无副作用」的聚合读，不触发禅道在线同步等重操作（与工作台/大盘的
  重型 overview 区分开），保证机器人调用低延迟、可频繁拉取。
- 写操作（创建反馈、触发企微推送）复用既有 Service，以「机器人执行身份」（管理员）落库。
"""
from __future__ import annotations

import difflib
import re
from datetime import date, datetime

from fastapi import HTTPException
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import (
    BugTracking,
    FeedbackRecord,
    Requirement,
    SoftwareProduct,
    User,
    Version,
)
from app.models.enums import FeedbackStatus, RequirementStatus, UserRole, VersionType
from app.services.overall_test_service import _bug_effective_status
from app.services.zentao_matcher import MAJOR_V_RE, VERSION_PREFIX_RE
from app.utils.time_utils import local_now

# ----------------------------------------------------------------- 版本解析辅助
# 用户口语化输入（"V4.0.3.1"、"40315"、"40300103 这个版本"）→ 规范大版本号 / 构建号。
# 复用 zentao_matcher 的正则，约定与 parse_job_name_to_major_version_no 一致：
# 纯数字串前 3 位 = a.b.c，其余为尾段（4030→V4.0.3.0，40315→V4.0.3.15）。
_BUILD_IN_PARENS_RE = re.compile(r"\((\d{5,})\)")
_DIGIT_RUN_RE = re.compile(r"\d+")


def _digits_to_major_no(d: str) -> str | None:
    if len(d) < 4:  # 少于 4 位无法确定尾段
        return None
    return f"V{d[0]}.{d[1]}.{d[2]}.{d[3:]}"


def _candidate_major_nos(q: str) -> set[str]:
    """从自由文本里推断出所有可能的大版本号（大写、带 V 前缀）。"""
    norm = q.replace("（", "(").replace("）", ")")
    out: set[str] = set()
    m = MAJOR_V_RE.search(norm)
    if m:
        out.add(m.group(0).upper())
    v = VERSION_PREFIX_RE.search(norm)
    if v:
        out.add(("V" + v.group(0)).upper())
    for d in _DIGIT_RUN_RE.findall(norm):
        mv = _digits_to_major_no(d)
        if mv:
            out.add(mv.upper())
    return out


def _candidate_build_codes(q: str) -> set[str]:
    """从自由文本里抽出可能的构建号：括号内 5+ 位优先，其次任意 5+ 位连续数字。"""
    norm = q.replace("（", "(").replace("）", ")")
    codes = set(_BUILD_IN_PARENS_RE.findall(norm))
    codes.update(re.findall(r"\d{5,}", norm))
    return codes


# --------------------------------------------------------- Bug / 需求 搜索辅助
# 形如 "b#29875"、"bug 29875"、"需求 5604"、"#5604"、"5604" 都视为「按 id 精确查」。
_ID_QUERY_RE = re.compile(r"^\s*(?:b#|r#|bug|缺陷|req|story|需求|故事)?\s*#?\s*(\d+)\s*$", re.IGNORECASE)


def _extract_id_query(q: str) -> str | None:
    m = _ID_QUERY_RE.match(q or "")
    return m.group(1) if m else None


def _fuzzy_score(query: str, text: str) -> float:
    """标题模糊匹配打分（0~1）。子串命中给高分，否则用编辑距离相似度。"""
    q = (query or "").strip().lower()
    t = (text or "").strip().lower()
    if not q or not t:
        return 0.0
    if q in t:
        return 0.9 + 0.1 * min(len(q) / len(t), 1.0)
    return difflib.SequenceMatcher(None, q, t).ratio()


def _fmt_dt(dt: datetime | None) -> str | None:
    return dt.strftime("%Y-%m-%d %H:%M") if dt else None


class BotApiService:
    def __init__(self, db: Session):
        self.db = db

    # ------------------------------------------------------------------ helpers
    def resolve_bot_user(self) -> User:
        """解析机器人写操作的执行身份：优先配置的用户名，否则任意管理员。"""
        username = (settings.bot_api_username or "").strip()
        if username:
            user = self.db.query(User).filter(User.username == username).first()
            if not user:
                raise HTTPException(status_code=500, detail=f"配置的机器人执行账号不存在：{username}")
            return user
        admin = self.db.query(User).filter(User.role == UserRole.ADMIN).order_by(User.id.asc()).first()
        if not admin:
            raise HTTPException(status_code=500, detail="系统没有可用的管理员账号作为机器人执行身份")
        return admin

    def _scoped_major_ids(self, software_id: int | None, major_version_id: int | None) -> list[int] | None:
        """把 software_id / major_version_id 归一为一组「大版本 id」过滤条件。None=不限。"""
        if major_version_id:
            return [int(major_version_id)]
        if software_id:
            rows = (
                self.db.query(Version.id)
                .filter(Version.version_type == VersionType.MAJOR, Version.software_id == int(software_id))
                .all()
            )
            return [r[0] for r in rows]
        return None

    # -------------------------------------------------------------------- reads
    def ping(self) -> dict:
        return {
            "ok": True,
            "app": settings.app_name,
            "version": settings.app_version,
            "server_time": local_now().isoformat(timespec="seconds"),
        }

    def list_softwares(self) -> list[dict]:
        rows = self.db.query(SoftwareProduct).order_by(SoftwareProduct.id.asc()).all()
        return [{"id": s.id, "name": s.name} for s in rows]

    def list_versions(self, software_id: int | None = None) -> list[dict]:
        """返回大版本及其子版本（供机器人/人工查到版本 id）。"""
        q = self.db.query(Version)
        if software_id:
            q = q.filter(Version.software_id == int(software_id))
        versions = q.all()
        majors = [v for v in versions if v.version_type == VersionType.MAJOR]
        minors_by_parent: dict[int, list[Version]] = {}
        for v in versions:
            if v.version_type == VersionType.MINOR and v.parent_id:
                minors_by_parent.setdefault(int(v.parent_id), []).append(v)
        result = []
        for m in sorted(majors, key=lambda x: x.id):
            kids = sorted(minors_by_parent.get(m.id, []), key=lambda x: x.id)
            result.append(
                {
                    "id": m.id,
                    "version_no": m.version_no,
                    "software_id": m.software_id,
                    "minor_versions": [{"id": k.id, "version_no": k.version_no} for k in kids],
                }
            )
        return result

    # ----------------------------------------------------------- 版本解析 / 聚合
    def _match_versions(self, q: str) -> list[dict]:
        """把自由文本解析成一组排序后的大版本候选（最多 5 个）。"""
        raw = (q or "").strip()
        if not raw:
            return []
        majors = self.db.query(Version).filter(Version.version_type == VersionType.MAJOR).all()
        minors = self.db.query(Version).filter(Version.version_type == VersionType.MINOR).all()
        major_by_no = {(m.version_no or "").upper(): m for m in majors}
        major_by_id = {m.id: m for m in majors}
        best: dict[int, dict] = {}

        def consider(major: Version | None, score: int, match: str, minor: Version | None = None) -> None:
            if major is None:
                return
            cur = best.get(major.id)
            if cur and cur["score"] >= score:
                return
            best[major.id] = {
                "major_version_id": major.id,
                "major_version_no": major.version_no,
                "software_id": major.software_id,
                "minor_version_id": minor.id if minor else None,
                "minor_version_no": minor.version_no if minor else None,
                "match": match,
                "score": score,
            }

        # 1) 精确匹配大版本号（V4.0.3.1 / 4.0.3.1 / 40315 推断而来）
        for token in _candidate_major_nos(raw):
            consider(major_by_no.get(token), 95, "exact_major")

        # 2) 构建号匹配子版本 → 回溯其父大版本（顺带带回精确子版本 id）
        codes = _candidate_build_codes(raw)
        if codes:
            for mn in minors:
                no = mn.version_no or ""
                parent = major_by_id.get(mn.parent_id) if mn.parent_id else None
                if not parent:
                    continue
                for c in codes:
                    if f"({c})" in no:
                        consider(parent, 92, "build_code", mn)
                    elif c in no:
                        consider(parent, 78, "build_code", mn)

        # 3) 兜底：大版本号子串包含
        if not best:
            ql = raw.upper().lstrip("V")
            if ql:
                for no, m in major_by_no.items():
                    if ql in no or no.lstrip("V") in ql:
                        consider(m, 60, "substring")

        ranked = sorted(best.values(), key=lambda x: (-x["score"], x["major_version_id"]))
        return ranked[:5]

    def resolve_version(self, q: str) -> dict:
        """把口语化版本解析为带 id 的候选大版本。唯一高置信→resolved=true。"""
        matches = self._match_versions(q)
        resolved = False
        if matches:
            top = matches[0]["score"]
            second = matches[1]["score"] if len(matches) > 1 else 0
            resolved = top >= 90 and (len(matches) == 1 or top - second >= 10)
        return {
            "query": (q or "").strip(),
            "resolved": resolved,
            "ambiguous": len(matches) > 1 and not resolved,
            "match_count": len(matches),
            "matches": matches,
        }

    def version_status(self, q: str) -> dict:
        """一问到底：解析版本后返回该大版本的进度+Bug+反馈紧凑汇总（无明细数组，体积受控）。"""
        res = self.resolve_version(q)
        if not res["resolved"]:
            return {
                "query": res["query"],
                "resolved": False,
                "ambiguous": res["ambiguous"],
                "candidates": [
                    {"major_version_id": m["major_version_id"], "major_version_no": m["major_version_no"]}
                    for m in res["matches"]
                ],
                "message": (
                    "未找到匹配版本，请确认版本号" if not res["matches"] else "匹配到多个版本，请指明具体大版本"
                ),
            }
        top = res["matches"][0]
        mid = top["major_version_id"]
        prog = self.version_progress(mid)
        bugs = self.bug_summary(major_version_id=mid)
        fb = self.feedback_summary(major_version_id=mid)
        return {
            "query": res["query"],
            "resolved": True,
            "major_version_id": mid,
            "major_version_no": top["major_version_no"],
            "software_id": top["software_id"],
            "matched_minor_version_no": top["minor_version_no"],
            "progress": prog,
            "bugs": {
                "total": bugs["total"],
                "open": bugs["open"],
                "by_status": bugs["by_status"],
                "retest_failed": bugs["retest_failed"],
            },
            "feedback": {
                "total": fb["total"],
                "pending": fb["pending"],
                "by_status": fb["by_status"],
            },
        }

    # --------------------------------------------------------------- Bug 查询
    def _bug_brief(self, b: BugTracking, score: int, match: str) -> dict:
        return {
            "zentao_bug_id": b.zentao_bug_id,
            "bug_id": b.bug_id,
            "title": b.zentao_bug_title or "",
            "status": _bug_effective_status(b),
            "assigned_to": b.zentao_assigned_to_name or "",
            "score": score,
            "match": match,
        }

    def search_bugs(self, q: str, limit: int = 5) -> dict:
        """按禅道 Bug id（纯数字→精确）或标题（→模糊）搜索，返回最可能的若干候选。"""
        raw = (q or "").strip()
        if not raw:
            raise HTTPException(status_code=422, detail="q 不能为空")
        limit = max(1, min(int(limit or 5), 20))
        id_digits = _extract_id_query(raw)
        matches: list[dict] = []

        if id_digits:
            rows = (
                self.db.query(BugTracking)
                .filter(BugTracking.zentao_deleted.is_(False))
                .filter(or_(BugTracking.zentao_bug_id == id_digits, BugTracking.bug_id == id_digits))
                .all()
            )
            matches = [self._bug_brief(b, 100, "id_exact") for b in rows]

        if not matches:
            # 标题模糊：仅取 id+标题两列做评分，再对 Top-N 回查完整行
            cols = (
                self.db.query(BugTracking.id, BugTracking.zentao_bug_title)
                .filter(BugTracking.zentao_deleted.is_(False))
                .all()
            )
            scored = [(bid, _fuzzy_score(raw, title or "")) for bid, title in cols]
            scored = sorted([s for s in scored if s[1] >= 0.3], key=lambda x: -x[1])[:limit]
            if scored:
                id2score = dict(scored)
                rows = self.db.query(BugTracking).filter(BugTracking.id.in_(list(id2score))).all()
                rows.sort(key=lambda b: -id2score.get(b.id, 0))
                matches = [self._bug_brief(b, round(id2score[b.id] * 100), "title_fuzzy") for b in rows]

        return {"query": raw, "match_count": len(matches[:limit]), "matches": matches[:limit]}

    def bug_detail(self, zentao_bug_id: str) -> dict:
        """已知禅道 Bug id 的精细信息：状态/指派/标题/受影响版本/关联需求/关闭信息等。"""
        zid = str(zentao_bug_id or "").strip()
        if not zid:
            raise HTTPException(status_code=422, detail="zentao_bug_id 不能为空")
        b = self.db.query(BugTracking).filter(BugTracking.zentao_bug_id == zid).first()
        if not b:
            raise HTTPException(status_code=404, detail="未找到该禅道 Bug")

        def _vno(vid: int | None) -> str | None:
            if not vid:
                return None
            v = self.db.query(Version).filter(Version.id == vid).first()
            return v.version_no if v else None

        req = b.requirement
        return {
            "zentao_bug_id": b.zentao_bug_id,
            "bug_id": b.bug_id,
            "title": b.zentao_bug_title or "",
            "status": _bug_effective_status(b),
            "live_status": b.zentao_live_status or "",
            "closed": bool(b.closed),
            "is_retest_failed": bool(b.is_retest_failed),
            "assigned_to": b.zentao_assigned_to_name or "",
            "opened_by": b.zentao_opened_by_name or b.zentao_creator_name or "",
            "opened_at": _fmt_dt(b.zentao_opened_at),
            "source_type": b.source_type.value if b.source_type else None,
            "zentao_source_type": b.zentao_source_type or "",
            "affected_version": b.zentao_affected_version or "",
            "major_version_no": _vno(b.major_version_id),
            "found_minor_version_no": _vno(b.found_minor_version_id),
            "fixed_minor_version_no": _vno(b.fixed_minor_version_id),
            "product_name": b.zentao_product_name or "",
            "execution_name": b.zentao_execution_name or "",
            "linked_requirement": {
                "zentao_requirement_id": b.zentao_requirement_id or (str(req.zentao_req_id) if req else None),
                "title": (b.zentao_requirement_name or (req.title if req else "")) or "",
            },
            "linked_case_label": b.zentao_linked_case_label or "",
            "resolution": b.resolution or "",
            "closed_by": b.zentao_closed_by_name or "",
            "close_date": _fmt_dt(b.zentao_close_date),
            "close_comment": b.zentao_close_comment or "",
            "zentao_bug_url": b.zentao_bug_url or "",
            "last_zentao_synced_at": _fmt_dt(b.last_zentao_synced_at),
            "note": "完整复现步骤/正文请见禅道原始链接 zentao_bug_url",
        }

    # --------------------------------------------------------------- 需求 查询
    def _req_brief(self, r: Requirement, score: int, match: str) -> dict:
        major = r.major_version
        return {
            "id": r.id,
            "zentao_req_id": r.zentao_req_id,
            "title": r.title or "",
            "major_version_no": major.version_no if major else None,
            "status": r.status.value if r.status else None,
            "owner": r.owner.shown_name if r.owner else None,
            "score": score,
            "match": match,
        }

    def search_requirements(self, q: str, limit: int = 5) -> dict:
        """按禅道需求 id（纯数字→精确）或标题（→模糊）搜索，返回最可能的若干候选。"""
        raw = (q or "").strip()
        if not raw:
            raise HTTPException(status_code=422, detail="q 不能为空")
        limit = max(1, min(int(limit or 5), 20))
        id_digits = _extract_id_query(raw)
        matches: list[dict] = []

        if id_digits:
            conds = [Requirement.zentao_req_id == id_digits, Requirement.zentao_req_id == f"r#{id_digits}"]
            if id_digits.isdigit():
                conds.append(Requirement.zentao_story_id == int(id_digits))
            rows = self.db.query(Requirement).filter(or_(*conds)).all()
            matches = [self._req_brief(r, 100, "id_exact") for r in rows]

        if not matches:
            cols = self.db.query(Requirement.id, Requirement.title).all()
            scored = [(rid, _fuzzy_score(raw, title or "")) for rid, title in cols]
            scored = sorted([s for s in scored if s[1] >= 0.3], key=lambda x: -x[1])[:limit]
            if scored:
                id2score = dict(scored)
                rows = self.db.query(Requirement).filter(Requirement.id.in_(list(id2score))).all()
                rows.sort(key=lambda r: -id2score.get(r.id, 0))
                matches = [self._req_brief(r, round(id2score[r.id] * 100), "title_fuzzy") for r in rows]

        return {"query": raw, "match_count": len(matches[:limit]), "matches": matches[:limit]}

    def requirement_detail(self, zentao_req_id: str, major_version_id: int | None = None) -> dict:
        """已知禅道需求 id 的精细信息。同一 id 可能存在于多个大版本，未指定大版本且命中多条时回候选。"""
        rid = str(zentao_req_id or "").strip()
        if not rid:
            raise HTTPException(status_code=422, detail="zentao_req_id 不能为空")
        q = self.db.query(Requirement).filter(Requirement.zentao_req_id == rid)
        if major_version_id:
            q = q.filter(Requirement.major_version_id == int(major_version_id))
        rows = q.all()
        if not rows:
            raise HTTPException(status_code=404, detail="未找到该禅道需求")
        if len(rows) > 1:
            return {
                "zentao_req_id": rid,
                "resolved": False,
                "ambiguous": True,
                "candidates": [self._req_brief(r, 100, "id_exact") for r in rows],
                "message": "该需求 id 存在于多个大版本，请补充 major_version_id",
            }

        r = rows[0]
        major = r.major_version
        bugs = [b for b in r.bug_tracks if not b.zentao_deleted]
        bug_open = sum(1 for b in bugs if _bug_effective_status(b) != "closed")
        return {
            "zentao_req_id": r.zentao_req_id,
            "resolved": True,
            "id": r.id,
            "title": r.title or "",
            "status": r.status.value if r.status else None,
            "major_version_id": r.major_version_id,
            "major_version_no": major.version_no if major else None,
            "owner": r.owner.shown_name if r.owner else None,
            "zentao_story_id": r.zentao_story_id,
            "plan_title": r.zentao_plan_title_cache or "",
            "case_completed": bool(r.case_completed),
            "test_completed": bool(r.test_completed),
            "test_completed_at": _fmt_dt(r.test_completed_at),
            "retest_completed": bool(r.retest_completed),
            "retest_passed": r.retest_passed,
            "retested_by": r.retester.shown_name if r.retester else None,
            "test_notes": r.test_notes or "",
            "case_count": len(r.test_cases),
            "bug_count": len(bugs),
            "bug_open": bug_open,
            "linked_bug_ids": [b.zentao_bug_id for b in bugs if b.zentao_bug_id][:10],
            "note": "完整需求正文（spec）请见禅道原始页面",
        }

    def version_progress(self, major_version_id: int) -> dict:
        """某大版本的需求测试 / 用例编写 / 复测进度概览。"""
        major = (
            self.db.query(Version)
            .filter(Version.id == int(major_version_id), Version.version_type == VersionType.MAJOR)
            .first()
        )
        if not major:
            raise HTTPException(status_code=404, detail="大版本不存在")
        reqs = self.db.query(Requirement).filter(Requirement.major_version_id == major.id).all()
        total = len(reqs)
        case_done = sum(1 for r in reqs if r.case_completed)
        test_done = sum(1 for r in reqs if r.test_completed)
        retest_pending = sum(1 for r in reqs if r.test_completed and not r.retest_completed)
        retest_done = sum(1 for r in reqs if r.retest_completed)

        def pct(n: int) -> float:
            return round(n * 100.0 / total, 1) if total else 0.0

        return {
            "major_version_id": major.id,
            "major_version_no": major.version_no,
            "software_id": major.software_id,
            "requirement_total": total,
            "case_completed": case_done,
            "case_completed_rate": pct(case_done),
            "test_completed": test_done,
            "test_completed_rate": pct(test_done),
            "test_pending": total - test_done,
            "retest_pending": retest_pending,
            "retest_done": retest_done,
        }

    def bug_summary(self, software_id: int | None = None, major_version_id: int | None = None) -> dict:
        """Bug 数量统计：按有效状态（活跃/已解决/已关闭/本地）与来源类型分组。"""
        major_ids = self._scoped_major_ids(software_id, major_version_id)
        q = self.db.query(BugTracking).filter(BugTracking.zentao_deleted.is_(False))
        if major_ids is not None:
            if not major_ids:
                return self._empty_bug_summary(software_id, major_version_id)
            q = q.filter(BugTracking.major_version_id.in_(major_ids))
        bugs = q.all()
        by_status = {"active": 0, "resolved": 0, "closed": 0, "local": 0}
        by_source: dict[str, int] = {}
        retest_failed = 0
        for b in bugs:
            by_status[_bug_effective_status(b)] = by_status.get(_bug_effective_status(b), 0) + 1
            src = b.source_type.value if b.source_type else "unknown"
            by_source[src] = by_source.get(src, 0) + 1
            if b.is_retest_failed:
                retest_failed += 1
        return {
            "software_id": software_id,
            "major_version_id": major_version_id,
            "total": len(bugs),
            "by_status": by_status,
            "by_source": by_source,
            "retest_failed": retest_failed,
            "open": by_status["active"] + by_status["resolved"] + by_status["local"],
        }

    @staticmethod
    def _empty_bug_summary(software_id, major_version_id) -> dict:
        return {
            "software_id": software_id,
            "major_version_id": major_version_id,
            "total": 0,
            "by_status": {"active": 0, "resolved": 0, "closed": 0, "local": 0},
            "by_source": {},
            "retest_failed": 0,
            "open": 0,
        }

    def feedback_summary(self, software_id: int | None = None, major_version_id: int | None = None) -> dict:
        """用户反馈按状态统计。"""
        major_ids = self._scoped_major_ids(software_id, major_version_id)
        q = self.db.query(FeedbackRecord.status, func.count(FeedbackRecord.id))
        if major_ids is not None:
            if not major_ids:
                major_ids = [-1]
            q = q.filter(FeedbackRecord.major_version_id.in_(major_ids))
        rows = q.group_by(FeedbackRecord.status).all()
        by_status = {s.value: 0 for s in FeedbackStatus}
        for status, count in rows:
            key = status.value if hasattr(status, "value") else str(status)
            by_status[key] = int(count)
        total = sum(by_status.values())
        return {
            "software_id": software_id,
            "major_version_id": major_version_id,
            "total": total,
            "by_status": by_status,
            "pending": by_status.get("pending", 0) + by_status.get("processing", 0),
        }

    def report_summary(
        self,
        start_date: date,
        end_date: date,
        software_id: int | None = None,
        major_version_id: int | None = None,
    ) -> dict:
        """团队整体工作量报表（复用 ReportService 的全员视图）。"""
        from app.services.report_service import ReportService

        bot_user = self.resolve_bot_user()
        data = ReportService(self.db).summary(
            start_date=start_date,
            end_date=end_date,
            current_user=bot_user,
            user_id=0,  # 0 = 全员（需要管理员身份）
            major_version_id=major_version_id,
            software_id=software_id,
        )
        overview = data.get("overview", data)
        return {
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "software_id": software_id,
            "major_version_id": major_version_id,
            "overview": overview,
        }

    # ------------------------------------------------------------------- writes
    def create_feedback(
        self,
        *,
        major_version_id: int,
        minor_version_id: int,
        summary: str,
        feedback_no: str | None = None,
    ) -> dict:
        from app.services.feedback_service import FeedbackService

        bot_user = self.resolve_bot_user()
        return FeedbackService(self.db).create_feedback(
            creator=bot_user,
            feedback_no_num=feedback_no,
            major_version_id=int(major_version_id),
            minor_version_id=int(minor_version_id),
            summary=summary,
        )

    async def push_version_progress(self, major_version_id: int) -> dict:
        """触发企业微信群推送：该大版本的用例编写进度。"""
        from app.services.push_service import PushService

        bot_user = self.resolve_bot_user()
        return await PushService(self.db).push_case_progress(int(major_version_id), bot_user)
