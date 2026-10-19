"""
Zentao API HTTP client service.

Provides a v2 REST client with explicitly retained page/form compatibility.
All requests are made with the token belonging to the calling user.

Usage:
    client = ZentaoClient(base_url="http://192.168.2.148:81/zentao", token="xxx")
    bug = client.get("bugs/29875")
    story = client.get("stories/5604")
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

import httpx

from app.services.zentao_v2_protocol import (
    ZentaoAPIError, decode_v2, normalize_base_url, relative_path, unwrap_resource, v2_params,
)

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 15.0


def _coerce_int_str(value: Any) -> str | None:
    try:
        text = str(int(value))
    except (TypeError, ValueError):
        text = str(value or "").strip()
    return text or None


def _extract_build_ids(raw: Any) -> list[str]:
    build_ids: list[str] = []
    if raw is None:
        return build_ids
    if isinstance(raw, dict):
        if "id" in raw:
            bid = _coerce_int_str(raw.get("id"))
            if bid:
                build_ids.append(bid)
        else:
            for key, value in raw.items():
                key_id = _coerce_int_str(key)
                if key_id:
                    build_ids.append(key_id)
                    continue
                build_ids.extend(_extract_build_ids(value))
        return build_ids
    if isinstance(raw, (list, tuple, set)):
        for item in raw:
            build_ids.extend(_extract_build_ids(item))
        return build_ids
    bid = _coerce_int_str(raw)
    return [bid] if bid else []


def _loads_lenient(text: str) -> Any:
    """
    json.loads that tolerates a leading UTF-8 BOM (﻿) and surrounding
    whitespace.

    某些禅道 IPD 部署在响应正文前会带一个 UTF-8 BOM（渲染成 ﻿），标准
    json.loads / httpx 的 .json() 会直接抛 "Expecting value: line 1
    column 1 (char 0)"，导致被误判成"非 JSON 响应"。这里统一先剥掉 BOM
    再解析。
    """
    return json.loads(text.strip("﻿ \t\r\n"))


def _parse_write_response(resp: "httpx.Response") -> dict | list:
    """Only an explicit v2 success envelope is a successful REST write."""
    return decode_v2(resp, write=True)


class ZentaoClient:
    """
    Authenticated Zentao v2 API client; no REST v1 transport exists.

    base_url: e.g. "http://192.168.2.148:81/zentao"
    token: Zentao API token (short-lived, managed by zentao_auth_service)
    """

    def __init__(self, base_url: str, token: str, *, account: str = "", web_login=None):
        self.base_url = normalize_base_url(base_url)
        self.token = token
        self.account = account
        self.web_login = web_login
        self._api_base = f"{self.base_url}/api.php/v2"

    @classmethod
    def from_binding(cls, binding, token: str):
        from app.services.zentao_auth_service import decrypt_password
        from app.services.zentao_web_session import ZentaoWebLogin
        account = binding.zentao_account or ""
        password = decrypt_password(binding.zentao_password_ciphertext or "")
        login = ZentaoWebLogin(binding.base_url, account, password) if password else None
        return cls(binding.base_url, token, account=account, web_login=login)

    def _headers(self) -> dict[str, str]:
        return {
            "Token": self.token,
            "Content-Type": "application/json",
        }

    def _request(self, method: str, path: str, *, params=None, body=None):
        path = relative_path(path)
        kwargs = {"headers": self._headers(), "timeout": _DEFAULT_TIMEOUT, "follow_redirects": False}
        if method == "get":
            kwargs["params"] = v2_params(path, params)
        elif method in {"post", "put"}:
            kwargs["json"] = body or {}
        try:
            resp = getattr(httpx, method)(f"{self._api_base}/{path}", **kwargs)
        except Exception as exc:
            raise ZentaoAPIError(0, "禅道请求未取得响应，请核实操作结果", outcome_unknown=method != "get") from exc
        if method == "get" and resp.status_code == 404:
            return None
        return decode_v2(resp, write=method != "get")

    def get(self, path: str, params: dict | None = None) -> dict | list | None:
        path = relative_path(path)
        # Legacy internal accessor, never an outbound /v2/user guess.
        if path == "user":
            if self.account:
                return {"profile": {"account": self.account}}
            page = self.get_page("my-index.json") or {}
            person = page.get("user") or page.get("profile")
            if not isinstance(person, dict) or not person.get("account"):
                raise ZentaoAPIError(502, "无法确认当前禅道账号")
            return {"profile": person}
        match = re.fullmatch(r"builds/(\d+)(/bugs)?", path)
        if match:
            if match.group(2):
                page = self.get_page(f"build-view-{match[1]}.json") or {}
                rows = page.get("bugs")
                if not isinstance(rows, (dict, list)):
                    raise ZentaoAPIError(502, "禅道构建页面未返回 Bug 列表")
                rows = list(rows.values()) if isinstance(rows, dict) else rows
                pager = page.get('pager') if isinstance(page.get('pager'), dict) else {}
                if pager.get('recTotal') is not None and int(pager['recTotal']) > len(rows):
                    raise ZentaoAPIError(502, '禅道构建页面 Bug 列表不完整，未标记同步完成')
                size = max(1, int((params or {}).get('limit', 100)))
                offset = (max(1, int((params or {}).get('page', 1))) - 1) * size
                return {"bugs": rows[offset:offset + size], 'total': len(rows)}
            return self.get_build(int(match[1]))
        payload = self._request("get", path, params=params)
        if payload is None:
            return None
        return unwrap_resource(path, payload)

    def post(self, path: str, body: dict | None = None) -> dict | list | None:
        return self._request("post", path, body=body)

    def put(self, path: str, body: dict | None = None) -> dict | list | None:
        return self._request("put", path, body=body)

    def delete(self, path: str) -> dict | None:
        return self._request("delete", path)

    @staticmethod
    def _created(result, label: str) -> dict:
        try:
            object_id = int(result.get("id")) if isinstance(result, dict) else 0
        except (TypeError, ValueError):
            object_id = 0
        if object_id <= 0:
            raise ZentaoAPIError(502, f"禅道创建{label}未返回编号，结果待核实", outcome_unknown=True)
        return {**result, "id": object_id}

    def _resource(self, collection: str, object_id: int, page_module: str) -> dict | None:
        """Retain read-only page recovery for documented v2 detail gaps."""
        try:
            obj = self.get(f"{collection}/{int(object_id)}")
            if isinstance(obj, dict) and str(obj.get("id")) == str(object_id):
                return obj
        except ZentaoAPIError as exc:
            if exc.status_code not in {404, 500, 501, 502}:
                raise
        page = self.get_page(f"{page_module}-view-{int(object_id)}.json")
        obj = page.get(page_module) if isinstance(page, dict) else None
        if isinstance(obj, dict) and str(obj.get("id")) == str(object_id):
            # Actions live at page level on some IPD versions.
            if "actions" in page and "actions" not in obj:
                obj = {**obj, "actions": page["actions"]}
            return obj
        raise ZentaoAPIError(502, f"无法通过 v2 或页面核对禅道 {page_module} #{object_id}")

    def _list_all(self, path: str, key: str, limit: int = 500, params: dict | None = None) -> list[dict]:
        limit = max(1, min(int(limit), 1000))
        rows: list[dict] = []
        seen: set[str] = set()
        expected_total = None
        for page in range(1, 1001):
            data = self.get(path, {**(params or {}), "limit": limit, "page": page})
            bucket = data.get(key) if isinstance(data, dict) else None
            if not isinstance(bucket, list):
                raise ZentaoAPIError(502, f"禅道列表缺少 {key}，同步未完成")
            if not bucket:
                if expected_total is not None and len(rows) < expected_total:
                    raise ZentaoAPIError(502, '禅道列表提前结束，同步完整性待核实')
                return rows
            added = 0
            for item in bucket:
                if not isinstance(item, dict) or not item.get("id"):
                    raise ZentaoAPIError(502, f"禅道 {key} 返回无编号记录")
                oid = str(item["id"])
                if oid not in seen:
                    rows.append(item)
                    seen.add(oid)
                    added += 1
            pager = data.get("pager") if isinstance(data.get("pager"), dict) else {}
            total = data.get("total", pager.get("recTotal"))
            if total is not None:
                try:
                    expected_total = int(total)
                    if len(rows) >= int(total):
                        return rows
                except (TypeError, ValueError):
                    raise ZentaoAPIError(502, "禅道分页总数无效")
            if not added:
                raise ZentaoAPIError(502, "禅道重复返回同一页，同步完整性待核实")
            if len(bucket) < limit and total is None:
                return rows
        raise ZentaoAPIError(502, "禅道列表超出分页上限，同步未完成")

    def _build_system(self, product_id: int) -> int:
        if not product_id:
            raise ZentaoAPIError(422, "缺少构建所属产品，未提交写入")
        data = self.get(f"products/{product_id}/systems")
        systems = data.get("systems") if isinstance(data, dict) else None
        if isinstance(systems, list):
            try:
                ids = {int(s["id"]) for s in systems if isinstance(s, dict) and s.get("id")}
            except (ValueError, TypeError) as exc:
                raise ZentaoAPIError(502, '禅道应用列表包含无效编号') from exc
            from app.core.config import settings
            try:
                mapping = json.loads(settings.zentao_build_system_mapping_json or '{}')
                selected = mapping.get(str(product_id))
                selected = int(selected) if selected is not None else None
            except (ValueError, TypeError, AttributeError) as exc:
                raise ZentaoAPIError(422, '禅道构建应用映射 JSON 配置无效') from exc
            if selected is not None:
                if selected not in ids:
                    raise ZentaoAPIError(422, '配置的禅道应用不属于该产品或当前账号不可访问')
                return selected
            if len(ids) == 1:
                return ids.pop()
        raise ZentaoAPIError(422, "无法唯一确定禅道产品应用，请配置 APP_ZENTAO_BUILD_SYSTEM_MAPPING_JSON 中的 system 编号")

    @staticmethod
    def _field_value(key, value):
        if isinstance(value, dict):
            value = value.get('account') if key == 'assignedTo' else value.get('id')
        if isinstance(value, list):
            return ','.join(str(v) for v in value)
        return '' if value is None else str(value)

    def _complete_created_fields(self, result, module: str, desired: dict, form: dict, getter):
        """Complete documented v2 gaps on the known ID; never repeat creation.

        Even a 401/timeout in this second step must return the created ID, so
        callers cannot mistake it for a failed POST and create another object.
        """
        desired = {k: v for k, v in desired.items() if v not in (None, '', [], 0)}
        if not desired:
            return result
        object_id = result['id']
        observed = {}
        try:
            observed = getter(object_id) or {}
            missing = [key for key, value in desired.items()
                       if self._field_value(key, observed.get(key)) != self._field_value(key, value)]
            if missing:
                # Keep values not edited by this request, including owner and
                # product references, while excluding unrelated page metadata.
                keys = {'title', 'product', 'branch', 'module', 'story', 'project', 'execution', 'pri', 'type',
                        'severity', 'assignedTo', 'openedBuild', 'steps', 'expects', 'stepType', 'precondition',
                        'keywords', 'stage', 'os', 'browser', 'color', 'deadline', 'mailto'}
                snapshot = {k: v for k, v in observed.items() if k in keys and v is not None}
                for key, value in list(snapshot.items()):
                    if isinstance(value, dict):
                        snapshot[key] = value.get('account') if key == 'assignedTo' else value.get('id')
                fields = {**snapshot, **form, **desired}
                if isinstance(fields.get('stage'), str):
                    fields['stage'] = [s for s in fields['stage'].split(',') if s]
                self.post_page(f'{module}-edit-{object_id}.json', fields)
                observed = getter(object_id) or {}
                missing = [key for key, value in desired.items()
                           if self._field_value(key, observed.get(key)) != self._field_value(key, value)]
                if missing:
                    raise ZentaoAPIError(502, '补充字段回读未一致', outcome_unknown=True)
        except Exception:
            logger.warning('Created %s #%s; supplemental fields pending verification: %s',
                           module, object_id, ','.join(desired))
            result = {**result, 'compat_warning': f'已创建 #{object_id}，补充字段待核实：' + ', '.join(desired)}
        return {**result, 'verified_fields': {key: self._field_value(key, observed.get(key)) for key in desired}}

    def get_page(self, path: str, params: dict | None = None) -> dict | None:
        """Keep working Token page reads; use the same user's Cookie on gaps."""
        path = relative_path(path, page=True)
        result = self._get_page_with_token(path, params)
        if (isinstance(result, dict) and result
                and result.get('status') != 'fail' and result.get('result') != 'fail'
                and not result.get('loginExpired') and not result.get('denyType')
                and result.get('title') != '用户登录'):
            return result
        if self.web_login is not None:
            from app.services.zentao_web_session import request_page_via_web
            try:
                return request_page_via_web(self.web_login, path, params=params)
            except Exception:
                logger.warning('Zentao page unavailable via Token and same-user Cookie: %s', path)
        return None

    def _get_page_with_token(self, path: str, params: dict | None = None) -> dict | None:
        """
        GET a Zentao *page* JSON (not the v2 API), e.g. /bug-activate-{id}.json.
        These are the web-app page data endpoints that include users/builds metadata.
        """
        path = relative_path(path, page=True)
        url = f"{self.base_url}/{path}"
        headers = {
            "Token": self.token,
            "Accept": "application/json",
        }
        try:
            resp = httpx.get(url, params=params, headers=headers, timeout=_DEFAULT_TIMEOUT, follow_redirects=False)
            if resp.status_code in (401, 403, 404):
                return None
            if resp.status_code != 200:
                return None
            try:
                result = _loads_lenient(resp.text or "")
            except Exception:
                result = resp.json()
            # Zentao page JSON often wraps the actual payload inside:
            # {"status":"success","data":"{\"users\":{...},...}"}
            if isinstance(result, dict) and isinstance(result.get('data'), dict):
                return result['data']
            if isinstance(result, dict) and isinstance(result.get("data"), str):
                try:
                    decoded = _loads_lenient(result["data"])
                    if isinstance(decoded, dict):
                        return decoded
                except Exception:
                    logger.debug("ZentaoClient.get_page: nested data decode failed path=%s", path, exc_info=True)
            return result
        except Exception as e:
            logger.warning("ZentaoClient.get_page error path=%s: %s", path, e)
            return None

    def post_page(self, path: str, data: dict | None = None) -> dict | None:
        """提交官方 v2 尚未覆盖的页面表单。

        已绑定密码时使用同账号网页会话，仅明确成功的 JSON 响应算成功。
        """
        path = relative_path(path, page=True)
        if self.web_login is not None:
            from app.services.zentao_web_session import request_page_via_web
            return request_page_via_web(self.web_login, path, data=data or {})
        url = f"{self.base_url}/{path}"
        headers = {
            "Token": self.token,
            "Accept": "application/json, text/html;q=0.8, */*;q=0.5",
        }
        try:
            resp = httpx.post(url, data=(data or {}), headers=headers, timeout=_DEFAULT_TIMEOUT, follow_redirects=False)
            if resp.status_code not in (200, 201):
                raise ZentaoAPIError(resp.status_code, "禅道表单请求失败", outcome_unknown=resp.status_code >= 500)
            text = (resp.text or "").strip()
            if not text:
                raise ZentaoAPIError(502, "禅道表单返回空响应，结果待核实", outcome_unknown=True)
            result = _loads_lenient(text)
            if not isinstance(result, dict) or (result.get("result") != "success" and result.get("status") != "success"):
                raise ZentaoAPIError(422, "禅道表单未确认成功")
            return result
        except ZentaoAPIError:
            raise
        except Exception as e:
            logger.warning("ZentaoClient.post_page error path=%s: %s", path, e)
            raise ZentaoAPIError(0, "禅道表单结果待核实", outcome_unknown=True) from e

    def get_page_text(self, path: str, params: dict | None = None) -> str | None:
        """
        Fetch raw HTML/text from a Zentao page endpoint using Token auth.

        Some IPD deployments return diagnostic HTML/JS instead of JSON when the
        token lacks page-level access. We use this as a best-effort probe when a
        write endpoint returns an empty success response but the created object
        cannot be found afterwards.
        """
        path = relative_path(path, page=True)
        url = f"{self.base_url}/{path}"
        headers = {
            "Token": self.token,
            "Accept": "text/html,application/json;q=0.9,*/*;q=0.8",
        }
        try:
            resp = httpx.get(url, params=params, headers=headers, timeout=_DEFAULT_TIMEOUT, follow_redirects=False)
            if resp.status_code != 200:
                return None
            return resp.text or ""
        except Exception as e:
            logger.warning("ZentaoClient.get_page_text error path=%s: %s", path, e)
            return None

    def get_file(self, file_id: int):
        """Retained authenticated file resource; no guessed v2 or v1 download."""
        if int(file_id) <= 0:
            raise ZentaoAPIError(400, '附件编号无效')
        path = f"file-read-{int(file_id)}.html"
        if self.web_login is not None:
            from app.services.zentao_web_session import fetch_file_via_web
            return fetch_file_via_web(self.web_login, file_id)
        try:
            resp = httpx.get(f"{self.base_url}/{path}", headers={"Token": self.token},
                             timeout=_DEFAULT_TIMEOUT, follow_redirects=False)
        except Exception as exc:
            raise ZentaoAPIError(0, "禅道附件获取失败") from exc
        if resp.status_code != 200 or "text/html" in resp.headers.get("content-type", "").lower():
            raise ZentaoAPIError(502, "禅道附件未返回文件内容")
        return resp

    def probe_bug_create_access(self, product_id: int, execution_id: int = 0) -> dict[str, Any]:
        """
        Probe whether the current token can access the Zentao create-bug page.

        Returns a normalized object:
          {
            "ok": bool | None,
            "reason": "ok" | "access_denied" | "login_required" | "unavailable",
            "path": "...",
            "preview": "..."
          }
        """
        json_candidates = [f"bug-create-{product_id}-{execution_id}.json"]
        if execution_id:
            json_candidates.append(f"bug-create-{product_id}-0.json")
        for path in json_candidates:
            payload = self.get_page(path)
            if isinstance(payload, dict) and payload:
                if payload.get("loginExpired") is True:
                    return {"ok": False, "reason": "login_required", "path": path, "preview": "loginExpired"}
                if payload.get("title") == "用户登录":
                    return {"ok": False, "reason": "login_required", "path": path, "preview": "用户登录"}
                return {"ok": True, "reason": "ok", "path": path, "preview": str(list(payload.keys())[:10])}

        html_candidates = [f"bug-create-{product_id}-{execution_id}.html"]
        if execution_id:
            html_candidates.append(f"bug-create-{product_id}-0.html")
        for path in html_candidates:
            text = self.get_page_text(path)
            if text is None:
                continue
            normalized = text.lstrip("\ufeff").strip()
            preview = normalized[:200]
            if "您无权访问该产品" in normalized:
                return {"ok": False, "reason": "access_denied", "path": path, "preview": preview}
            if "用户登录" in normalized and "login" in normalized.lower():
                return {"ok": False, "reason": "login_required", "path": path, "preview": preview}
            return {"ok": True, "reason": "ok", "path": path, "preview": preview}
        return {"ok": None, "reason": "unavailable", "path": "", "preview": ""}

    # ------------------------------------------------------------------
    # Convenience accessors for common objects
    # ------------------------------------------------------------------

    def get_bug(self, bug_id: int) -> dict | None:
        return self._resource('bugs', bug_id, 'bug')

    def get_bug_with_fallback(self, bug_id: int) -> dict | None:
        """
        Return a bug's raw dict, falling back to the page JSON
        (`bug-view-{id}.json`) when the v2 API yields no usable body.

        401 交给调用方刷新 Token。缺少操作历史时从保留页面补充，
        页面字段不会覆盖 v2 已返回的字段。
        """
        raw: dict | list | None = None
        try:
            raw = self.get_bug(bug_id)
        except ZentaoAPIError as exc:
            if exc.status_code == 401:
                raise
            logger.info("get_bug_with_fallback: v2 失败 bug_id=%s status=%s，回退页面 JSON",
                        bug_id, exc.status_code)
            raw = None
        if raw and 'actions' in raw:
            return raw

        try:
            page = self.get_page(f"bug-view-{bug_id}.json")
        except Exception:
            if not raw:
                raise
            logger.warning('Bug #%s page history unavailable; v2 detail remains readable', bug_id)
            return raw
        if isinstance(page, dict):
            bug = page.get("bug") or page
            if not isinstance(bug, dict) or str(bug.get('id')) != str(bug_id):
                return raw or None
            if isinstance(raw, dict):
                bug = {**bug, **raw}
            if isinstance(bug, dict) and "actions" not in bug and "actions" in page:
                bug = {**bug, "actions": page["actions"]}
            return bug
        return raw or None

    def get_story(self, story_id: int) -> dict | None:
        return self._resource('stories', story_id, 'story')

    def get_testcase(self, case_id: int) -> dict | None:
        return self._resource('testcases', case_id, 'testcase')

    def create_testcase(self, product_id: int, payload: dict) -> dict:
        body = {k: v for k, v in payload.items() if k not in {'product', 'keywords', 'stage'}}
        body['productID'] = int(product_id)
        steps = body.get('steps')
        if isinstance(steps, list) and steps and all(isinstance(s, dict) for s in steps):
            body['steps'] = [s.get('desc', s.get('step', '')) for s in steps]
            body['expects'] = [s.get('expect', s.get('expected', '')) for s in steps]
            body['stepType'] = [s.get('type') or 'step' for s in steps]
        result = self._created(self.post('testcases', body), '用例')
        form = {k: v for k, v in body.items() if k != 'productID'}
        form['product'] = product_id
        return self._complete_created_fields(result, 'testcase',
                                             {k: payload[k] for k in ('keywords', 'stage') if k in payload},
                                             form, self.get_testcase)

    def get_create_testcase_meta(self, product_id: int) -> dict | None:
        return self.get_page(f"testcase-create-{product_id}.json")

    def get_build(self, build_id: int) -> dict | None:
        # The official v2 catalogue has no single-build GET. Retain page JSON.
        page = self.get_page(f'build-view-{int(build_id)}.json')
        if not isinstance(page, dict):
            raise ZentaoAPIError(502, '无法读取禅道构建详情')
        build = page.get('build')
        if not isinstance(build, dict) or str(build.get('id')) != str(build_id):
            raise ZentaoAPIError(502, '禅道构建详情缺少匹配的编号')
        return build

    def get_execution(self, execution_id: int) -> dict | None:
        return self._resource('executions', execution_id, 'execution')

    def get_user(self, user_id: int) -> dict | None:
        return self._resource('users', user_id, 'user')

    def list_project_executions(self, project_id: int, limit: int = 200, status: str = 'all') -> list[dict]:
        return self._list_all(f'projects/{project_id}/executions', 'executions', limit, {'status': status})

    def list_execution_stories(self, execution_id: int, limit: int = 500) -> list[dict]:
        return self._list_all(f'executions/{execution_id}/stories', 'stories', limit)

    def list_projects(self, limit: int = 100) -> list[dict]:
        return self._list_all('projects', 'projects', limit)

    def list_execution_builds(self, execution_id: int, limit: int = 500) -> list[dict]:
        return self._list_all(f'executions/{execution_id}/builds', 'builds', limit)

    def list_project_builds(self, project_id: int, limit: int = 500) -> list[dict]:
        return self._list_all(f'projects/{project_id}/builds', 'builds', limit)

    # ------------------------------------------------------------------
    # Build write actions
    # ------------------------------------------------------------------

    def create_execution_build(self, execution_id: int, name: str, *, project_id: int | None = None,
                               product_id: int | None = None, builder: str | None = None,
                               date: str | None = None, desc: str | None = None,
                               system_id: int | None = None) -> dict:
        from app.utils.time_utils import local_now
        if not product_id:
            raise ZentaoAPIError(422, '创建禅道构建需要产品编号')
        builder = builder or self.account
        if not builder:
            raise ZentaoAPIError(422, '创建禅道构建需要构建者账号')
        body = {'executionID': execution_id, 'product': product_id, 'name': name,
                'system': system_id or self._build_system(product_id), 'builder': builder,
                'date': date or local_now().strftime('%Y-%m-%d')}
        if desc is not None:
            body['desc'] = desc
        return self._created(self.post('builds', body), '构建')

    def update_build(self, build_id: int, *, name: str | None = None, execution_id: int | None = None,
                     product_id: int | None = None, desc: str | None = None) -> dict | None:
        if all(v is None for v in (name, execution_id, product_id, desc)):
            return None
        snapshot = self.get_build(build_id) or {}
        keys = ('execution', 'product', 'name', 'system', 'builder', 'date', 'scmPath', 'filePath', 'desc')
        body = {k: snapshot[k] for k in keys if k in snapshot}
        changes = {'name': name, 'execution': execution_id, 'product': product_id, 'desc': desc}
        body.update({k: v for k, v in changes.items() if v is not None})
        for key in ('execution', 'product', 'system'):
            value = body.get(key)
            if isinstance(value, dict):
                value = value.get('id')
            if value is not None:
                body[key] = int(value)
        if isinstance(body.get('builder'), dict):
            body['builder'] = body['builder'].get('account')
        if not body.get('system') or (product_id is not None and str(product_id) != str(snapshot.get('product'))):
            body['system'] = self._build_system(int(body.get('product') or 0))
        for key in ('execution', 'product', 'name', 'system', 'builder', 'date'):
            if not body.get(key):
                raise ZentaoAPIError(422, f'禅道构建详情缺少 {key}，未发送修改请求')
        return self.put(f'builds/{build_id}', body)

    def delete_build(self, build_id: int) -> dict | None:
        """DELETE /v2/builds/{id}"""
        return self.delete(f"builds/{build_id}")

    # ------------------------------------------------------------------
    # Task read + write（禅道任务联动，2026-06-29）
    # 字段/行为见 docs/2026-06-29-禅道任务联动-实现计划.md 第三节附录（已实测）。
    # ------------------------------------------------------------------

    def list_execution_tasks(self, execution_id: int, limit: int = 500) -> list[dict]:
        return self._list_all(f'executions/{execution_id}/tasks', 'tasks', limit, {'status': 'all'})

    def get_task(self, task_id: int) -> dict | None:
        return self._resource('tasks', task_id, 'task')

    def create_execution_task(self, execution_id: int, *, name: str, assigned_to: str | None = None,
                              task_type: str = 'test', story: int | None = None,
                              est_started: str | None = None, deadline: str | None = None,
                              estimate: float | None = None, pri: int = 3, desc: str | None = None) -> dict:
        body = {'executionID': execution_id, 'name': name, 'type': task_type, 'pri': pri}
        values = {'assignedTo': assigned_to, 'story': story, 'estStarted': est_started,
                  'deadline': deadline, 'estimate': estimate, 'desc': desc}
        body.update({k: v for k, v in values.items() if v is not None})
        return self._created(self.post('tasks', body), '任务')

    def update_task(self, task_id: int, data: dict) -> dict | None:
        supported = {'name', 'type', 'assignedTo', 'estStarted', 'deadline', 'pri', 'estimate', 'module', 'story', 'desc'}
        if set(data) <= supported:
            return self.put(f'tasks/{task_id}', data)
        # Parent links and legacy lifecycle repair are not documented v2 fields.
        # Use the existing edit form, preserving all fields that the user did not edit.
        task = self.get_task(task_id)
        if not isinstance(task, dict) or str(task.get('id')) != str(task_id):
            raise ZentaoAPIError(502, '无法核对禅道任务，未提交表单')
        # IPD 5.6 validates the execution on retained edit forms as well.
        form_keys = supported | {'execution', 'parent', 'status', 'left', 'consumed', 'finishedDate', 'realStarted'}
        fields = {k: v for k, v in task.items() if k in form_keys and v is not None}
        for key, value in list(fields.items()):
            if isinstance(value, dict):
                fields[key] = value.get('account') if key == 'assignedTo' else value.get('id')
        fields.update(data)
        return self.post_page(f'task-edit-{task_id}.json', fields)

    def update_task_dates(self, task_id: int, *, est_started: str, deadline: str) -> dict | None:
        """Update only the planned dates; preserve task lifecycle, owner and efforts."""
        return self.put(f"tasks/{task_id}", {"estStarted": est_started, "deadline": deadline})

    def link_task_parent(self, child_task_id: int, parent_task_id: int) -> dict | None:
        result = self.update_task(child_task_id, {'parent': parent_task_id})
        task = self.get_task(child_task_id) or {}
        if str(task.get('parent')) != str(parent_task_id):
            raise ZentaoAPIError(502, '禅道父子任务关联结果待核实', outcome_unknown=True)
        return result

    def reassign_task(self, task_id: int, assigned_to: str) -> dict | None:
        """改派任务负责人。"""
        return self.put(f"tasks/{task_id}", {"assignedTo": assigned_to})

    def start_task(self, task_id: int, *, real_started: str | None = None,
                   left: float | None = None, assigned_to: str | None = None) -> dict | None:
        from app.utils.time_utils import local_now
        body = {'realStarted': real_started or local_now().strftime('%Y-%m-%d %H:%M:%S')}
        if left is not None:
            body['left'] = left
        if assigned_to:
            body['assignedTo'] = assigned_to
        return self.put(f'tasks/{task_id}/start', body)

    def pause_task(self, task_id: int, *, comment: str | None = None) -> dict | None:
        # No documented v2 pause endpoint. Existing callers provide the same-user web action.
        raise ZentaoAPIError(501, '任务暂停使用保留的网页登录方式')

    def finish_task(self, task_id: int, *, current_consumed: float,
                    finished_date: str | None = None, assigned_to: str | None = None) -> dict | None:
        from app.utils.time_utils import local_now
        task = self.get_task(task_id) or {}
        started = task.get('realStarted') or task.get('estStarted')
        if not started:
            raise ZentaoAPIError(422, '无法确认任务实际开始时间，使用保留表单完成')
        body = {'currentConsumed': current_consumed, 'realStarted': started,
                'finishedDate': finished_date or local_now().strftime('%Y-%m-%d %H:%M:%S')}
        if assigned_to:
            body['assignedTo'] = assigned_to
        return self.put(f'tasks/{task_id}/finish', body)

    def restart_task(self, task_id: int, *, consumed: float, left: float,
                     assigned_to: str | None = None) -> dict | None:
        task = self.get_task(task_id) or {}
        status = str(task.get('rawStatus') or task.get('status') or '').lower()
        if status not in {'done', 'closed', 'cancel'}:
            # Resuming a paused task is not assumed equivalent to v2 activate.
            raise ZentaoAPIError(501, '继续暂停任务使用保留的网页登录方式')
        body = {'left': left}
        if assigned_to:
            body['assignedTo'] = assigned_to
        return self.put(f'tasks/{task_id}/activate', body)

    def close_task(self, task_id: int, *, comment: str | None = None) -> dict | None:
        return self.put(f'tasks/{task_id}/close', {'comment': comment or ''})

    def cancel_task(self, task_id: int, *, comment: str | None = None) -> dict | None:
        raise ZentaoAPIError(501, '任务取消使用保留的网页登录方式')

    def list_assignable_users(self, execution_id: int) -> dict[str, str]:
        """该执行可指派用户 {account: realname}。

        保留任务创建页的执行级人员过滤。
        `task-create-{execId}.json` 的 `users`/`members` 字典（value 形如 "C:陈文博"）。
        """
        page = self.get_page(f"task-create-{execution_id}.json") or {}
        # 禅道不同版本：users/members 可能挂在 data 下，也可能直接在页面顶层。
        container = page.get("data") if isinstance(page, dict) else None
        if isinstance(container, str):
            try:
                container = _loads_lenient(container)
            except Exception:
                container = None
        if not isinstance(container, dict):
            container = page if isinstance(page, dict) else {}
        raw = container.get("users") or container.get("members") or {}
        out: dict[str, str] = {}
        if isinstance(raw, dict):
            for account, label in raw.items():
                acc = str(account or "").strip()
                if not acc:
                    continue
                text = str(label or "").strip()
                # value 形如 "C:陈文博" —— 去掉首字母分组前缀
                realname = text.split(":", 1)[1].strip() if ":" in text else text
                out[acc] = realname or acc
        return out

    # ------------------------------------------------------------------
    # Bug write actions
    # ------------------------------------------------------------------

    def close_bug(self, bug_id: int, comment: str = '') -> dict | None:
        return self.put(f'bugs/{bug_id}/close', {'comment': comment})

    def active_bug(self, bug_id: int, assigned_to: str = '', opened_build: list[str] | None = None,
                   comment: str = '') -> dict | None:
        body = {'comment': comment}
        if assigned_to:
            body['assignedTo'] = assigned_to
        if opened_build:
            body['openedBuild'] = opened_build
        return self.put(f'bugs/{bug_id}/activate', body)

    def assign_bug(self, bug_id: int, assigned_to: str, comment: str = '') -> dict | None:
        result = self.post_page(f'bug-assignTo-{bug_id}.json', {'assignedTo': assigned_to, 'comment': comment})
        bug = self.get_bug(bug_id) or {}
        actual = bug.get('assignedTo')
        actual = actual.get('account') if isinstance(actual, dict) else actual
        if actual != assigned_to:
            raise ZentaoAPIError(502, '禅道 Bug 改派结果待核实', outcome_unknown=True)
        return result

    def delete_bug(self, bug_id: int) -> dict | None:
        """DELETE /v2/bugs/{id}"""
        return self.delete(f"bugs/{bug_id}")

    def update_bug(self, bug_id: int, data: dict) -> dict | None:
        """PUT /v2/bugs/{id}"""
        return self.put(f"bugs/{bug_id}", data)

    # ------------------------------------------------------------------
    # Bug action page metadata (users/builds for dropdowns)
    # ------------------------------------------------------------------

    def get_bug_action_meta(self, bug_id: int, action: str = "activate") -> dict | None:
        """
        Fetch page-level JSON for a bug action page (activate / resolve / view / close).
        These pages include a `users` dict suitable for building assignee dropdowns.

        action: "activate" | "resolve" | "view" | "close"
        """
        path = f"bug-{action}-{bug_id}.json"
        return self.get_page(path)

    def create_bug(self, product_id: int, data: dict) -> dict:
        body = {k: v for k, v in data.items() if k not in {'product', 'module', 'assignedTo'}}
        body['productID'] = int(product_id)
        result = self._created(self.post('bugs', body), 'Bug')
        form = {k: v for k, v in body.items() if k != 'productID'}
        form['product'] = product_id
        return self._complete_created_fields(result, 'bug',
                                             {k: data[k] for k in ('module', 'assignedTo') if k in data},
                                             form, self.get_bug)



    def get_execution_context(self, execution_id: int) -> dict:
        """
        Return {product_ids: [...], project_id: int|None} for the given execution.
        Uses GET /v2/executions/{id}.
        """
        try:
            resp = self.get_execution(execution_id)
            if not resp:
                return {"product_ids": [], "project_id": None}
            execution = resp.get("execution") or resp
            if not isinstance(execution, dict):
                return {"product_ids": [], "project_id": None}

            # Extract product IDs
            products_raw = execution.get("products") or []
            product_ids: list[int] = []
            if isinstance(products_raw, list):
                for p in products_raw:
                    if isinstance(p, dict):
                        pid = p.get("id")
                        if pid:
                            product_ids.append(int(pid))
                    elif p:
                        try:
                            product_ids.append(int(p))
                        except (TypeError, ValueError):
                            pass
            elif isinstance(products_raw, dict):
                for pid in products_raw:
                    try:
                        product_ids.append(int(pid))
                    except (TypeError, ValueError):
                        pass

            # Extract project ID
            project_raw = execution.get("project") or {}
            project_id = None
            if isinstance(project_raw, dict):
                project_id = project_raw.get("id")
            elif project_raw:
                try:
                    project_id = int(project_raw)
                except (TypeError, ValueError):
                    pass

            return {"product_ids": product_ids, "project_id": project_id}
        except Exception:
            return {"product_ids": [], "project_id": None}

    def get_execution_build_ids(self, execution_id: int) -> list[str]:
        """
        Return the build IDs bound to a given execution.

        Primary source: GET /v2/executions/{id}/builds — the same endpoint
        used by overall_test_service and zentao_version_sync_service, so
        we know it is the reliable shape on this Zentao version.

        Fallback (for older Zentao builds): inspect the execution detail
        payload for `build/builds/openedBuild/openedBuilds` keys.
        """
        try:
            rows = self.list_execution_builds(execution_id)
            return list(dict.fromkeys(str(row['id']) for row in rows))
        except ZentaoAPIError as exc:
            if exc.status_code not in {404, 501}:
                raise

        try:
            resp = self.get_execution(execution_id)
            if not resp:
                return []
            execution = resp.get("execution") or resp
            if not isinstance(execution, dict):
                return []
            build_ids: list[str] = []
            for key in ("build", "builds", "openedBuild", "openedBuilds"):
                build_ids.extend(_extract_build_ids(execution.get(key)))
            return list(dict.fromkeys([bid for bid in build_ids if bid]))
        except Exception:
            return []

    def upload_file(self, file_bytes: bytes, filename: str, content_type: str = 'application/octet-stream',
                     object_type: str = 'bug', object_id: int = 0) -> dict:
        safe_name = self._ensure_filename_extension(filename, content_type)
        try:
            resp = httpx.post(f'{self._api_base}/files', headers={'Token': self.token},
                              files={'file': (safe_name, file_bytes, content_type)},
                              data={'objectType': object_type, 'objectID': str(object_id)},
                              timeout=30.0, follow_redirects=False)
        except Exception as exc:
            raise ZentaoAPIError(0, '禅道文件上传结果待核实', outcome_unknown=True) from exc
        payload = decode_v2(resp, write=True)
        entry = self._extract_uploaded_entry(payload)
        if not entry or not entry.get('id'):
            raise ZentaoAPIError(502, '禅道上传未返回文件编号，结果待核实', outcome_unknown=True)
        return {'id': int(entry['id']), 'title': entry.get('title') or safe_name,
                'pathname': entry.get('pathname') or '', 'url': entry.get('url') or '',
                'extension': entry.get('extension') or (safe_name.rsplit('.', 1)[-1] if '.' in safe_name else '')}

    @staticmethod
    def _ensure_filename_extension(filename: str, content_type: str) -> str:
        name = (filename or "").strip() or "file"
        if "." in name and not name.endswith("."):
            return name
        mime_ext = {
            "image/png": "png",
            "image/jpeg": "jpg",
            "image/jpg": "jpg",
            "image/gif": "gif",
            "image/webp": "webp",
            "image/bmp": "bmp",
            "image/svg+xml": "svg",
        }
        ext = mime_ext.get((content_type or "").lower().split(";")[0].strip(), "")
        if ext:
            return f"{name.rstrip('.')}.{ext}"
        return name

    @staticmethod
    def _extract_uploaded_entry(payload: Any) -> dict | None:
        """
        Best-effort extraction of the first uploaded-file entry from the
        many response shapes Zentao versions can produce.
        """
        if isinstance(payload, dict):
            if isinstance(payload.get("files"), list) and payload["files"]:
                first = payload["files"][0]
                if isinstance(first, dict):
                    return first
            if isinstance(payload.get("files"), dict) and payload["files"]:
                first = next(iter(payload["files"].values()), None)
                if isinstance(first, dict):
                    return first
            if isinstance(payload.get("data"), list) and payload["data"]:
                first = payload["data"][0]
                if isinstance(first, dict):
                    return first
            if isinstance(payload.get("data"), dict) and payload["data"].get("id"):
                return payload["data"]
            if payload.get("id"):
                return payload
            # Sometimes upload responses wrap under "file"
            if isinstance(payload.get("file"), dict) and payload["file"].get("id"):
                return payload["file"]
        elif isinstance(payload, list) and payload:
            first = payload[0]
            if isinstance(first, dict):
                return first
        return None

    def get_create_bug_meta(self, product_id: int, execution_id: int = 0) -> dict | None:
        """Fetch page-level JSON for the bug creation form (users, builds etc.)."""
        path = f"bug-create-{product_id}-{execution_id}.json"
        meta = self.get_page(path)
        if meta:
            meta["__meta_scope__"] = "execution" if execution_id else "product"
            meta["__meta_path__"] = path
            return meta
        if execution_id:
            path = f"bug-create-{product_id}-0.json"
            meta = self.get_page(path)
            if meta:
                meta["__meta_scope__"] = "product_fallback"
                meta["__meta_path__"] = path
                return meta
        return meta


__all__ = ["ZentaoClient", "ZentaoAPIError"]
