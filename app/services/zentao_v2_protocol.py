"""Zentao v2 wire contract and URL boundaries (no REST v1 transport)."""
from __future__ import annotations

import json
import re
from urllib.parse import unquote, urlsplit, urlunsplit


class ZentaoAPIError(Exception):
    def __init__(self, status_code: int, message: str, *, outcome_unknown: bool = False):
        self.status_code = status_code
        self.message = message
        # A write may have reached the server: never replay via another transport.
        self.outcome_unknown = outcome_unknown
        super().__init__(f"ZentaoAPI {status_code}: {message}")


def normalize_base_url(base_url: str) -> str:
    parts = urlsplit(base_url.strip())
    if (parts.scheme not in {"http", "https"} or not parts.hostname
            or parts.username or parts.password or parts.query or parts.fragment):
        raise ZentaoAPIError(400, "Invalid Zentao base URL")
    path = parts.path.rstrip("/")
    if path.endswith("/api.php/v2"):
        path = path[:-len("/api.php/v2")]
    decoded = path
    for _ in range(4):
        decoded = unquote(decoded)
    decoded = decoded.lower()
    if "api.php" in decoded or "\\" in decoded or any(p in {".", ".."} for p in decoded.split("/")):
        raise ZentaoAPIError(400, "Use the Zentao site URL, without an old API path")
    return urlunsplit((parts.scheme, parts.netloc, path, "", ""))


def relative_path(path: str, *, page: bool = False) -> str:
    decoded = path
    for _ in range(4):
        decoded = unquote(decoded)
    parts = urlsplit(decoded)
    value = parts.path.lstrip("/")
    if (parts.scheme or parts.netloc or parts.query or parts.fragment or "\\" in decoded
            or any(p in {".", ".."} for p in value.split("/"))
            or "api.php" in value.lower() or ":" in value or "\x00" in decoded):
        raise ZentaoAPIError(400, "Invalid relative Zentao path")
    if page:
        if not re.fullmatch(r"[A-Za-z0-9_-]+\.[A-Za-z0-9]+", value):
            raise ZentaoAPIError(400, "Invalid Zentao compatibility page")
    elif not re.fullmatch(r"[A-Za-z0-9_/-]+", value):
        raise ZentaoAPIError(400, "Invalid Zentao v2 resource path")
    return value


def v2_params(path: str, params: dict | None) -> dict:
    """Translate internal pagination arguments at the single wire boundary."""
    result = dict(params or {})
    if "limit" in result:
        result.setdefault("recPerPage", result.pop("limit"))
    if "page" in result:
        result.setdefault("pageID", result.pop("page"))
    collection = path.rsplit("/", 1)[-1]
    if collection in {"projects", "products", "bugs", "testcases", "stories", "requirements", "epics"}:
        if "status" in result:
            result.setdefault("browseType", result.pop("status"))
        result.setdefault("browseType", "all")
    elif collection in {"tasks", "executions"}:
        result.setdefault("status", "all")
        if ((collection == 'executions' and path.startswith('projects/'))
                or (collection == 'tasks' and path.startswith('executions/'))):
            # The official handbook calls this status; the official MCP
            # OpenAPI calls it browseType. Send the same filter for both,
            # IPD 5.6 execution task lists also require browseType to include
            # closed tasks, even though the catalogue documents status.
            result.setdefault('browseType', result['status'])
    return result


def decode_v2(response, *, write: bool = False) -> dict:
    status = response.status_code
    if not 200 <= status < 300:
        # Do not expose upstream HTML, credentials or SQL diagnostics to callers.
        raise ZentaoAPIError(status, f"Zentao HTTP {status}",
                             outcome_unknown=write and (status >= 500 or status in {408, 504}))
    try:
        payload = json.loads((response.text or "").lstrip("\ufeff").strip())
    except (ValueError, TypeError) as exc:
        raise ZentaoAPIError(502, "Zentao returned invalid/empty JSON; verify the operation result",
                             outcome_unknown=write) from exc
    if not isinstance(payload, dict):
        raise ZentaoAPIError(502, "Zentao returned an invalid v2 envelope", outcome_unknown=write)
    if payload.get("status") != "success" or payload.get("error") or payload.get("errors"):
        rejected = any(payload.get(key) in ("fail", "failed", "error")
                       for key in ("status", "result"))
        code = payload.get("code") or payload.get("errcode")
        try:
            code = int(code)
        except (TypeError, ValueError):
            code = 422 if rejected else 502
        if code not in {400, 401, 403, 404, 409, 422}:
            code = 422
        message = {401: '禅道认证已失效', 403: '禅道拒绝访问，请检查当前账号权限',
                   404: '禅道对象或接口不存在'}.get(code, '禅道校验未通过，请检查请求字段')
        # Show field names, never upstream diagnostics or credential values.
        errors = payload.get('errors') or payload.get('error') or payload.get('message')
        fields = {'title', 'name', 'system', 'product', 'productID', 'executionID', 'builder', 'date',
                  'steps', 'expects', 'assignedTo', 'openedBuild', 'realStarted', 'finishedDate', 'currentConsumed',
                  'estStarted', 'deadline'}
        invalid = sorted(fields.intersection(errors)) if isinstance(errors, dict) else []
        if invalid:
            message += '：' + ', '.join(invalid)
        raise ZentaoAPIError(code, message,
                             outcome_unknown=write and not rejected)
    return payload


RESOURCE_KEYS = {
    "bugs": "bug", "stories": "story", "testcases": "testcase", "tasks": "task",
    "executions": "execution", "users": "user", "products": "product",
    "projects": "project", "requirements": "requirement", "epics": "epic",
}


def unwrap_resource(path: str, payload: dict) -> dict:
    bits = path.split("/")
    if len(bits) == 2 and bits[1].isdigit() and bits[0] in RESOURCE_KEYS:
        key = RESOURCE_KEYS[bits[0]]
        resource = payload.get(key)
        if not isinstance(resource, dict) or str(resource.get("id")) != bits[1]:
            raise ZentaoAPIError(502, f"Zentao v2 response has no {key} resource")
        return resource
    # Collection and action envelopes retain their keys for business services.
    return payload
