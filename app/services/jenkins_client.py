"""
Jenkins REST API client.

用 HTTP Basic Auth（username + API Token）调用 Jenkins，封装本系统需要的最小能力：
- 测试连接 / whoami
- 列某个 view 下的 Job
- 取 Job / build 状态
- 触发构建（带或不带参数）并拿到队列项
- 跟踪队列项 → 真正的 build number

设计取舍
--------
- Jenkins 中文 view / job 名要 URL 编码后塞进 /view/<name> /job/<name> 路径。
- 触发构建是异步的：先进队列（返回 Location: .../queue/item/<id>/），executor 空闲后才
  分配 build number。本客户端只负责取到 queue item，由上层决定是否轮询。
- 若实例开启 CSRF 保护，POST 需要带 crumb；本客户端在每次写操作前尝试取一次 crumb，
  取不到（未开启 CSRF）就直接发，不报错。
"""
from __future__ import annotations

import logging
from typing import Any, Optional
from urllib.parse import quote

import httpx

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 15.0


class JenkinsError(Exception):
    """Raised when a Jenkins API call fails in a way the caller should surface."""


class JenkinsClient:
    def __init__(self, base_url: str, username: str, token: str, *, timeout: float = _DEFAULT_TIMEOUT):
        self.base_url = (base_url or "").rstrip("/")
        self.username = username
        self.token = token
        self.timeout = timeout

    # ------------------------------------------------------------------
    # Low-level helpers
    # ------------------------------------------------------------------

    def _auth(self) -> tuple[str, str]:
        return (self.username, self.token)

    @staticmethod
    def _job_path(job_full_name: str) -> str:
        """
        Build the /job/.../job/... path for a (possibly nested) job name.

        - "s40311"            → "job/s40311"
        - "folderA/jobB"      → "job/folderA/job/jobB"
        每段单独 URL 编码以兼容中文 / 空格。
        """
        segments = [seg for seg in str(job_full_name or "").split("/") if seg]
        return "/".join(f"job/{quote(seg)}" for seg in segments)

    def _get(self, path: str, params: dict | None = None) -> Any:
        url = f"{self.base_url}/{path.lstrip('/')}"
        try:
            resp = httpx.get(url, auth=self._auth(), params=params, timeout=self.timeout)
        except Exception as exc:
            raise JenkinsError(f"请求 Jenkins 失败：{exc}") from exc
        if resp.status_code == 401:
            raise JenkinsError("Jenkins 认证失败（401）：请检查账号或 API Token")
        if resp.status_code == 403:
            raise JenkinsError("Jenkins 拒绝访问（403）：账号无权限或需要 API Token")
        if resp.status_code == 404:
            raise JenkinsError("Jenkins 资源不存在（404）：请检查 Job / 视图名称")
        if resp.status_code >= 400:
            raise JenkinsError(f"Jenkins 返回 {resp.status_code}：{resp.text[:200]}")
        return resp

    def _crumb(self) -> dict[str, str]:
        """Fetch a CSRF crumb header; return {} if CSRF protection is disabled."""
        try:
            resp = httpx.get(
                f"{self.base_url}/crumbIssuer/api/json",
                auth=self._auth(),
                timeout=self.timeout,
            )
            if resp.status_code == 200:
                data = resp.json()
                field = data.get("crumbRequestField")
                crumb = data.get("crumb")
                if field and crumb:
                    return {field: crumb}
        except Exception as exc:
            logger.debug("jenkins crumb fetch skipped: %s", exc)
        return {}

    def _post(self, path: str, params: dict | None = None) -> httpx.Response:
        url = f"{self.base_url}/{path.lstrip('/')}"
        headers = self._crumb()
        try:
            resp = httpx.post(
                url,
                auth=self._auth(),
                params=params,
                headers=headers,
                timeout=self.timeout,
            )
        except Exception as exc:
            raise JenkinsError(f"请求 Jenkins 失败：{exc}") from exc
        if resp.status_code == 401:
            raise JenkinsError("Jenkins 认证失败（401）：请检查账号或 API Token")
        if resp.status_code == 403:
            raise JenkinsError("Jenkins 拒绝访问（403）：账号无权限触发该 Job，或 CSRF 校验失败")
        if resp.status_code == 404:
            raise JenkinsError("Jenkins Job 不存在（404）：请检查 Job 名称")
        if resp.status_code >= 400:
            raise JenkinsError(f"Jenkins 返回 {resp.status_code}：{resp.text[:200]}")
        return resp

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def whoami(self) -> dict:
        """Verify credentials; returns the current Jenkins user info."""
        resp = self._get("me/api/json")
        try:
            return resp.json()
        except Exception:
            return {}

    def list_views(self) -> list[dict]:
        """List all top-level Jenkins views (name + url)."""
        resp = self._get("api/json", params={"tree": "views[name,url]"})
        data = resp.json() if resp is not None else {}
        views = data.get("views") if isinstance(data, dict) else []
        return views if isinstance(views, list) else []

    def list_view_jobs(self, view_name: str) -> list[dict]:
        """
        List jobs under a Jenkins view.

        view_name 可含中文（如 "自动化测试"），单段编码后拼到 /view/<name>。
        返回每个 Job 的 name / url / color（color 含运行状态：blue=成功、red=失败、
        *_anime=运行中、disabled=禁用 等）。
        """
        path = f"view/{quote(view_name)}/api/json"
        resp = self._get(
            path,
            params={
                "tree": (
                    "jobs[name,url,color,buildable,_class,description,"
                    "lastBuild[number,result,timestamp,building,duration],"
                    "lastSuccessfulBuild[number,timestamp],"
                    "healthReport[score,description]]"
                )
            },
        )
        data = resp.json() if resp is not None else {}
        jobs = data.get("jobs") if isinstance(data, dict) else []
        return jobs if isinstance(jobs, list) else []

    def get_job(self, job_full_name: str) -> dict:
        """Return job detail incl. recent builds, artifacts and parameter definitions."""
        path = f"{self._job_path(job_full_name)}/api/json"
        resp = self._get(
            path,
            params={
                "tree": (
                    "name,fullName,url,color,buildable,inQueue,description,_class,"
                    "healthReport[score,description],"
                    "lastBuild[number,url,result,building,timestamp,duration],"
                    "lastCompletedBuild[number,url,result,timestamp,duration],"
                    "lastSuccessfulBuild[number,url,timestamp],"
                    "lastFailedBuild[number,url,timestamp],"
                    "builds[number,url,result,building,timestamp,duration]{0,30},"
                    "property[parameterDefinitions[name,type,description,"
                    "defaultParameterValue[value],choices]]"
                )
            },
        )
        return resp.json()

    def get_build(self, job_full_name: str, build_number: int) -> dict:
        path = f"{self._job_path(job_full_name)}/{int(build_number)}/api/json"
        resp = self._get(
            path,
            params={
                "tree": (
                    "number,url,result,building,timestamp,duration,displayName,description,"
                    "artifacts[fileName,relativePath],"
                    "actions[parameters[name,value]],"
                    "actions[causes[shortDescription,userName]]"
                )
            },
        )
        return resp.json()

    def trigger_build(self, job_full_name: str, params: Optional[dict] = None) -> Optional[str]:
        """
        Trigger a build. Returns the queue item URL (…/queue/item/<id>/) if Jenkins
        provides one (via the Location header), else None.
        """
        job_path = self._job_path(job_full_name)
        if params:
            resp = self._post(f"{job_path}/buildWithParameters", params=params)
        else:
            resp = self._post(f"{job_path}/build")
        location = resp.headers.get("Location")
        if location:
            return location.rstrip("/") + "/"
        return None

    def get_queue_item(self, queue_item_url: str) -> dict:
        """
        Resolve a queue item to its execution info. When `executable` is present the
        build has started and carries the real build number.
        """
        url = queue_item_url.rstrip("/")
        # Accept either a full URL or a relative queue path.
        if url.startswith("http"):
            full = f"{url}/api/json"
            try:
                resp = httpx.get(full, auth=self._auth(), timeout=self.timeout)
            except Exception as exc:
                raise JenkinsError(f"请求 Jenkins 队列失败：{exc}") from exc
            if resp.status_code >= 400:
                raise JenkinsError(f"Jenkins 队列返回 {resp.status_code}")
            return resp.json()
        resp = self._get(f"{url}/api/json")
        return resp.json()


__all__ = ["JenkinsClient", "JenkinsError"]
