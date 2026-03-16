from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any, Optional

from fastapi import HTTPException
from sqlalchemy import desc, or_
from sqlalchemy.orm import Session, joinedload

from app.models import AuditLog, BugTracking, FeedbackRecord, FieldTestRecord, Requirement, TestCase, User, Version


ACTION_META: dict[str, dict[str, Any]] = {
    "bug.create": {"module": "bug", "action_text": "创建 Bug", "level": "important", "important": True},
    "bug.update": {"module": "bug", "action_text": "更新 Bug", "level": "info", "important": False},
    "bug.delete": {"module": "bug", "action_text": "删除 Bug", "level": "critical", "important": True},
    "bug.dispatch": {"module": "bug", "action_text": "特派 Bug", "level": "important", "important": True},
    "bug.toggle_retest_fail": {"module": "bug", "action_text": "切换复测未修好标记", "level": "important", "important": True},
    "stage5.submit_result": {"module": "bug", "action_text": "提交整体测试结果", "level": "important", "important": True},
    "stage5.add_issue": {"module": "bug", "action_text": "新增整体测试问题", "level": "important", "important": True},
    "requirement.create": {"module": "requirement", "action_text": "创建需求", "level": "important", "important": True},
    "requirement.batch_create": {"module": "requirement", "action_text": "批量导入需求", "level": "important", "important": True},
    "requirement.link_major": {"module": "requirement", "action_text": "关联版本需求", "level": "important", "important": True},
    "requirement.update": {"module": "requirement", "action_text": "更新需求", "level": "info", "important": False},
    "requirement.delete": {"module": "requirement", "action_text": "删除需求", "level": "critical", "important": True},
    "requirement.assign": {"module": "requirement", "action_text": "变更负责人", "level": "important", "important": True},
    "requirement.patch_status": {"module": "requirement", "action_text": "更新需求状态", "level": "important", "important": True},
    "requirement.update_test_completion": {"module": "requirement", "action_text": "更新测试完成状态", "level": "important", "important": True},
    "requirement.update_cases": {"module": "requirement", "action_text": "批量更新用例", "level": "important", "important": True},
    "requirement.add_case": {"module": "requirement", "action_text": "新增测试用例", "level": "info", "important": False},
    "requirement.update_case": {"module": "requirement", "action_text": "更新测试用例", "level": "info", "important": False},
    "requirement.delete_case": {"module": "requirement", "action_text": "删除测试用例", "level": "important", "important": True},
    "requirement.upsert_test_execution": {"module": "requirement", "action_text": "提交测试执行记录", "level": "important", "important": True},
    "requirement.test_notes.update": {"module": "requirement", "action_text": "更新测试要点", "level": "important", "important": True},
    "requirement.update_test_notes": {"module": "requirement", "action_text": "更新测试要点", "level": "important", "important": True},
    "feedback.create": {"module": "feedback", "action_text": "创建反馈", "level": "important", "important": True},
    "feedback.assign": {"module": "feedback", "action_text": "指派反馈处理人", "level": "important", "important": True},
    "feedback.handle": {"module": "feedback", "action_text": "提交反馈处理结果", "level": "important", "important": True},
    "feedback.status": {"module": "feedback", "action_text": "更新反馈状态", "level": "important", "important": True},
    "feedback.upload_attachment": {"module": "feedback", "action_text": "上传反馈附件", "level": "info", "important": False},
    "feedback.delete_attachment": {"module": "feedback", "action_text": "删除反馈附件", "level": "info", "important": False},
    "feedback.link_bug": {"module": "feedback", "action_text": "关联已有 Bug", "level": "important", "important": True},
    "feedback.create_bug_link": {"module": "feedback", "action_text": "新建并关联 Bug", "level": "important", "important": True},
    "feedback.unlink_bug": {"module": "feedback", "action_text": "解除 Bug 关联", "level": "info", "important": False},
    "field_test.create": {"module": "field_test", "action_text": "创建外业测试记录", "level": "important", "important": True},
    "field_test.update": {"module": "field_test", "action_text": "更新外业测试记录", "level": "important", "important": True},
    "field_test.add_bug": {"module": "field_test", "action_text": "新增外业测试 Bug", "level": "important", "important": True},
    "field_test.link_bug": {"module": "field_test", "action_text": "关联外业测试 Bug", "level": "important", "important": True},
    "field_test.unlink_bug": {"module": "field_test", "action_text": "解除外业测试 Bug", "level": "info", "important": False},
    "retest.submit": {"module": "retest", "action_text": "提交复测结果", "level": "important", "important": True},
    "auth.login": {"module": "admin", "action_text": "用户登录", "level": "info", "important": False},
    "auth.change_password": {"module": "admin", "action_text": "修改密码", "level": "important", "important": True},
    "user.reset_password": {"module": "admin", "action_text": "重置用户密码", "level": "critical", "important": True},
    "user.create": {"module": "admin", "action_text": "创建用户", "level": "important", "important": True},
    "user.delete": {"module": "admin", "action_text": "删除用户", "level": "critical", "important": True},
    "user.update_role": {"module": "admin", "action_text": "修改用户角色", "level": "important", "important": True},
    "user.update_team_status": {"module": "admin", "action_text": "修改成员归属", "level": "important", "important": True},
}

SUMMARY_BUCKETS: dict[str, tuple[str, str]] = {
    "bug.create": ("new_bugs", "新增 Bug"),
    "bug.update": ("updated_bugs", "更新 Bug"),
    "feedback.status": ("closed_feedbacks", "关闭反馈"),
    "feedback.handle": ("handled_feedbacks", "处理反馈"),
    "requirement.update": ("updated_requirements", "更新需求"),
    "requirement.assign": ("assign_requirements", "变更负责人"),
    "requirement.test_notes.update": ("updated_test_notes", "更新测试要点"),
    "requirement.update_test_notes": ("updated_test_notes", "更新测试要点"),
    "retest.submit": ("retest_submits", "提交复测"),
    "field_test.create": ("new_field_tests", "新增外业记录"),
}

STATUS_TEXT = {
    "pending": "待处理",
    "assigned": "已分配",
    "case_done": "用例完成",
    "testing": "测试中",
    "test_done": "测试完成",
    "retest_pending": "待复测",
    "retest_done": "复测完成",
    "processing": "处理中",
    "resolved": "已处理",
    "closed": "已关闭",
}

RESULT_TEXT = {
    "passed": "通过",
    "failed": "失败",
    "blocked": "阻塞",
    "partial": "部分完成",
    "untested": "未测试",
    "fixed": "修复通过",
    "false_alarm": "误报",
    "rejected": "拒绝修复",
}

FIELD_TEST_RESULT_TEXT = {
    "passed": "测试通过",
    "failed": "测试未通过",
}


class ActivityService:
    def __init__(self, db: Session):
        self.db = db

    def _bool_text(self, raw_value: str | None) -> str:
        text = (raw_value or "").strip()
        if text == "True":
            return "是"
        if text == "False":
            return "否"
        return text or "-"

    def _status_text(self, raw_value: str | None) -> str:
        text = (raw_value or "").strip()
        return STATUS_TEXT.get(text, RESULT_TEXT.get(text, FIELD_TEST_RESULT_TEXT.get(text, text or "-")))

    def _parse_int(self, raw_value: str | int | None) -> int | None:
        try:
            if raw_value is None or raw_value == "":
                return None
            return int(raw_value)
        except Exception:
            return None

    def _user_name(self, raw_id: str | int | None) -> str:
        user_id = self._parse_int(raw_id)
        if user_id is None or user_id < 0:
            return "未分配"
        user = self.db.query(User).filter(User.id == user_id).first()
        return user.shown_name if user else f"用户#{user_id}"

    def _version_name(self, raw_id: str | int | None) -> str:
        version_id = self._parse_int(raw_id)
        if version_id is None or version_id < 0:
            return "未设置"
        version = self.db.query(Version).filter(Version.id == version_id).first()
        return version.version_no if version else f"版本#{version_id}"

    def _requirement_label(self, raw_id: str | int | None) -> str:
        requirement_id = self._parse_int(raw_id)
        if requirement_id is None or requirement_id < 0:
            return "未指定"
        req = self.db.query(Requirement).filter(Requirement.id == requirement_id).first()
        return f"{req.zentao_req_id} {req.title}" if req else f"需求#{requirement_id}"

    def _bug_no(self, raw_id: str | int | None) -> str:
        bug_id = self._parse_int(raw_id)
        if bug_id is None or bug_id < 0:
            return "未指定"
        bug = self.db.query(BugTracking).filter(BugTracking.id == bug_id).first()
        return bug.bug_id if bug else f"Bug#{bug_id}"

    def _get_action_meta(self, action: str) -> dict[str, Any]:
        if action in ACTION_META:
            return ACTION_META[action]
        prefix = (action or "").split(".", 1)[0]
        module = "admin" if prefix in {"auth", "user"} else ("bug" if prefix == "stage5" else prefix or "system")
        return {"module": module, "action_text": action or "未知动作", "level": "info", "important": False}

    def _load_context(self, logs: list[AuditLog]) -> dict[str, dict[int, Any]]:
        ids_by_type: dict[str, set[int]] = defaultdict(set)
        for log in logs:
            if log.target_type and log.target_id is not None:
                numeric_id = self._parse_int(log.target_id)
                if numeric_id is not None:
                    ids_by_type[log.target_type].add(numeric_id)

        ctx: dict[str, dict[int, Any]] = {}
        if ids_by_type.get("bug"):
            rows = self.db.query(BugTracking).options(joinedload(BugTracking.requirement)).filter(BugTracking.id.in_(ids_by_type["bug"])).all()
            ctx["bug"] = {row.id: row for row in rows}
        if ids_by_type.get("requirement"):
            rows = self.db.query(Requirement).options(joinedload(Requirement.owner)).filter(Requirement.id.in_(ids_by_type["requirement"])).all()
            ctx["requirement"] = {row.id: row for row in rows}
        if ids_by_type.get("feedback"):
            rows = self.db.query(FeedbackRecord).filter(FeedbackRecord.id.in_(ids_by_type["feedback"])).all()
            ctx["feedback"] = {row.id: row for row in rows}
        if ids_by_type.get("field_test"):
            rows = (
                self.db.query(FieldTestRecord)
                .options(joinedload(FieldTestRecord.requirement), joinedload(FieldTestRecord.tester))
                .filter(FieldTestRecord.id.in_(ids_by_type["field_test"]))
                .all()
            )
            ctx["field_test"] = {row.id: row for row in rows}
        if ids_by_type.get("test_case"):
            rows = self.db.query(TestCase).options(joinedload(TestCase.requirement)).filter(TestCase.id.in_(ids_by_type["test_case"])).all()
            ctx["test_case"] = {row.id: row for row in rows}
        if ids_by_type.get("user"):
            rows = self.db.query(User).filter(User.id.in_(ids_by_type["user"])).all()
            ctx["user"] = {row.id: row for row in rows}
        if ids_by_type.get("version"):
            rows = self.db.query(Version).filter(Version.id.in_(ids_by_type["version"])).all()
            ctx["version"] = {row.id: row for row in rows}
        return ctx

    def _resolve_target_info(self, log: AuditLog, ctx: dict[str, dict[int, Any]]) -> tuple[Optional[int], str | None, str | None]:
        numeric_target_id = self._parse_int(log.target_id)
        if numeric_target_id is None:
            return None, log.target_id, None

        target_type = log.target_type or ""
        item = ctx.get(target_type, {}).get(numeric_target_id)
        if target_type == "bug" and item:
            return numeric_target_id, item.bug_id, item.requirement.title if item.requirement else None
        if target_type == "requirement" and item:
            return numeric_target_id, item.zentao_req_id, item.title
        if target_type == "feedback" and item:
            return numeric_target_id, item.feedback_no or f"f#{item.id}", item.summary
        if target_type == "field_test" and item:
            title = item.requirement.title if item.requirement else (item.test_content or "外业测试记录")
            return numeric_target_id, f"外业#{item.id}", title
        if target_type == "test_case" and item:
            req_title = f"{item.requirement.zentao_req_id} {item.requirement.title}" if item.requirement else "测试用例"
            return numeric_target_id, item.zentao_case_id, req_title
        if target_type == "user" and item:
            return numeric_target_id, item.shown_name, item.username
        if target_type == "version" and item:
            return numeric_target_id, item.version_no, getattr(item.version_type, "value", "")
        return numeric_target_id, log.target_id, None

    def _render_detail(self, action: str, detail: str | None) -> str | None:
        raw = (detail or "").strip()
        if not raw:
            return None

        if action == "bug.update" and "->" in raw:
            old_value, new_value = raw.split("->", 1)
            return f"Bug 编号：{old_value} -> {new_value}"
        if action == "bug.dispatch" and raw.startswith("dispatch_to="):
            return f"特派给：{self._user_name(raw.split('=', 1)[1])}"
        if action == "bug.toggle_retest_fail":
            return "已标记为复测未修好" if raw == "True" else "已取消复测未修好标记"
        if action == "requirement.assign" and "->" in raw:
            old_value, new_value = raw.split("->", 1)
            return f"负责人变更：{self._user_name(old_value)} -> {self._user_name(new_value)}"
        if action == "requirement.patch_status":
            parts = dict(part.split("=", 1) for part in raw.split(",") if "=" in part)
            return (
                f"用例完成：{self._bool_text(parts.get('case'))}，"
                f"测试完成：{self._bool_text(parts.get('test'))}，"
                f"状态：{self._status_text(parts.get('status'))}"
            )
        if action == "requirement.update_test_completion" and raw.startswith("test_completed="):
            return f"测试完成状态：{'已完成' if raw.split('=', 1)[1] == 'True' else '未完成'}"
        if action == "requirement.update_cases" and raw.startswith("case_count="):
            return f"用例数量：{raw.split('=', 1)[1]}"
        if action in {"requirement.add_case", "requirement.delete_case"}:
            return f"用例编号：{raw}"
        if action == "requirement.update_case" and "->" in raw:
            old_value, new_value = raw.split("->", 1)
            return f"用例编号：{old_value} -> {new_value}"
        if action == "requirement.upsert_test_execution":
            parts = dict(part.split("=", 1) for part in raw.split(",") if "=" in part)
            return (
                f"小版本：{self._version_name(parts.get('minor'))}，"
                f"结果：{self._status_text(parts.get('result'))}，"
                f"测试完成：{self._bool_text(parts.get('test_completed'))}"
            )
        if action in {"requirement.test_notes.update", "requirement.update_test_notes"}:
            parts = dict(part.split("=", 1) for part in raw.split(",") if "=" in part)
            return f"测试要点长度：{parts.get('before_len', '0')} -> {parts.get('after_len', '0')}"
        if action == "requirement.link_major":
            parts = dict(part.split("=", 1) for part in raw.split(",") if "=" in part)
            return (
                f"来源大版本：{self._version_name(parts.get('from_major'))}，"
                f"来源需求：{self._requirement_label(parts.get('from_req'))}，"
                f"同步状态：{self._bool_text(parts.get('copy_status'))}"
            )
        if action == "feedback.assign" and raw.startswith("assignee="):
            return f"处理人：{self._user_name(raw.split('=', 1)[1])}"
        if action in {"feedback.handle", "feedback.status"}:
            status_value = raw.split("=", 1)[1] if raw.startswith("status=") else raw
            return f"反馈状态：{self._status_text(status_value)}"
        if action in {"feedback.link_bug", "feedback.unlink_bug"} and raw.startswith("bug="):
            return f"关联 Bug：{self._bug_no(raw.split('=', 1)[1])}"
        if action == "feedback.create_bug_link":
            return f"新建并关联 Bug：{raw}"
        if action == "feedback.upload_attachment":
            return f"附件：{raw}"
        if action == "feedback.delete_attachment":
            return f"删除附件：{raw}"
        if action in {"field_test.add_bug", "field_test.link_bug", "field_test.unlink_bug"} and raw.startswith("bug="):
            return f"Bug：{self._bug_no(raw.split('=', 1)[1])}"
        if action == "field_test.update":
            parts = dict(part.split("=", 1) for part in raw.split(",") if "=" in part)
            return f"测试结果：{self._status_text(parts.get('result'))}，新增 Bug：{parts.get('added_bugs', '0')}"
        if action == "field_test.create":
            parts = dict(part.split("=", 1) for part in raw.split(",") if "=" in part)
            return f"测试结果：{self._status_text(parts.get('result'))}，Bug 数量：{parts.get('bugs', '0')}"
        if action == "retest.submit":
            parts = dict(part.split("=", 1) for part in raw.split(",") if "=" in part)
            passed_text = "通过" if parts.get("passed") == "True" else "打回" if parts.get("passed") == "False" else parts.get("passed", "-")
            return f"复测结论：{passed_text}，小版本：{self._version_name(parts.get('minor'))}"
        if action == "stage5.submit_result":
            parts = dict(part.split("=", 1) for part in raw.split(",") if "=" in part)
            return (
                f"闭环：{self._bool_text(parts.get('closed'))}，"
                f"结论：{self._status_text(parts.get('resolution'))}，"
                f"新增问题：{parts.get('new', '无') or '无'}"
            )
        if action == "stage5.add_issue":
            return f"新增问题：{raw}"
        return raw

    def _build_summary(self, log: AuditLog, actor_name: str, action_text: str, target_no: str | None, target_title: str | None) -> str:
        action = log.action or ""
        if action == "bug.create":
            return f"{actor_name} 创建了 Bug {target_no or ''}".strip()
        if action == "bug.dispatch":
            return f"{actor_name} 特派了 Bug {target_no or ''}".strip()
        if action == "bug.toggle_retest_fail":
            return f"{actor_name} 切换了 Bug {target_no or ''} 的复测未修好标记".strip()
        if action in {"stage5.submit_result", "stage5.add_issue"}:
            return f"{actor_name} {action_text} {target_no or ''}".strip()
        if action in {"requirement.test_notes.update", "requirement.update_test_notes"}:
            return f"{actor_name} 更新了需求 {target_no or ''} 的测试要点".strip()
        if action == "requirement.assign":
            return f"{actor_name} 变更了需求 {target_no or ''} 的负责人".strip()
        if action == "requirement.link_major":
            return f"{actor_name} 关联了需求 {target_no or ''} 到新大版本".strip()
        if action == "retest.submit":
            return f"{actor_name} 提交了需求 {target_no or ''} 的复测结果".strip()
        if action == "feedback.link_bug":
            return f"{actor_name} 将反馈 {target_no or ''} 关联到了 Bug".strip()
        if action == "feedback.create_bug_link":
            return f"{actor_name} 为反馈 {target_no or ''} 新建并关联了 Bug".strip()
        if action == "field_test.create":
            return f"{actor_name} 创建了 {target_no or '外业测试记录'}".strip()
        if action == "field_test.add_bug":
            return f"{actor_name} 为 {target_no or '外业测试记录'} 新增了 Bug".strip()
        title_part = f"（{target_title}）" if target_title else ""
        return f"{actor_name} {action_text} {target_no or ''}{title_part}".strip()

    def _build_item(self, log: AuditLog, actor_map: dict[int, str], ctx: dict[str, dict[int, Any]]) -> dict[str, Any]:
        meta = self._get_action_meta(log.action or "")
        actor_name = actor_map.get(log.actor_id or -1, "系统")
        target_id, target_no, target_title = self._resolve_target_info(log, ctx)
        detail_text = self._render_detail(log.action or "", log.detail)
        summary = self._build_summary(log, actor_name, meta["action_text"], target_no, target_title)
        return {
            "id": log.id,
            "created_at": log.created_at.isoformat() if log.created_at else None,
            "actor_id": log.actor_id,
            "actor_name": actor_name,
            "module": meta["module"],
            "action": log.action,
            "action_text": meta["action_text"],
            "target_type": log.target_type,
            "target_id": target_id,
            "target_no": target_no,
            "target_title": target_title,
            "summary": summary,
            "detail": detail_text,
            "level": meta["level"],
            "important": bool(meta["important"]),
        }

    def _hydrate_logs(self, logs: list[AuditLog]) -> list[dict[str, Any]]:
        actor_ids = list({log.actor_id for log in logs if log.actor_id})
        actor_rows = self.db.query(User).filter(User.id.in_(actor_ids)).all() if actor_ids else []
        actor_map = {user.id: user.shown_name for user in actor_rows}
        ctx = self._load_context(logs)
        return [self._build_item(log, actor_map, ctx) for log in logs]

    def list_feed(
        self,
        *,
        target_type: str | None = None,
        action: str | None = None,
        actor_id: int | None = None,
        keyword: str | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
        only_important: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        query = self.db.query(AuditLog)
        if actor_id:
            query = query.filter(AuditLog.actor_id == actor_id)
        if date_from:
            query = query.filter(AuditLog.created_at >= date_from)
        if date_to:
            query = query.filter(AuditLog.created_at <= date_to)
        if action:
            query = query.filter(AuditLog.action == action)
        if keyword:
            key = keyword.strip()
            query = query.filter(or_(AuditLog.action.like(f"%{key}%"), AuditLog.detail.like(f"%{key}%"), AuditLog.target_id.like(f"%{key}%")))

        logs = query.order_by(desc(AuditLog.created_at), desc(AuditLog.id)).limit(max(1000, offset + limit + 200)).all()
        items = self._hydrate_logs(logs)

        if target_type:
            items = [item for item in items if item["module"] == target_type or item["target_type"] == target_type]
        if keyword:
            lowered = keyword.strip().lower()
            items = [
                item
                for item in items
                if lowered in (item["summary"] or "").lower()
                or lowered in (item["detail"] or "").lower()
                or lowered in (item["target_no"] or "").lower()
                or lowered in (item["target_title"] or "").lower()
                or lowered in (item["actor_name"] or "").lower()
            ]
        if only_important:
            items = [item for item in items if item["important"]]

        total = len(items)
        return {"items": items[offset : offset + limit], "total": total, "limit": limit, "offset": offset}

    def get_summary(
        self,
        *,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
        target_types: list[str] | None = None,
        only_important: bool = False,
    ) -> dict[str, Any]:
        items = self.list_feed(date_from=date_from, date_to=date_to, only_important=only_important, limit=500, offset=0)["items"]
        if target_types:
            items = [item for item in items if item["module"] in target_types or item["target_type"] in target_types]

        buckets = {key: {"key": key, "label": label, "count": 0} for key, label in SUMMARY_BUCKETS.values()}
        for item in items:
            bucket = SUMMARY_BUCKETS.get(item["action"])
            if bucket:
                buckets[bucket[0]]["count"] += 1

        cards = [card for card in buckets.values() if card["count"] > 0]
        cards.sort(key=lambda card: card["count"], reverse=True)
        highlights = [item for item in items if item["important"]][:8]
        return {"cards": cards, "highlights": highlights}

    def build_push_markdown(self, *, hours: int = 24, target_types: list[str] | None = None, only_important: bool = True) -> str:
        end_at = datetime.utcnow()
        start_at = end_at - timedelta(hours=hours)
        summary = self.get_summary(date_from=start_at, date_to=end_at, target_types=target_types, only_important=only_important)
        items = self.list_feed(
            date_from=start_at,
            date_to=end_at,
            target_type=target_types[0] if target_types and len(target_types) == 1 else None,
            only_important=only_important,
            limit=10,
            offset=0,
        )["items"]
        if target_types and len(target_types) > 1:
            items = [item for item in items if item["module"] in target_types or item["target_type"] in target_types]

        lines = ["【团队最近动态摘要】", f"时间范围：最近{hours}小时", ""]
        if summary["cards"]:
            for idx, card in enumerate(summary["cards"], start=1):
                lines.append(f"{idx}. {card['label']}：{card['count']} 次")
        else:
            lines.append("最近没有关键动态。")

        if items:
            lines.extend(["", "关键动态："])
            for item in items[:6]:
                lines.append(f"- {item['summary']}")
        return "\n".join(lines)

    def _timeline_by_logs(self, logs: list[AuditLog]) -> list[dict[str, Any]]:
        if not logs:
            return []
        items = self._hydrate_logs(logs)
        items.sort(key=lambda item: (item["created_at"] or "", item["id"] or 0), reverse=True)
        return items

    def bug_timeline(self, bug_id: int) -> list[dict[str, Any]]:
        bug = self.db.query(BugTracking).filter(BugTracking.id == bug_id).first()
        if not bug:
            raise HTTPException(status_code=404, detail="Bug 不存在")
        logs = (
            self.db.query(AuditLog)
            .filter(AuditLog.target_type == "bug", AuditLog.target_id == str(bug_id))
            .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
            .all()
        )
        return self._timeline_by_logs(logs)

    def feedback_timeline(self, feedback_id: int) -> list[dict[str, Any]]:
        row = self.db.query(FeedbackRecord).filter(FeedbackRecord.id == feedback_id).first()
        if not row:
            raise HTTPException(status_code=404, detail="反馈不存在")
        logs = (
            self.db.query(AuditLog)
            .filter(AuditLog.target_type == "feedback", AuditLog.target_id == str(feedback_id))
            .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
            .all()
        )
        return self._timeline_by_logs(logs)

    def field_test_timeline(self, record_id: int) -> list[dict[str, Any]]:
        row = self.db.query(FieldTestRecord).filter(FieldTestRecord.id == record_id).first()
        if not row:
            raise HTTPException(status_code=404, detail="外业测试记录不存在")
        logs = (
            self.db.query(AuditLog)
            .filter(AuditLog.target_type == "field_test", AuditLog.target_id == str(record_id))
            .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
            .all()
        )
        return self._timeline_by_logs(logs)

    def requirement_timeline(self, requirement_id: int) -> list[dict[str, Any]]:
        requirement = self.db.query(Requirement).options(joinedload(Requirement.test_cases)).filter(Requirement.id == requirement_id).first()
        if not requirement:
            raise HTTPException(status_code=404, detail="需求不存在")
        case_ids = [case.id for case in requirement.test_cases or []]
        filters = [(AuditLog.target_type == "requirement") & (AuditLog.target_id == str(requirement_id))]
        if case_ids:
            filters.append((AuditLog.target_type == "test_case") & (AuditLog.target_id.in_([str(case_id) for case_id in case_ids])))
        query = self.db.query(AuditLog).filter(or_(*filters))
        logs = query.order_by(AuditLog.created_at.desc(), AuditLog.id.desc()).all()
        return self._timeline_by_logs(logs)
