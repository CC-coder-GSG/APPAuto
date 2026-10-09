"""Version 1 exporter compatibility, bounded parsing and field normalization."""
from __future__ import annotations

import csv
import hashlib
import io
import json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import HTTPException

MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_REPORTS = 2000
MAX_TEXT = 600000
csv.field_size_limit(MAX_TEXT * 2)


def fail(message):
    raise HTTPException(status_code=422, detail=message)


def text(value, maximum=MAX_TEXT):
    if value is None:
        return ""
    if isinstance(value, (dict, list, bool)):
        fail("文本字段类型错误")
    value = str(value).strip()
    if len(value) > maximum:
        fail(f"字段长度超过 {maximum} 字符")
    return value


def identifier(value):
    value = text(value, 100).lstrip("#").strip()
    return value or None


def source_zone(value):
    try:
        return ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError):
        fail("来源时区无效，请使用 Asia/Shanghai 等 IANA 时区")


def timestamp(value, zone="Asia/Shanghai"):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=source_zone(zone))
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def utc_expiry(value):
    if value is None:
        return None
    if value.tzinfo is None:
        fail("有效期必须包含时区")
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def digest(payload):
    content = {key: value for key, value in payload.items()
               if key not in {"collectedAt", "captureDiagnostics", "visibleTabs"}}
    return hashlib.sha256(json.dumps(content, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def merge_payload(old, new):
    """Missing/empty capture fields do not erase richer previous captures."""
    result = dict(old)
    for key, value in new.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = merge_payload(result[key], value)
        elif value is not None and value != "":
            result[key] = value
    return result


def normalize_report(report):
    if not isinstance(report, dict):
        fail("上报记录必须是对象")
    result = {}
    for key in ("listFields", "detailFields", "captureDiagnostics", "visibleTabs"):
        if key in report:
            if not isinstance(report[key], dict):
                fail(f"{key} 必须是对象")
            # Keep diagnostic maps; list/detail values must be simple display text.
            if key in {"listFields", "detailFields"}:
                result[key] = {text(k, 200): text(v, 20000) for k, v in report[key].items()}
            else:
                result[key] = report[key]
    for key in ("pageUrl", "collectedAt", "rawDetailText"):
        if key in report:
            result[key] = text(report[key], MAX_TEXT if key == "rawDetailText" else 2000)
    result["reportId"] = identifier(report.get("reportId"))
    if "stack" in report:
        stack = report["stack"]
        if not isinstance(stack, dict):
            fail("stack 必须是对象")
        result["stack"] = {"text": text(stack.get("text")), "mode": text(stack.get("mode", "fallback"), 30)}
        for key in ("frameCount", "nativeFrameCount", "javaFrameCount"):
            if key in stack:
                try:
                    count = int(stack[key])
                except (ValueError, TypeError):
                    fail(f"{key} 必须是整数")
                if not 0 <= count <= MAX_TEXT:
                    fail(f"{key} 超出范围")
                result["stack"][key] = count
    return result


def report_columns(payload, zone):
    detail, listing = payload.get("detailFields", {}), payload.get("listFields", {})
    def field(*keys):
        for fields in (detail, listing):
            compact = {str(k).replace(" ", ""): v for k, v in fields.items()}
            for key in keys:
                if compact.get(key):
                    return text(compact[key], 255)
        return ""
    raw_time = field("发生时间", "异常上报时间", "上报时间")
    stack = payload.get("stack", {})
    return dict(app_version=field("应用版本", "版本"), device=field("设备机型", "设备"),
                os_version=field("系统版本"), occurred_at_raw=raw_time,
                occurred_at=timestamp(raw_time, zone), collected_at=timestamp(payload.get("collectedAt"), zone),
                stack_text=stack.get("text", ""), stack_mode=stack.get("mode", "manual"),
                frame_count=stack.get("frameCount", 0))


def parse_file(filename, data, assigned_issue=None):
    if len(data) > MAX_FILE_BYTES:
        fail("单文件不能超过 20 MB")
    try:
        decoded = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        fail("请上传 UTF-8 编码的 JSON 或 CSV 文件")
    diagnostics = {"failures": [], "warnings": []}
    try:
        if filename.lower().endswith(".json"):
            envelope = json.loads(decoded)
            if not isinstance(envelope, dict) or envelope.get("schemaVersion") != 1:
                fail("仅支持当前插件 schemaVersion=1 的 JSON")
            reports = envelope.get("reports")
            if not isinstance(reports, list):
                fail("reports 必须是数组")
            issue = envelope.get("issue", {})
            if not isinstance(issue, dict):
                fail("issue 必须是对象")
            diagnostics.update({key: envelope.get(key) for key in (
                "startedAt", "finishedAt", "totalDiscovered", "totalSucceeded", "totalFailed", "failures")})
            if "failures" not in envelope:
                diagnostics["failures"] = []
            if not isinstance(diagnostics.get("failures"), list):
                fail("failures 必须是数组")
            if envelope.get("totalSucceeded") != len(reports):
                diagnostics["warnings"].append("声明成功数与 reports 长度不一致")
            rows = []
            for report in reports:
                if isinstance(report, dict):
                    identity = report.get("issue") or issue
                    rows.append((identity, report))
                else:
                    rows.append((issue, report))
        elif filename.lower().endswith(".csv"):
            reader = csv.DictReader(io.StringIO(decoded, newline=""))
            if not reader.fieldnames or not {"issueId", "reportId", "stack"}.issubset(reader.fieldnames):
                fail("CSV 缺少 issueId、reportId 或 stack 列，请使用插件导出的文件")
            rows = []
            for row in reader:
                if len(rows) >= MAX_REPORTS:
                    fail("单批次不能超过 2000 条上报")
                report = {"reportId": row.get("reportId"), "collectedAt": row.get("collectedAt"),
                          "pageUrl": row.get("pageUrl"),
                          "listFields": {k[3:]: v for k, v in row.items() if k and k.startswith("列表.")},
                          "detailFields": {k[3:]: v for k, v in row.items() if k and k.startswith("详情.")},
                          "stack": {"text": row.get("stack"), "mode": row.get("stackMode") or "fallback",
                                    "frameCount": row.get("frameCount") or 0}}
                rows.append(({"issueId": row.get("issueId"), "title": row.get("issueTitle")}, report))
            diagnostics["warnings"].append("CSV 不包含完整原始文本、诊断与采集失败信息")
        else:
            fail("仅支持 .json 或 .csv 文件")
    except (ValueError, csv.Error, RecursionError) as exc:
        fail(f"文件解析失败：{str(exc)[:180]}")
    if len(rows) > MAX_REPORTS:
        fail("单批次不能超过 2000 条上报")
    items = []
    for index, (issue, report) in enumerate(rows, 1):
        item = {"row": index}
        try:
            if not isinstance(issue, dict):
                fail("问题信息必须是对象")
            source_issue_id = identifier(issue.get("issueId"))
            if not source_issue_id and not assigned_issue:
                fail("未识别问题编号，请选择归属问题后重试")
            payload = normalize_report(report)
            if not payload["reportId"]:
                fail("导入记录缺少上报 ID；无编号记录请使用手动新增")
            item.update(source_issue_id=source_issue_id, title=text(issue.get("title") or f"Bugly #{source_issue_id}", 500),
                        payload=payload, warnings=[])
            stack = payload.get("stack", {})
            if not stack.get("text") or stack.get("mode") == "fallback":
                item["warnings"].append("堆栈为空或仅有兜底结果")
            if payload.get("captureDiagnostics", {}).get("domWaitTimedOut"):
                item["warnings"].append("采集等待超时")
        except HTTPException as exc:
            item["error"] = exc.detail
        items.append(item)
    return items, diagnostics
