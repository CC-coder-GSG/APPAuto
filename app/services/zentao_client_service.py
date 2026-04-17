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
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 15.0


class ZentaoAPIError(Exception):
    def __init__(self, status_code: int, message: str):
        self.status_code = status_code
        self.message = message
        super().__init__(f"ZentaoAPI {status_code}: {message}")


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
            return resp.json()
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
            return resp.json()
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

    def create_bug(self, data: dict) -> dict | None:
        """POST /v1/bugs — create a new bug in Zentao."""
        return self.post("bugs", data)

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

    def get_create_bug_meta(self, product_id: int, execution_id: int = 0) -> dict | None:
        """Fetch page-level JSON for the bug creation form (users, builds etc.)."""
        path = f"bug-create-{product_id}-{execution_id}.json"
        meta = self.get_page(path)
        if not meta:
            path = f"bug-create-{product_id}-0.json"
            meta = self.get_page(path)
        return meta


__all__ = ["ZentaoClient", "ZentaoAPIError"]
