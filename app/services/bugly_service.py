from __future__ import annotations

import hashlib
import json
import re
from datetime import timedelta
from urllib.parse import urlparse
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import and_, or_, exists, func, update, Text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import StaleDataError

from app.models import User, UserRole, AuditLog
from app.models.bugly import (
    BuglyApplication, BuglyIssue, BuglyReport, BuglyReportRevision,
    BuglyImportBatch, BuglyGrant, BuglyPublication,
)
from app.services.permission_service import ensure_admin, ensure_tab_access, get_allowed_tabs, serialize_tab_permissions
from app.services.bugly_privacy import hide_user_ids, user_id_values
from app.services.bugly_import import (
    digest, merge_payload, normalize_report, parse_file, report_columns, source_zone, utc_expiry,
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
    def __init__(self, db, user):
        self.db, self.user = db, user

    @property
    def admin(self):
        return self.user.account_type != "external" and self.user.role == UserRole.ADMIN

    def internal(self):
        if self.user.account_type == "external":
            raise HTTPException(403, "外部账号只能浏览发布内容")
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

    def grants(self):
        return self.db.query(BuglyGrant).filter(
            BuglyGrant.user_id == self.user.id,
            or_(BuglyGrant.expires_at.is_(None), BuglyGrant.expires_at > utc_now_naive()),
        )

    def visible_clause(self):
        return exists().where(and_(
            BuglyGrant.user_id == self.user.id,
            BuglyGrant.application_id == BuglyIssue.application_id,
            or_(BuglyGrant.issue_id.is_(None), BuglyGrant.issue_id == BuglyIssue.id),
            or_(BuglyGrant.expires_at.is_(None), BuglyGrant.expires_at > utc_now_naive()),
        ))

    def authorize(self, application_id, issue_id=None, operation="view"):
        if self.admin:
            return
        query = self.grants().filter(BuglyGrant.application_id == application_id)
        query = query.filter(or_(BuglyGrant.issue_id.is_(None), BuglyGrant.issue_id == issue_id)) if issue_id else query.filter(BuglyGrant.issue_id.is_(None))
        if operation == "edit":
            query = query.filter(BuglyGrant.role == "editor")
        elif operation == "publish":
            query = query.filter(BuglyGrant.can_publish.is_(True))
        elif operation == "sensitive":
            query = query.filter(BuglyGrant.can_sensitive.is_(True))
        if not query.first() or (self.user.account_type == "external" and operation != "view"):
            raise HTTPException(403, "没有该应用或问题的操作权限")

    def permitted(self, issue, operation):
        try:
            self.authorize(issue.application_id, issue.id, operation)
            return True
        except HTTPException:
            return False

    def application(self, app_id):
        app = self.db.get(BuglyApplication, app_id)
        if not app:
            raise HTTPException(404, "应用不存在")
        return app

    def issue(self, issue_id, operation="view"):
        row = self.db.get(BuglyIssue, issue_id)
        if not row:
            raise HTTPException(404, "问题不存在")
        self.authorize(row.application_id, row.id, operation)
        return row

    def applications(self):
        self.internal()
        query = self.db.query(BuglyApplication)
        if not self.admin:
            query = query.filter(BuglyApplication.id.in_(self.grants().with_entities(BuglyGrant.application_id)))
        rows = query.order_by(BuglyApplication.name).all()
        result = []
        for app in rows:
            writable = self.admin or self.grants().filter(BuglyGrant.application_id == app.id, BuglyGrant.issue_id.is_(None), BuglyGrant.role == "editor").first() is not None
            result.append({"id": app.id, "name": app.name, "source_host": app.source_host,
                           "bugly_app_id": app.bugly_app_id, "package_name": app.package_name,
                           "platform": app.platform, "source_timezone": app.source_timezone,
                           "software_id": app.software_id, "can_edit": writable})
        return result

    def create_application(self, data):
        self.internal()
        ensure_admin(self.user)
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
        self.authorize(data["application_id"], operation="edit")
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
        publication = self.db.get(BuglyPublication, issue.id)
        return {"id": issue.id, "application_id": app.id, "application_name": app.name,
                "source_issue_id": issue.source_issue_id, "title": issue.title,
                "status": issue.status, "exception_type": issue.exception_type,
                "archived": issue.archived, "version": issue.version,
                "report_count": reports.count(),
                "last_occurred_at": iso(reports.with_entities(func.max(BuglyReport.occurred_at)).scalar()),
                "versions": [v[0] for v in reports.with_entities(BuglyReport.app_version).filter(BuglyReport.app_version != "").distinct().limit(20)],
                "published": bool(publication and publication.active),
                "unpublished_changes": bool(publication and publication.source_version != issue.version),
                "updated_at": iso(issue.updated_at)}

    def issue_query(self, filters, external=False):
        if not external:
            self.internal()
        query = self.db.query(BuglyIssue)
        if not self.admin:
            query = query.filter(self.visible_clause())
        if external:
            # Search approved content, never current internal issue/report fields.
            query = query.join(BuglyPublication).filter(BuglyPublication.active.is_(True), BuglyIssue.archived.is_(False))
        elif not filters.get("include_archived"):
            query = query.filter(BuglyIssue.archived.is_(False))
        if filters.get("application_id"):
            query = query.filter(BuglyIssue.application_id == filters["application_id"])
        if external:
            # Snapshot JSON filtering is independent from unreviewed internal data.
            if filters.get("q"):
                query = query.filter(BuglyPublication.snapshot.cast(Text).contains(filters["q"], autoescape=True))
        else:
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

    def list_issues(self, filters, external=False):
        query = self.issue_query(filters, external)
        total = query.count()
        page, size = filters.get("page", 1), filters.get("page_size", 20)
        rows = query.order_by(BuglyIssue.updated_at.desc(), BuglyIssue.id.desc()).offset((page - 1) * size).limit(size).all()
        if external:
            items = []
            for row in rows:
                publication = self.db.get(BuglyPublication, row.id)
                snapshot = publication.snapshot
                items.append({key: snapshot.get(key) for key in ("title", "source_issue_id", "application_name", "exception_type")}
                             | {"id": row.id, "application_id": row.application_id, "report_count": len(snapshot["reports"]), "published_at": iso(publication.published_at)})
        else:
            items = [self.summary(row) for row in rows]
        return {"items": items, "total": total, "page": page, "page_size": size}

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
            publication = self.db.get(BuglyPublication, issue.id)
            items.append({"id": row.id, "issue_id": issue.id, "title": issue.title,
                          "source_issue_id": issue.source_issue_id, "source_report_id": row.source_report_id,
                          "application_name": self.application(issue.application_id).name,
                          "app_version": row.app_version, "device": row.device,
                          "occurred_at": iso(row.occurred_at), "stack_mode": row.stack_mode,
                          "report_count": 1, "published": bool(publication and publication.active), "status": issue.status})
        return {"items": items, "total": total, "page": page, "page_size": size}

    def report_dict(self, row, sensitive=False):
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
        return self.summary(issue) | {"analysis": issue.analysis, "external_note": issue.external_note,
                                     "owner_id": issue.owner_id, "can_edit": self.permitted(issue, "edit"),
                                     "can_publish": self.permitted(issue, "publish"),
                                     "can_sensitive": self.permitted(issue, "sensitive")}

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
        issue = self.issue(row.issue_id)
        return self.report_dict(row, self.permitted(issue, "sensitive"))

    def report_revisions(self, report_id):
        self.internal()
        row = self.db.get(BuglyReport, report_id)
        if not row:
            raise HTTPException(404, "上报不存在")
        self.issue(row.issue_id, "sensitive")
        return [{"revision": revision.revision, "created_at": iso(revision.created_at),
                 "payload": hide_user_ids(revision.payload), "batch_id": revision.batch_id}
                for revision in self.db.query(BuglyReportRevision).filter_by(report_id=row.id).order_by(BuglyReportRevision.id.desc()).limit(50)]

    def update_issue(self, issue_id, data, expected_version):
        self.internal()
        row = self.issue(issue_id, "edit")
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
        if data.get("archived"):
            publication = self.db.get(BuglyPublication, row.id)
            if publication:
                publication.active = False
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
        issue = self.issue(issue_id, "edit")
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
        assigned = self.issue(assigned_issue_id, "edit") if assigned_issue_id else None
        if assigned and assigned.application_id != app_id:
            raise HTTPException(422, "归属问题不属于所选应用")
        self.authorize(app_id, assigned_issue_id, "edit")
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
        self.authorize(row.application_id, row.diagnostics.get("assigned_issue_id"), "edit")
        if not self.admin and row.actor_id != self.user.id:
            raise HTTPException(403, "仅上传者或管理员可查看该导入任务")
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
                self.authorize(batch.application_id, issue.id if issue else None, "edit")
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
        query = self.db.query(BuglyImportBatch)
        if not self.admin:
            query = query.filter(BuglyImportBatch.actor_id == self.user.id)
        rows = query.order_by(BuglyImportBatch.created_at.desc()).limit(50).all()
        result = []
        for row in rows:
            try:
                self.batch(row.id)
                result.append(self.batch_dict(row))
            except HTTPException:
                pass
        return result

    def publication_preview(self, issue_id):
        self.internal()
        issue = self.issue(issue_id, "publish")
        reports = self.db.query(BuglyReport).filter_by(issue_id=issue.id).order_by(BuglyReport.id).all()
        if len(reports) > 2000:
            raise HTTPException(422, "单次发布最多 2000 条上报，请缩小数据范围")
        snapshot_reports = []
        sensitive = []
        for row in reports:
            sensitive.extend(sensitive_values(row.payload))
        for row in reports:
            snapshot_reports.append({"id": row.id, "source_report_id": row.source_report_id,
                                     "app_version": redact(row.app_version, sensitive), "device": redact(row.device, sensitive),
                                     "os_version": redact(row.os_version, sensitive), "occurred_at": iso(row.occurred_at),
                                     "stack_text": redact(row.stack_text, sensitive), "stack_mode": row.stack_mode})
        return hide_user_ids({"source_version": issue.version, "snapshot": {
            "title": redact(issue.title, sensitive), "source_issue_id": issue.source_issue_id,
            "application_name": redact(self.application(issue.application_id).name, sensitive),
            "exception_type": redact(issue.exception_type, sensitive), "external_note": redact(issue.external_note, sensitive),
            "reports": snapshot_reports,
        }})

    def publish(self, issue_id, source_version, snapshot):
        self.internal()
        issue = self.issue(issue_id, "publish")
        if issue.archived or issue.version != source_version:
            raise HTTPException(409, "问题已变化或已归档，请重新预览")
        valid_ids = {r[0] for r in self.db.query(BuglyReport.id).filter_by(issue_id=issue.id)}
        ids = [r["id"] for r in snapshot["reports"]]
        if not set(ids).issubset(valid_ids) or len(ids) != len(set(ids)):
            raise HTTPException(422, "发布快照包含无效或重复的上报记录")
        snapshot = hide_user_ids(snapshot)
        publication = self.db.get(BuglyPublication, issue.id)
        if publication:
            publication.snapshot, publication.source_version = snapshot, source_version
            publication.active, publication.published_by = True, self.user.id
            publication.published_at, publication.version = utc_now_naive(), publication.version + 1
        else:
            self.db.add(BuglyPublication(issue_id=issue.id, snapshot=snapshot, source_version=source_version, published_by=self.user.id))
        # Touch the versioned issue to detect a concurrent import/change before commit.
        issue.updated_at = utc_now_naive()
        self.flush()
        publication = self.db.get(BuglyPublication, issue.id)
        publication.source_version = issue.version
        self.audit("publication.publish", issue.id)
        self.commit()
        return {"message": "已发布审核后的快照"}

    def withdraw(self, issue_id):
        self.internal()
        issue = self.issue(issue_id, "publish")
        publication = self.db.get(BuglyPublication, issue.id)
        if publication:
            publication.active = False
        self.audit("publication.withdraw", issue.id)
        self.commit()
        return {"message": "已撤回"}

    def external_detail(self, issue_id):
        issue = self.issue(issue_id)
        publication = self.db.get(BuglyPublication, issue.id)
        if not publication or not publication.active or issue.archived:
            raise HTTPException(404, "内容尚未发布或已撤回")
        self.audit("external.view", issue.id)
        self.commit()
        return {"id": issue.id, "application_id": issue.application_id, "publication_version": publication.version,
                "published_at": iso(publication.published_at), **hide_user_ids(publication.snapshot)}

    def external_applications(self):
        query = self.db.query(BuglyIssue).join(BuglyPublication).filter(self.visible_clause(), BuglyPublication.active.is_(True), BuglyIssue.archived.is_(False))
        names = {}
        for issue in query:
            publication = self.db.get(BuglyPublication, issue.id)
            names[issue.application_id] = publication.snapshot["application_name"]
        return [{"id": key, "name": value} for key, value in names.items()]

    def grant(self, data):
        self.internal()
        ensure_admin(self.user)
        user = self.db.get(User, data["user_id"])
        if not user:
            raise HTTPException(404, "用户不存在")
        self.application(data["application_id"])
        if data.get("issue_id") and self.issue(data["issue_id"]).application_id != data["application_id"]:
            raise HTTPException(422, "问题不属于所选应用")
        if user.account_type == "external" and (data["role"] != "viewer" or data["can_sensitive"] or data["can_publish"]):
            raise HTTPException(422, "外部账号仅支持只读脱敏授权")
        data["expires_at"] = utc_expiry(data.get("expires_at"))
        scope = str(data["issue_id"]) if data.get("issue_id") else "*"
        row = self.db.query(BuglyGrant).filter_by(user_id=user.id, application_id=data["application_id"], scope=scope).first()
        if row:
            for key, value in data.items():
                setattr(row, key, value)
            row.granted_by = self.user.id
        else:
            row = BuglyGrant(**data, scope=scope, granted_by=self.user.id)
            self.db.add(row)
        if user.account_type != "external":
            user.tab_permissions = serialize_tab_permissions(get_allowed_tabs(user) + ["bugly"])
        self.flush()
        self.audit("grant.save", row.id)
        self.commit()
        return {"id": row.id}

    def list_grants(self):
        self.internal()
        ensure_admin(self.user)
        return [{"id": row.id, "user_id": row.user_id, "username": self.db.get(User, row.user_id).shown_name,
                 "application_id": row.application_id, "application_name": self.application(row.application_id).name,
                 "issue_id": row.issue_id, "issue_title": self.db.get(BuglyIssue, row.issue_id).title if row.issue_id else None,
                 "role": row.role, "can_sensitive": row.can_sensitive,
                 "can_publish": row.can_publish, "expires_at": iso(row.expires_at)} for row in self.db.query(BuglyGrant).order_by(BuglyGrant.id.desc())]

    def revoke(self, grant_id):
        self.internal()
        ensure_admin(self.user)
        row = self.db.get(BuglyGrant, grant_id)
        if not row:
            raise HTTPException(404, "授权不存在")
        self.db.delete(row)
        self.audit("grant.revoke", grant_id)
        self.commit()
        return {"message": "授权已撤销"}

    def create_external_account(self, data):
        self.internal()
        ensure_admin(self.user)
        if self.db.query(User).filter_by(username=data["username"]).first():
            raise HTTPException(409, "用户名已存在")
        user = User(username=data["username"], display_name=data["display_name"] or data["username"],
                    password_hash=User.hash_password(data["password"]), role=UserRole.USER,
                    account_type="external", is_team_member=False, tab_permissions="[]",
                    expires_at=utc_expiry(data.get("expires_at")))
        self.db.add(user)
        self.flush()
        self.audit("account.create", user.id)
        self.commit()
        return {"id": user.id}

    def update_external_account(self, user_id, data):
        self.internal()
        ensure_admin(self.user)
        user = self.db.get(User, user_id)
        if not user or user.account_type != "external":
            raise HTTPException(404, "外部账号不存在")
        user.is_active, user.expires_at = data["is_active"], utc_expiry(data.get("expires_at"))
        user.session_token = user.session_token_mobile = None
        self.audit("account.update", user.id)
        self.commit()
        return {"message": "账号状态已更新，原会话已失效"}
