from __future__ import annotations

import hashlib
import json
import re
from datetime import timedelta
from urllib.parse import urlparse
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import or_, func, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import StaleDataError

from app.models import User, AuditLog
from app.models.bugly import (
    BuglyApplication, BuglyIssue, BuglyReport, BuglyReportRevision, BuglyImportBatch,
)
from app.services.permission_service import ensure_tab_access
from app.services.bugly_privacy import hide_user_ids, user_id_values
from app.services.bugly_import import (
    digest, merge_payload, normalize_report, parse_file, report_columns, source_zone,
)
from app.utils.time_utils import utc_now_naive


def iso(value):
    return value.isoformat() + "Z" if value else None


def redact(value, sensitive_values=()):
    value = str(value or "")
    for token in sensitive_values:
        if token and len(str(token)) >= 3:
            value = value.replace(str(token), "[已脱敏]")
    value = re.sub(r"(?i)(token|authorization|password|secret|用户\s*ID|user[_ ]?id)\s*[:=：]\s*[^\s,;]+", r"\1=[已脱敏]", value)
    value = re.sub(r"(?i)Bearer\s+\S+", "Bearer [已脱敏]", value)
    value = re.sub(r"https?://[^\s<>\"']+", "[地址已脱敏]", value)
    value = re.sub(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", "[IP已脱敏]", value)
    value = re.sub(r"[A-Za-z]:[\\/][^\s<>\"']+|/(?:Users|home|data|storage|sdcard|workspace|builds)/[^\s<>\"']+", "[路径已脱敏]", value)
    return value


def sensitive_values(payload):
    values = []
    for group in ("listFields", "detailFields"):
        for key, value in payload.get(group, {}).items():
            canonical = re.sub(r"[\s_]", "", key).lower()
            if canonical in {"用户id", "userid", "设备id", "deviceid", "token", "password", "secret", "authorization"}:
                values.append(value)
    values.extend(user_id_values(payload))
    return values


class BuglyService:
    """Bugly 问题中心。

    访问控制只有一层：数据中心里勾选了「Bugly 问题中心」页面权限的内部账号即可查看与维护全部应用、
    问题、上报和导入（2026-11-26 起不再有应用 / 问题级授权、外部只读账号和对外发布）。
    """

    def __init__(self, db, user):
        self.db, self.user = db, user

    def internal(self):
        if self.user.account_type == "external":
            raise HTTPException(403, "外部账号已停用")
        ensure_tab_access(self.user, "bugly")

    def audit(self, action, target, detail=None):
        # Do not use the legacy helper's commit inside an import transaction.
        self.db.add(AuditLog(actor_id=self.user.id, action="bugly." + action,
                             target_type="bugly", target_id=str(target), detail=detail))

    def commit(self):
        try:
            self.db.commit()
        except (IntegrityError, StaleDataError):
            self.db.rollback()
            raise HTTPException(409, "数据已变化或编号重复，请刷新后重试")

    def flush(self):
        try:
            self.db.flush()
        except (IntegrityError, StaleDataError):
            self.db.rollback()
            raise HTTPException(409, "数据已变化或编号重复，请刷新后重试")

    def application(self, app_id):
        app = self.db.get(BuglyApplication, app_id)
        if not app:
            raise HTTPException(404, "应用不存在")
        return app

    def issue(self, issue_id):
        row = self.db.get(BuglyIssue, issue_id)
        if not row:
            raise HTTPException(404, "问题不存在")
        return row

    def applications(self):
        self.internal()
        rows = self.db.query(BuglyApplication).order_by(BuglyApplication.name).all()
        return [{"id": app.id, "name": app.name, "source_host": app.source_host,
                 "bugly_app_id": app.bugly_app_id, "package_name": app.package_name,
                 "platform": app.platform, "source_timezone": app.source_timezone,
                 "software_id": app.software_id,
                 "issue_count": self.db.query(func.count(BuglyIssue.id)).filter(BuglyIssue.application_id == app.id).scalar() or 0}
                for app in rows]

    def create_application(self, data):
        self.internal()
        source_zone(data["source_timezone"])
        host = data["source_host"].strip().lower()
        if not re.fullmatch(r"[a-z0-9.-]+", host):
            raise HTTPException(422, "来源站点请填写主机名，不包含 https:// 或路径")
        if data.get("software_id"):
            from app.models import SoftwareProduct
            if not self.db.get(SoftwareProduct, data["software_id"]):
                raise HTTPException(422, "关联软件不存在")
        row = BuglyApplication(**{**data, "source_host": host})
        self.db.add(row)
        self.flush()
        self.audit("application.create", row.id)
        self.commit()
        return {"id": row.id}

    def create_issue(self, data):
        self.internal()
        self.application(data["application_id"])
        row = BuglyIssue(**data)
        self.db.add(row)
        try:
            self.db.flush()
        except IntegrityError:
            self.db.rollback()
            raise HTTPException(409, "该应用下的问题编号已存在")
        self.audit("issue.create", row.id)
        self.commit()
        return {"id": row.id}

    def summary(self, issue):
        app = self.application(issue.application_id)
        reports = self.db.query(BuglyReport).filter_by(issue_id=issue.id)
        return {"id": issue.id, "application_id": app.id, "application_name": app.name,
                "source_issue_id": issue.source_issue_id, "title": issue.title,
                "status": issue.status, "exception_type": issue.exception_type,
                "archived": issue.archived, "version": issue.version,
                "report_count": reports.count(),
                "last_occurred_at": iso(reports.with_entities(func.max(BuglyReport.occurred_at)).scalar()),
                "versions": [v[0] for v in reports.with_entities(BuglyReport.app_version).filter(BuglyReport.app_version != "").distinct().limit(20)],
                "owner_id": issue.owner_id,
                "updated_at": iso(issue.updated_at)}

    def issue_query(self, filters):
        self.internal()
        query = self.db.query(BuglyIssue)
        if not filters.get("include_archived"):
            query = query.filter(BuglyIssue.archived.is_(False))
        if filters.get("application_id"):
            query = query.filter(BuglyIssue.application_id == filters["application_id"])
        if filters.get("status"):
            query = query.filter(BuglyIssue.status == filters["status"])
        if filters.get("exception_type"):
            query = query.filter(BuglyIssue.exception_type == filters["exception_type"])
        reports = self.db.query(BuglyReport.issue_id)
        for key, column in (("app_version", BuglyReport.app_version), ("device", BuglyReport.device)):
            if filters.get(key):
                reports = reports.filter(column.contains(filters[key], autoescape=True))
        for key, comparator in (("since", BuglyReport.occurred_at.__ge__), ("until", BuglyReport.occurred_at.__le__)):
            if filters.get(key):
                reports = reports.filter(comparator(filters[key]))
        if any(filters.get(k) for k in ("app_version", "device", "since", "until")):
            query = query.filter(BuglyIssue.id.in_(reports))
        if filters.get("q"):
            value = filters["q"]
            matching = self.db.query(BuglyReport.issue_id).filter(or_(BuglyReport.stack_text.contains(value, autoescape=True), BuglyReport.source_report_id.contains(value, autoescape=True)))
            query = query.filter(or_(BuglyIssue.title.contains(value, autoescape=True), BuglyIssue.source_issue_id.contains(value, autoescape=True), BuglyIssue.id.in_(matching)))
        return query

    def list_issues(self, filters):
        query = self.issue_query(filters)
        total = query.count()
        page, size = filters.get("page", 1), filters.get("page_size", 20)
        rows = query.order_by(BuglyIssue.updated_at.desc(), BuglyIssue.id.desc()).offset((page - 1) * size).limit(size).all()
        # 状态计数（不受状态筛选本身影响），页面上做成可点的状态筛选条
        counted = self.issue_query({**filters, "status": ""}).with_entities(BuglyIssue.status, func.count(BuglyIssue.id)).group_by(BuglyIssue.status)
        return {"items": [self.summary(row) for row in rows], "total": total, "page": page, "page_size": size,
                "status_counts": {status: count for status, count in counted}}

    def list_reports(self, filters):
        self.internal()
        issues = self.issue_query(filters).with_entities(BuglyIssue.id)
        query = self.db.query(BuglyReport).filter(BuglyReport.issue_id.in_(issues))
        for key, column in (("app_version", BuglyReport.app_version), ("device", BuglyReport.device)):
            if filters.get(key):
                query = query.filter(column.contains(filters[key], autoescape=True))
        if filters.get("since"):
            query = query.filter(BuglyReport.occurred_at >= filters["since"])
        if filters.get("until"):
            query = query.filter(BuglyReport.occurred_at <= filters["until"])
        if filters.get("q"):
            value = filters["q"]
            matching_issues = self.db.query(BuglyIssue.id).filter(or_(BuglyIssue.title.contains(value, autoescape=True), BuglyIssue.source_issue_id.contains(value, autoescape=True)))
            query = query.filter(or_(BuglyReport.issue_id.in_(matching_issues), BuglyReport.stack_text.contains(value, autoescape=True), BuglyReport.source_report_id.contains(value, autoescape=True)))
        total = query.count()
        page, size = filters.get("page", 1), filters.get("page_size", 20)
        rows = query.order_by(BuglyReport.occurred_at.desc(), BuglyReport.id.desc()).offset((page-1)*size).limit(size).all()
        items = []
        for row in rows:
            issue = self.db.get(BuglyIssue, row.issue_id)
            items.append({"id": row.id, "issue_id": issue.id, "title": issue.title,
                          "source_issue_id": issue.source_issue_id, "source_report_id": row.source_report_id,
                          "application_name": self.application(issue.application_id).name,
                          "app_version": row.app_version, "device": row.device,
                          "occurred_at": iso(row.occurred_at), "stack_mode": row.stack_mode,
                          "report_count": 1, "status": issue.status})
        return {"items": items, "total": total, "page": page, "page_size": size}

    def report_dict(self, row, sensitive=True):
        payload = row.payload
        display = hide_user_ids({"payload": payload, "stack_text": row.stack_text})
        hidden_values = sensitive_values(payload)
        if sensitive:
            safe = display["payload"]
        else:
            safe = {"listFields": {}, "detailFields": {}}
            allowed = {"版本", "应用版本", "异常上报时间", "发生时间", "设备", "设备机型", "系统版本", "CPU架构", "ROM", "前后台状态", "网络类型"}
            for key in ("listFields", "detailFields"):
                safe[key] = {k: redact(v, hidden_values) for k, v in display["payload"].get(key, {}).items() if k.replace(" ", "") in allowed}
        return {"id": row.id, "source_report_id": row.source_report_id, "app_version": row.app_version,
                "device": row.device, "os_version": row.os_version, "occurred_at": iso(row.occurred_at),
                "occurred_at_raw": row.occurred_at_raw, "collected_at": iso(row.collected_at),
                "stack_text": display["stack_text"] if sensitive else redact(display["stack_text"], hidden_values), "stack_mode": row.stack_mode,
                "frame_count": row.frame_count, "revision": row.revision, "payload": safe}

    def detail(self, issue_id):
        self.internal()
        issue = self.issue(issue_id)
        owner = self.db.get(User, issue.owner_id) if issue.owner_id else None
        return self.summary(issue) | {"analysis": issue.analysis, "external_note": issue.external_note,
                                     "owner_name": owner.shown_name if owner else None}

    def reports(self, issue_id, page=1, page_size=20, q=""):
        self.internal()
        issue = self.issue(issue_id)
        query = self.db.query(BuglyReport).filter_by(issue_id=issue.id)
        if q:
            query = query.filter(or_(BuglyReport.app_version.contains(q, autoescape=True), BuglyReport.device.contains(q, autoescape=True), BuglyReport.source_report_id.contains(q, autoescape=True)))
        total = query.count()
        rows = query.order_by(BuglyReport.occurred_at.desc(), BuglyReport.id.desc()).offset((page-1)*page_size).limit(page_size).all()
        return {"items": [{"id": r.id, "source_report_id": r.source_report_id, "app_version": r.app_version,
                           "device": r.device, "occurred_at": iso(r.occurred_at), "stack_mode": r.stack_mode} for r in rows], "total": total}

    def report_detail(self, report_id):
        self.internal()
        row = self.db.get(BuglyReport, report_id)
        if not row:
            raise HTTPException(404, "上报不存在")
        self.issue(row.issue_id)
        return self.report_dict(row)

    def report_revisions(self, report_id):
        self.internal()
        row = self.db.get(BuglyReport, report_id)
        if not row:
            raise HTTPException(404, "上报不存在")
        self.issue(row.issue_id)
        return [{"revision": revision.revision, "created_at": iso(revision.created_at),
                 "payload": hide_user_ids(revision.payload), "batch_id": revision.batch_id}
                for revision in self.db.query(BuglyReportRevision).filter_by(report_id=row.id).order_by(BuglyReportRevision.id.desc()).limit(50)]

    def update_issue(self, issue_id, data, expected_version):
        self.internal()
        row = self.issue(issue_id)
        if row.version != expected_version:
            raise HTTPException(409, "问题已被修改，请刷新后保存")
        if "source_issue_id" in data and row.source_issue_id and data["source_issue_id"] != row.source_issue_id:
            raise HTTPException(422, "已有 Bugly 编号不能直接更换，请保留原问题关联")
        if data.get("source_issue_id"):
            conflict = self.db.query(BuglyIssue).filter_by(application_id=row.application_id, source_issue_id=data["source_issue_id"]).first()
            if conflict and conflict.id != row.id:
                raise HTTPException(409, "该 Bugly 编号已属于其他问题，请核对并使用已有问题")
        if data.get("owner_id") and not self.db.get(User, data["owner_id"]):
            raise HTTPException(422, "负责人不存在")
        for key, value in data.items():
            setattr(row, key, value)
        row.updated_at = utc_now_naive()
        self.audit("issue.update", row.id)
        self.commit()
        return self.detail(row.id)

    def put_report(self, issue, payload, batch_id=None):
        row = self.db.query(BuglyReport).filter_by(issue_id=issue.id, source_report_id=payload["reportId"]).first() if payload["reportId"] else None
        merged = merge_payload(row.payload, payload) if row else payload
        content_hash = digest(merged)
        if row and row.content_hash == content_hash:
            return "skipped", row.id
        columns = report_columns(merged, self.application(issue.application_id).source_timezone)
        if row:
            row.payload, row.content_hash = merged, content_hash
            for key, value in columns.items():
                setattr(row, key, value)
            action = "updated"
        else:
            row = BuglyReport(issue_id=issue.id, source_report_id=payload["reportId"], payload=merged, content_hash=content_hash, **columns)
            self.db.add(row)
            action = "created"
        issue.updated_at = utc_now_naive()
        self.flush()
        self.db.add(BuglyReportRevision(report_id=row.id, revision=row.revision, payload=merged,
                                       actor_id=self.user.id, batch_id=batch_id))
        return action, row.id

    def create_report(self, issue_id, payload):
        self.internal()
        issue = self.issue(issue_id)
        if issue.archived:
            raise HTTPException(409, "问题已归档，请恢复后新增上报")
        if payload.get("reportId") and self.db.query(BuglyReport).filter_by(issue_id=issue.id, source_report_id=payload["reportId"]).first():
            raise HTTPException(409, "上报编号已存在，请通过导入预览更新")
        _, report_id = self.put_report(issue, normalize_report(payload))
        self.audit("report.create", report_id)
        self.commit()
        return {"id": report_id}

    def preview_import(self, app_id, filename, data, assigned_issue_id=None):
        self.internal()
        app = self.application(app_id)
        assigned = self.issue(assigned_issue_id) if assigned_issue_id else None
        if assigned and assigned.application_id != app_id:
            raise HTTPException(422, "归属问题不属于所选应用")
        items, diagnostics = parse_file(filename, data, assigned)
        seen = set()
        for item in items:
            if item.get("error"):
                continue
            payload = item["payload"]
            package = payload.get("detailFields", {}).get("应用包名")
            host = urlparse(payload.get("pageUrl", "")).hostname
            if (app.package_name and package and app.package_name != package) or (host and host != app.source_host):
                item["error"] = "应用包名或来源站点与所选应用不一致"
                continue
            if assigned and item["source_issue_id"] and assigned.source_issue_id != item["source_issue_id"]:
                item["error"] = "文件问题编号与所选归属问题不一致"
                continue
            source_issue = assigned.source_issue_id if assigned else item["source_issue_id"]
            key = (source_issue, payload["reportId"])
            if key in seen:
                item["error"] = "本文件重复上报编号，请先合并重复行"
                continue
            seen.add(key)
            issue = assigned or self.db.query(BuglyIssue).filter_by(application_id=app_id, source_issue_id=source_issue).first()
            if issue and issue.archived:
                item["error"] = "归属问题已归档，请恢复后导入"
                continue
            row = self.db.query(BuglyReport).filter_by(issue_id=issue.id, source_report_id=payload["reportId"]).first() if issue else None
            item["issue_id"] = issue.id if issue else None
            item["source_issue_id"] = source_issue
            item["existing_hash"] = row.content_hash if row else None
            item["action"] = ("skipped" if row.content_hash == digest(merge_payload(row.payload, payload)) else "updated") if row else "created"
        diagnostics["assigned_issue_id"] = assigned_issue_id
        batch = BuglyImportBatch(id=str(uuid4()), application_id=app_id, actor_id=self.user.id,
                                 filename=filename[:255], file_hash=hashlib.sha256(data).hexdigest(), items=items,
                                 diagnostics=diagnostics, expires_at=utc_now_naive()+timedelta(minutes=30))
        self.db.add(batch)
        self.audit("import.preview", batch.id)
        self.commit()
        return self.batch_dict(batch)

    def batch_dict(self, row):
        return hide_user_ids({"id": row.id, "filename": row.filename, "status": row.status, "created_at": iso(row.created_at),
                "expires_at": iso(row.expires_at), "application_id": row.application_id,
                "diagnostics": row.diagnostics, "result": row.result,
                "items": [{key: value for key, value in item.items() if key not in {"payload", "existing_hash"}} for item in row.items]})

    def batch(self, batch_id):
        self.internal()
        row = self.db.get(BuglyImportBatch, batch_id)
        if not row:
            raise HTTPException(404, "导入记录不存在")
        return row

    def confirm_import(self, batch_id):
        batch = self.batch(batch_id)
        if batch.status == "confirmed":
            return self.batch_dict(batch)
        if batch.expires_at <= utc_now_naive():
            raise HTTPException(410, "导入预览已过期，请重新上传")
        # Acquire a write lock with a conditional update, making confirmations idempotent.
        claimed = self.db.execute(update(BuglyImportBatch).where(BuglyImportBatch.id == batch.id, BuglyImportBatch.status == "preview").values(status="confirming")).rowcount
        if not claimed:
            self.db.rollback()
            raise HTTPException(409, "导入任务正在处理，请刷新查看结果")
        result = {"created": 0, "updated": 0, "skipped": 0, "failed": 0, "items": []}
        try:
            for item in batch.items:
                if item.get("error"):
                    result["failed"] += 1
                    result["items"].append({"row": item["row"], "error": item["error"]})
                    continue
                issue = self.db.get(BuglyIssue, item["issue_id"]) if item["issue_id"] else self.db.query(BuglyIssue).filter_by(application_id=batch.application_id, source_issue_id=item["source_issue_id"]).first()
                if issue and issue.archived:
                    raise HTTPException(409, "问题已归档，请重新预览")
                existing = self.db.query(BuglyReport).filter_by(issue_id=issue.id, source_report_id=item["payload"]["reportId"]).first() if issue else None
                if (existing.content_hash if existing else None) != item["existing_hash"]:
                    raise HTTPException(409, "预览后数据已变化，请重新上传预览")
                if not issue:
                    issue = BuglyIssue(application_id=batch.application_id, source_issue_id=item["source_issue_id"], title=item["title"])
                    self.db.add(issue)
                    self.flush()
                action, report_id = self.put_report(issue, item["payload"], batch.id)
                result[action] += 1
                result["items"].append({"row": item["row"], "action": action, "report_id": report_id, "issue_id": issue.id})
            batch.status, batch.confirmed_at, batch.result = "confirmed", utc_now_naive(), result
            # Source payload now lives in revision records. Do not retain duplicate stacks in the batch.
            batch.items = [{k: v for k, v in item.items() if k != "payload"} for item in batch.items]
            self.audit("import.confirm", batch.id, json.dumps({k: v for k, v in result.items() if k != "items"}))
            self.commit()
        except Exception:
            self.db.rollback()
            raise
        return self.batch_dict(batch)

    def import_history(self):
        self.internal()
        rows = self.db.query(BuglyImportBatch).order_by(BuglyImportBatch.created_at.desc()).limit(50).all()
        result = []
        for row in rows:
            item = self.batch_dict(row)
            actor = self.db.get(User, row.actor_id) if row.actor_id else None
            item["actor_name"] = actor.shown_name if actor else None
            result.append(item)
        return result
