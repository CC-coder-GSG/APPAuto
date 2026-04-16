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
            return resp.json()
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
            return resp.json()
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


__all__ = ["ZentaoClient", "ZentaoAPIError"]
