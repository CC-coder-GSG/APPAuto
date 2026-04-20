"""
Zentao API HTTP client service.

Provides a thin, authenticated wrapper around the Zentao v1 REST API.
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


class ZentaoAPIError(Exception):
    def __init__(self, status_code: int, message: str):
        self.status_code = status_code
        self.message = message
        super().__init__(f"ZentaoAPI {status_code}: {message}")


def _parse_write_response(resp: "httpx.Response") -> dict | list:
    """
    Parse body of a POST/PUT response defensively.

    Zentao v1 write endpoints are inconsistent: some return full JSON,
    some return an empty body on success, some return `id=xxx` plain
    text, and PHP warnings can leak HTML with a 200 status.
    """
    text = (resp.text or "").strip()
    if not text:
        return {"message": "success"}
    try:
        return resp.json()
    except ValueError:
        lowered = text.lower()
        if "fatal error" in lowered:
            raise ZentaoAPIError(500, "禅道服务器内部错误（PHP Fatal Error）")
        m = re.search(r'(?:bugid|id)[=:"\s]*(\d+)', text, re.IGNORECASE)
        if m:
            return {"id": int(m.group(1))}
        raise ZentaoAPIError(502, f"禅道返回了非 JSON 响应: {text[:200]}")


class ZentaoClient:
    """
    Authenticated Zentao v1 API client.

    base_url: e.g. "http://192.168.2.148:81/zentao"
    token: Zentao API token (short-lived, managed by zentao_auth_service)
    """

    def __init__(self, base_url: str, token: str):
        self.base_url = base_url.rstrip('/')
        self.token = token
        self._api_base = f"{self.base_url}/api.php/v1"

    def _headers(self) -> dict[str, str]:
        return {
            "Token": self.token,
            "Content-Type": "application/json",
        }

    def get(self, path: str, params: dict | None = None) -> dict | list | None:
        """
        GET /v1/{path}

        Returns parsed JSON body, or raises ZentaoAPIError.
        Returns None if the server returns 404.
        """
        url = f"{self._api_base}/{path.lstrip('/')}"
        try:
            resp = httpx.get(url, params=params, headers=self._headers(), timeout=_DEFAULT_TIMEOUT)
            if resp.status_code == 404:
                return None
            if resp.status_code not in (200, 201):
                raise ZentaoAPIError(resp.status_code, resp.text[:500])
            try:
                return resp.json()
            except Exception:
                body = resp.text or ""
                if "Fatal error" in body or "fatal error" in body:
                    raise ZentaoAPIError(500, "禅道服务器内部错误（PHP Fatal Error），该 Bug 数据可能存在异常，请联系禅道管理员检查。")
                raise ZentaoAPIError(502, f"禅道返回了非 JSON 响应: {body[:200]}")
        except ZentaoAPIError:
            raise
        except Exception as e:
            logger.warning("ZentaoClient.get error path=%s: %s", path, e)
            raise ZentaoAPIError(0, str(e))

    def post(self, path: str, body: dict | None = None) -> dict | list | None:
        """POST /v1/{path}"""
        url = f"{self._api_base}/{path.lstrip('/')}"
        try:
            resp = httpx.post(url, json=body or {}, headers=self._headers(), timeout=_DEFAULT_TIMEOUT)
            if resp.status_code not in (200, 201):
                raise ZentaoAPIError(resp.status_code, resp.text[:500])
            return _parse_write_response(resp)
        except ZentaoAPIError:
            raise
        except Exception as e:
            logger.warning("ZentaoClient.post error path=%s: %s", path, e)
            raise ZentaoAPIError(0, str(e))

    def put(self, path: str, body: dict | None = None) -> dict | list | None:
        """PUT /v1/{path}"""
        url = f"{self._api_base}/{path.lstrip('/')}"
        try:
            resp = httpx.put(url, json=body or {}, headers=self._headers(), timeout=_DEFAULT_TIMEOUT)
            if resp.status_code not in (200, 201):
                raise ZentaoAPIError(resp.status_code, resp.text[:500])
            return _parse_write_response(resp)
        except ZentaoAPIError:
            raise
        except Exception as e:
            logger.warning("ZentaoClient.put error path=%s: %s", path, e)
            raise ZentaoAPIError(0, str(e))

    def delete(self, path: str) -> dict | None:
        """DELETE /v1/{path}"""
        url = f"{self._api_base}/{path.lstrip('/')}"
        try:
            resp = httpx.delete(url, headers=self._headers(), timeout=_DEFAULT_TIMEOUT)
            if resp.status_code == 404:
                return None
            if resp.status_code not in (200, 201):
                raise ZentaoAPIError(resp.status_code, resp.text[:500])
            text = resp.text.strip()
            return resp.json() if text else {"message": "success"}
        except ZentaoAPIError:
            raise
        except Exception as e:
            logger.warning("ZentaoClient.delete error path=%s: %s", path, e)
            raise ZentaoAPIError(0, str(e))

    def get_page(self, path: str, params: dict | None = None) -> dict | None:
        """
        GET a Zentao *page* JSON (not the v1 API), e.g. /bug-activate-{id}.json.
        These are the web-app page data endpoints that include users/builds metadata.
        """
        url = f"{self.base_url}/{path.lstrip('/')}"
        headers = {
            "Token": self.token,
            "Accept": "application/json",
        }
        try:
            resp = httpx.get(url, params=params, headers=headers, timeout=_DEFAULT_TIMEOUT)
            if resp.status_code in (401, 403, 404):
                return None
            if resp.status_code != 200:
                return None
            result = resp.json()
            # Zentao page JSON often wraps the actual payload inside:
            # {"status":"success","data":"{\"users\":{...},...}"}
            if isinstance(result, dict) and isinstance(result.get("data"), str):
                try:
                    decoded = json.loads(result["data"])
                    if isinstance(decoded, dict):
                        return decoded
                except Exception:
                    logger.debug("ZentaoClient.get_page: nested data decode failed path=%s", path, exc_info=True)
            return result
        except Exception as e:
            logger.warning("ZentaoClient.get_page error path=%s: %s", path, e)
            return None

    # ------------------------------------------------------------------
    # Convenience accessors for common objects
    # ------------------------------------------------------------------

    def get_bug(self, bug_id: int) -> dict | None:
        return self.get(f"bugs/{bug_id}")

    def get_story(self, story_id: int) -> dict | None:
        return self.get(f"stories/{story_id}")

    def get_testcase(self, case_id: int) -> dict | None:
        return self.get(f"testcases/{case_id}")

    def get_build(self, build_id: int) -> dict | None:
        return self.get(f"builds/{build_id}")

    def get_execution(self, execution_id: int) -> dict | None:
        return self.get(f"executions/{execution_id}")

    def get_user(self, user_id: int) -> dict | None:
        return self.get(f"users/{user_id}")

    def list_project_executions(self, project_id: int, limit: int = 200) -> list[dict]:
        """Return the execution rows visible under a Zentao project."""
        data = self.get(f"projects/{project_id}/executions", params={"limit": limit}) or {}
        rows = data.get("executions") if isinstance(data, dict) else []
        return rows if isinstance(rows, list) else []

    # ------------------------------------------------------------------
    # Bug write actions
    # ------------------------------------------------------------------

    def close_bug(self, bug_id: int, comment: str = "") -> dict | None:
        """POST /v1/bugs/{id}/close"""
        body: dict[str, Any] = {}
        if comment:
            body["comment"] = comment
        return self.post(f"bugs/{bug_id}/close", body)

    def active_bug(self, bug_id: int, assigned_to: str = "", opened_build: list[str] | None = None, comment: str = "") -> dict | None:
        """POST /v1/bugs/{id}/active"""
        body: dict[str, Any] = {}
        if assigned_to:
            body["assignedTo"] = assigned_to
        if opened_build:
            body["openedBuild"] = opened_build
        if comment:
            body["comment"] = comment
        return self.post(f"bugs/{bug_id}/active", body)

    def assign_bug(self, bug_id: int, assigned_to: str, comment: str = "") -> dict | None:
        """POST /v1/bugs/{id}/assign"""
        body: dict[str, Any] = {"assignedTo": assigned_to}
        if comment:
            body["comment"] = comment
        return self.post(f"bugs/{bug_id}/assign", body)

    def delete_bug(self, bug_id: int) -> dict | None:
        """DELETE /v1/bugs/{id}"""
        return self.delete(f"bugs/{bug_id}")

    def update_bug(self, bug_id: int, data: dict) -> dict | None:
        """PUT /v1/bugs/{id}"""
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

    def create_bug(self, product_id: int, data: dict) -> dict | None:
        """
        Create a new bug in Zentao.

        Uses the product-scoped REST endpoint, which is the stable path
        across Zentao 18.x: POST /v1/products/{productID}/bugs.
        The 'product' key is stripped from the body since it goes in the URL.
        """
        body = {k: v for k, v in data.items() if k != "product"}
        return self.post(f"products/{product_id}/bugs", body)

    def get_execution_context(self, execution_id: int) -> dict:
        """
        Return {product_ids: [...], project_id: int|None} for the given execution.
        Uses GET /v1/executions/{id}.
        """
        try:
            resp = self.get(f"executions/{execution_id}")
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

        Primary source: GET /v1/executions/{id}/builds — the same endpoint
        used by overall_test_service and zentao_version_sync_service, so
        we know it is the reliable shape on this Zentao version.

        Fallback (for older Zentao builds): inspect the execution detail
        payload for `build/builds/openedBuild/openedBuilds` keys.
        """
        try:
            resp = self.get(f"executions/{execution_id}/builds", params={"limit": 500})
            if isinstance(resp, dict):
                rows = resp.get("builds")
                if isinstance(rows, list):
                    ids: list[str] = []
                    for r in rows:
                        if isinstance(r, dict):
                            bid = _coerce_int_str(r.get("id"))
                            if bid:
                                ids.append(bid)
                    if ids:
                        return list(dict.fromkeys(ids))
        except Exception:
            pass

        try:
            resp = self.get(f"executions/{execution_id}")
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

    def upload_file(
        self,
        file_bytes: bytes,
        filename: str,
        content_type: str = "application/octet-stream",
        object_type: str = "bug",
        object_id: int = 0,
    ) -> dict:
        """
        Upload a file to Zentao and return metadata describing the uploaded
        file.

        Zentao v1 accepts multipart POSTs at `/v1/files` with the fields
        `files[]`, `objectType`, `objectID`. Responses vary by version:

          - {"files": [{"id": N, ...}]}
          - {"data": [...]}  (some IPD forks)
          - [{"id": N, ...}]  (bare list)
          - {"id": N, ...}    (single dict when only one file was sent)

        Returns a normalized dict with at least: id (int), title, pathname,
        extension. Raises ZentaoAPIError on failure.
        """
        url = f"{self._api_base}/files"
        headers = {"Token": self.token}
        files = {"files[]": (filename, file_bytes, content_type)}
        data = {"objectType": object_type, "objectID": str(object_id)}
        try:
            resp = httpx.post(url, headers=headers, files=files, data=data, timeout=30.0)
            if resp.status_code not in (200, 201):
                raise ZentaoAPIError(resp.status_code, resp.text[:500])
            payload = _parse_write_response(resp)
        except ZentaoAPIError:
            raise
        except Exception as e:
            logger.warning("ZentaoClient.upload_file error: %s", e)
            raise ZentaoAPIError(0, str(e))

        entry: dict | None = None
        if isinstance(payload, dict):
            if isinstance(payload.get("files"), list) and payload["files"]:
                first = payload["files"][0]
                if isinstance(first, dict):
                    entry = first
            elif isinstance(payload.get("data"), list) and payload["data"]:
                first = payload["data"][0]
                if isinstance(first, dict):
                    entry = first
            elif payload.get("id"):
                entry = payload
        elif isinstance(payload, list) and payload:
            first = payload[0]
            if isinstance(first, dict):
                entry = first
        if not entry or not entry.get("id"):
            raise ZentaoAPIError(502, f"禅道上传文件返回异常: {str(payload)[:200]}")

        file_id = int(entry.get("id"))
        pathname = str(entry.get("pathname") or entry.get("addedBy") or "")
        title = str(entry.get("title") or entry.get("name") or filename)
        extension = str(entry.get("extension") or "").lstrip(".").lower()
        if not extension and "." in filename:
            extension = filename.rsplit(".", 1)[-1].lower()
        return {
            "id": file_id,
            "title": title,
            "pathname": pathname,
            "extension": extension,
        }

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
