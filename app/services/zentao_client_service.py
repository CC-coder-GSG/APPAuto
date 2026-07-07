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
    """
    Parse body of a POST/PUT response defensively.

    Zentao v1 write endpoints are inconsistent: some return full JSON,
    some return an empty body on success, some return `id=xxx` plain
    text, and PHP warnings can leak HTML with a 200 status.
    """
    text = (resp.text or "").lstrip("﻿").strip()
    if not text:
        return {"message": "success"}
    try:
        return _loads_lenient(text)
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
            body = resp.text or ""
            # 禅道 IPD 对某些异常 Bug 的 v1 接口会返回 200 + 仅一个 UTF-8 BOM
            # （3 字节、无 JSON 正文）。这种"空响应"等同于拿不到数据，返回 None
            # 让上层走页面 JSON 兜底，而不是当成"非 JSON 响应"直接报错。
            if not body.strip("﻿ \t\r\n"):
                return None
            try:
                # 优先用宽松解析（剥 BOM），失败再退回 httpx 的 .json()
                return _loads_lenient(body)
            except Exception:
                try:
                    return resp.json()
                except Exception:
                    pass
                if "Fatal error" in body or "fatal error" in body:
                    raise ZentaoAPIError(500, "禅道服务器内部错误（PHP Fatal Error），该 Bug 数据可能存在异常，请联系禅道管理员检查。")
                raise ZentaoAPIError(502, f"禅道返回了非 JSON 响应: {body.lstrip(chr(0xFEFF)).strip()[:200]}")
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
            resp = httpx.get(url, params=params, headers=headers, timeout=_DEFAULT_TIMEOUT, follow_redirects=True)
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
        """POST 到禅道传统页面动作（非 v1 REST），如 task-pause-{id}.json。

        用于 v1 REST 未开放的状态动作（pause 等）。本部署的传统 .json 页面接受
        Token 头鉴权（list_assignable_users 即走 get_page + Token）。用表单编码提交，
        与 Zentao 传统动作读取 $_POST 一致。返回解析后的 JSON（失败/非 JSON 返回原文包裹）。
        """
        url = f"{self.base_url}/{path.lstrip('/')}"
        headers = {
            "Token": self.token,
            "Accept": "application/json, text/html;q=0.8, */*;q=0.5",
        }
        try:
            resp = httpx.post(url, data=(data or {}), headers=headers, timeout=_DEFAULT_TIMEOUT, follow_redirects=True)
            if resp.status_code not in (200, 201):
                raise ZentaoAPIError(resp.status_code, resp.text[:300])
            text = (resp.text or "").strip()
            if not text:
                return {"message": "success"}
            try:
                return _loads_lenient(text)
            except Exception:
                return {"raw": text[:300]}
        except ZentaoAPIError:
            raise
        except Exception as e:
            logger.warning("ZentaoClient.post_page error path=%s: %s", path, e)
            raise ZentaoAPIError(0, str(e))

    def get_page_text(self, path: str, params: dict | None = None) -> str | None:
        """
        Fetch raw HTML/text from a Zentao page endpoint using Token auth.

        Some IPD deployments return diagnostic HTML/JS instead of JSON when the
        token lacks page-level access. We use this as a best-effort probe when a
        write endpoint returns an empty success response but the created object
        cannot be found afterwards.
        """
        url = f"{self.base_url}/{path.lstrip('/')}"
        headers = {
            "Token": self.token,
            "Accept": "text/html,application/json;q=0.9,*/*;q=0.8",
        }
        try:
            resp = httpx.get(url, params=params, headers=headers, timeout=_DEFAULT_TIMEOUT, follow_redirects=True)
            if resp.status_code != 200:
                return None
            return resp.text or ""
        except Exception as e:
            logger.warning("ZentaoClient.get_page_text error path=%s: %s", path, e)
            return None

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
        return self.get(f"bugs/{bug_id}")

    def get_bug_with_fallback(self, bug_id: int) -> dict | None:
        """
        Return a bug's raw dict, falling back to the page JSON
        (`bug-view-{id}.json`) when the v1 API yields no usable body.

        禅道 IPD 对部分异常 Bug 的 v1 接口会返回 200 + 仅一个 UTF-8 BOM（空响应），
        或直接抛 PHP Fatal Error。这种情况下 v1 拿不到数据，但页面 JSON 接口
        仍返回完整的 bug（含 assignedTo）+ actions + users，所以统一在这里兜底。

        401（token 失效）会原样抛出，交给调用方刷新 token 后重试——页面 JSON
        兜底不应吞掉 token 过期信号。页面 JSON 顶层的 actions 会合并进返回的
        bug dict，供详情 normalizer 使用。
        """
        raw: dict | list | None = None
        try:
            raw = self.get_bug(bug_id)
        except ZentaoAPIError as exc:
            if exc.status_code == 401:
                raise
            logger.info("get_bug_with_fallback: v1 失败 bug_id=%s status=%s，回退页面 JSON",
                        bug_id, exc.status_code)
            raw = None
        if raw:
            return raw

        page = self.get_page(f"bug-view-{bug_id}.json")
        if isinstance(page, dict):
            bug = page.get("bug") or page
            if isinstance(bug, dict) and "actions" not in bug and "actions" in page:
                bug = {**bug, "actions": page["actions"]}
            return bug
        return None

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

    def list_project_executions(self, project_id: int, limit: int = 200, status: str = "all") -> list[dict]:
        """
        Return the execution rows visible under a Zentao project.

        禅道默认会过滤掉非 doing/wait 状态的执行（closed/suspended），
        而我们这边手动同步是要 *所有* 状态都拿到，所以传 status=all。
        同时翻页直到响应不再带满 limit 为止，避免 100 截断丢数据。
        """
        page = 1
        all_rows: list[dict] = []
        seen_ids: set[int] = set()
        while True:
            params: dict[str, Any] = {"limit": limit, "page": page}
            if status:
                params["status"] = status
            data = self.get(f"projects/{project_id}/executions", params=params) or {}
            rows = data.get("executions") if isinstance(data, dict) else None
            if not isinstance(rows, list) or not rows:
                break
            added = 0
            for row in rows:
                rid = row.get("id") if isinstance(row, dict) else None
                try:
                    rid_int = int(rid) if rid is not None else None
                except (TypeError, ValueError):
                    rid_int = None
                if rid_int is None or rid_int in seen_ids:
                    continue
                seen_ids.add(rid_int)
                all_rows.append(row)
                added += 1
            # 没新增 = 已经翻完，跳出
            if added == 0 or len(rows) < limit:
                break
            page += 1
            if page > 50:  # safety cap
                break
        return all_rows

    def list_execution_stories(self, execution_id: int, limit: int = 500) -> list[dict]:
        """Return the stories/requirements linked to an execution."""
        data = self.get(f"executions/{execution_id}/stories", params={"limit": limit}) or {}
        rows = data.get("stories") if isinstance(data, dict) else []
        return rows if isinstance(rows, list) else []

    def list_projects(self, limit: int = 100) -> list[dict]:
        """Return the projects visible to the current token."""
        data = self.get("projects", params={"limit": limit}) or {}
        rows = data.get("projects") if isinstance(data, dict) else []
        return rows if isinstance(rows, list) else []

    def list_execution_builds(self, execution_id: int, limit: int = 500) -> list[dict]:
        """Return all builds bound to an execution (raw rows, ordered as returned by 禅道)."""
        data = self.get(f"executions/{execution_id}/builds", params={"limit": limit}) or {}
        rows = data.get("builds") if isinstance(data, dict) else []
        return rows if isinstance(rows, list) else []

    def list_project_builds(self, project_id: int, limit: int = 500) -> list[dict]:
        """Return all builds in a project across all its executions.

        用于"按名字找 build" —— 禅道 v1 没有按 name 的全局搜索 endpoint，
        只能拉这个项目的全量列表然后客户端过滤。每条 row 带 execution / id /
        name，足够定位。
        """
        data = self.get(f"projects/{project_id}/builds", params={"limit": limit}) or {}
        rows = data.get("builds") if isinstance(data, dict) else []
        return rows if isinstance(rows, list) else []

    # ------------------------------------------------------------------
    # Build write actions
    # ------------------------------------------------------------------

    def create_execution_build(
        self,
        execution_id: int,
        name: str,
        *,
        project_id: int | None = None,
        product_id: int | None = None,
        builder: str | None = None,
        date: str | None = None,
        desc: str | None = None,
    ) -> dict | None:
        """
        Create a build attached to an execution.

        实测：禅道 IPD 4.3 的 `POST /v1/executions/{id}/builds` 永远返回 200 + 空 body
        且什么都不写（哪怕权限 / 字段全部正确）。但 `POST /v1/projects/{project_id}/builds`
        在同一 token 下能正常 201 + 完整 body + 真正落库 —— 在 body 里把 `execution`
        传上就行。所以这里强制走 projects 端点，必须传 project_id。

        date 不传时自动填今天 —— 禅道 IPD 4.3 不传 `date` 会 400 "打包日期不能为空"，
        所有调用方都得"今天"，没必要让每个调用方各自重复一遍。
        """
        if project_id is None:
            raise ValueError("create_execution_build: project_id is required")
        from datetime import datetime as _dt

        body: dict[str, Any] = {
            "name": name,
            "execution": execution_id,
            "date": date or _dt.now().strftime("%Y-%m-%d"),
        }
        if product_id is not None:
            body["product"] = product_id
        if builder:
            body["builder"] = builder
        if desc is not None:
            body["desc"] = desc
        return self.post(f"projects/{project_id}/builds", body)

    def update_build(
        self,
        build_id: int,
        *,
        name: str | None = None,
        execution_id: int | None = None,
        product_id: int | None = None,
        desc: str | None = None,
    ) -> dict | None:
        """PUT /v1/builds/{id}"""
        body: dict[str, Any] = {}
        if name is not None:
            body["name"] = name
        if execution_id is not None:
            body["execution"] = execution_id
        if product_id is not None:
            body["product"] = product_id
        if desc is not None:
            body["desc"] = desc
        if not body:
            return None
        return self.put(f"builds/{build_id}", body)

    def delete_build(self, build_id: int) -> dict | None:
        """DELETE /v1/builds/{id}"""
        return self.delete(f"builds/{build_id}")

    # ------------------------------------------------------------------
    # Task read + write（禅道任务联动，2026-06-29）
    # 字段/行为见 docs/2026-06-29-禅道任务联动-实现计划.md 第三节附录（已实测）。
    # ------------------------------------------------------------------

    def list_execution_tasks(self, execution_id: int, limit: int = 500) -> list[dict]:
        """GET /v1/executions/{id}/tasks —— 该执行下的任务（含父/子）。"""
        data = self.get(f"executions/{execution_id}/tasks", params={"limit": limit}) or {}
        rows = data.get("tasks") if isinstance(data, dict) else []
        return rows if isinstance(rows, list) else []

    def get_task(self, task_id: int) -> dict | None:
        """GET /v1/tasks/{id}"""
        return self.get(f"tasks/{task_id}")

    def create_execution_task(
        self,
        execution_id: int,
        *,
        name: str,
        assigned_to: str | None = None,
        task_type: str = "test",
        story: int | None = None,
        est_started: str | None = None,
        deadline: str | None = None,
        estimate: float | None = None,
        pri: int = 3,
        desc: str | None = None,
    ) -> dict | None:
        """POST /v1/executions/{id}/tasks —— 建任务，返回完整对象(含 id)。

        ⚠️ 实测：`parent` 在 create 时被忽略，建子任务须建完再 PUT 设 parent
        （见 link_task_parent）。datetime/date 一律按上海本地传，禅道存为 UTC。
        """
        body: dict[str, Any] = {"name": name, "type": task_type, "pri": pri}
        if assigned_to:
            body["assignedTo"] = assigned_to
        if story:
            body["story"] = story
        if est_started:
            body["estStarted"] = est_started
        if deadline:
            body["deadline"] = deadline
        if estimate is not None:
            body["estimate"] = estimate
        if desc is not None:
            body["desc"] = desc
        result = self.post(f"executions/{execution_id}/tasks", body)
        if isinstance(result, dict) and result.get("id"):
            return result
        # 禅道写接口有时建成功却返回空体 / {"message":"success"}（不带 id）。
        # 回查执行任务列表按名称(+指派人/需求)找回刚建的任务，避免本地写回不到 id。
        recovered = self._recover_created_task(
            execution_id, name=name, assigned_to=assigned_to, story=story
        )
        return recovered or result

    def _recover_created_task(
        self,
        execution_id: int,
        *,
        name: str,
        assigned_to: str | None = None,
        story: int | None = None,
    ) -> dict | None:
        """create 未返回 id 时，从任务列表里按名称找回刚建的任务（取最新一条）。"""
        try:
            rows = self.list_execution_tasks(execution_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("_recover_created_task list failed exec=%s: %s", execution_id, exc)
            return None

        def _acc(v: Any) -> str:
            if isinstance(v, dict):
                return str(v.get("account") or "").strip().lower()
            return str(v or "").strip().lower()

        target = (name or "").strip()
        cands = [
            t for t in rows
            if t.get("id") and str(t.get("name") or "").strip() == target
        ]
        if not cands:
            return None
        # 尽量用指派人 / 关联需求进一步收窄，再取 id 最大（最新建）的一条。
        if assigned_to:
            acc_l = str(assigned_to).strip().lower()
            narrowed = [t for t in cands if _acc(t.get("assignedTo")) == acc_l]
            cands = narrowed or cands
        if story:
            narrowed = [t for t in cands if str(t.get("story") or "") == str(story)]
            cands = narrowed or cands
        cands.sort(key=lambda t: int(t.get("id") or 0))
        return cands[-1]

    def update_task(self, task_id: int, data: dict) -> dict | None:
        """PUT /v1/tasks/{id} —— 改派(assignedTo)/设父子(parent)/改 estimate 等。"""
        return self.put(f"tasks/{task_id}", data)

    def link_task_parent(self, child_task_id: int, parent_task_id: int) -> dict | None:
        """把子任务挂到父任务下（create 不认 parent，须事后 PUT）。"""
        return self.put(f"tasks/{child_task_id}", {"parent": parent_task_id})

    def reassign_task(self, task_id: int, assigned_to: str) -> dict | None:
        """改派任务负责人。"""
        return self.put(f"tasks/{task_id}", {"assignedTo": assigned_to})

    def start_task(self, task_id: int, *, real_started: str | None = None, left: float | None = None, assigned_to: str | None = None) -> dict | None:
        """POST /v1/tasks/{id}/start —— 开始任务（status→doing）。

        ⚠️ 用系统账号（非指派人本人）调用 start 时，禅道可能把 assignedTo 清空/改掉，
        故显式回传 assignedTo=当前指派人，保持指派不变。
        """
        body: dict[str, Any] = {}
        if real_started:
            body["realStarted"] = real_started
        if left is not None:
            body["left"] = left
        if assigned_to:
            body["assignedTo"] = assigned_to
        return self.post(f"tasks/{task_id}/start", body)

    def pause_task(self, task_id: int, *, comment: str | None = None) -> dict | None:
        """POST /v1/tasks/{id}/pause —— 暂停任务（status→pause），之后可再 start 继续。

        ⚠️ 本部署（ipd4.3）任务动作的 REST POST **空 body 会被静默忽略**（返回
        200 + 任务原样，状态不变），必须至少带 comment 字段（空串即可）。此外
        token 被当 guest 时同样 200 无效果——调用方必须回读校验状态，未生效时
        回退 zentao_web_session.pause_task_via_web（网页 cookie 会话）。
        """
        return self.post(f"tasks/{task_id}/pause", {"comment": comment or ""})

    def finish_task(
        self,
        task_id: int,
        *,
        current_consumed: float,
        finished_date: str | None = None,
        assigned_to: str | None = None,
    ) -> dict | None:
        """POST /v1/tasks/{id}/finish —— 完成任务。

        ⚠️ `currentConsumed`（本次消耗）必须 >0，否则禅道 400。
        """
        body: dict[str, Any] = {"currentConsumed": current_consumed}
        if finished_date:
            body["finishedDate"] = finished_date
        if assigned_to:
            body["assignedTo"] = assigned_to
        return self.post(f"tasks/{task_id}/finish", body)

    def restart_task(self, task_id: int, *, consumed: float, left: float, assigned_to: str | None = None) -> dict | None:
        """POST /v1/tasks/{id}/restart —— 继续暂停/重新激活已完成任务（status→doing）。

        ⚠️ `consumed` 与 `left`(>0) 必填，否则禅道 400。无 /activate 端点。
        显式回传 assignedTo 保持指派不变（系统账号操作时禅道可能改掉指派人）。
        """
        body: dict[str, Any] = {"consumed": consumed, "left": left}
        if assigned_to:
            body["assignedTo"] = assigned_to
        return self.post(f"tasks/{task_id}/restart", body)

    def close_task(self, task_id: int, *, comment: str | None = None) -> dict | None:
        """POST /v1/tasks/{id}/close —— 关闭任务（status→closed）。

        ⚠️ 本部署（ipd4.3）空 body 会被静默忽略（200 + 任务原样、状态不变），
        必须至少带 comment 字段（空串即可）。平台前端点「关闭」不传 comment，
        曾因此全部静默失败。
        """
        return self.post(f"tasks/{task_id}/close", {"comment": comment or ""})

    def cancel_task(self, task_id: int, *, comment: str | None = None) -> dict | None:
        """POST /v1/tasks/{id}/cancel —— 取消任务（status→cancel）。

        与 pause/close 同理：ipd4.3 空 body 会被静默忽略，固定带 comment 字段。
        REST 未生效时由调用方回退网页 cookie 会话（cancel_task_via_web）。
        """
        return self.post(f"tasks/{task_id}/cancel", {"comment": comment or ""})

    def list_assignable_users(self, execution_id: int) -> dict[str, str]:
        """该执行可指派用户 {account: realname}。

        禅道 `/v1/users` 需 company-browse 权限（当前账号 400），改走任务创建页 JSON
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
        across Zentao 18.x: POST /v1/products/{productID}/bugs. The
        'product' key is stripped from the body since it goes in the URL.

        Some Zentao deployments respond with HTTP 201 + empty body to bug
        creation. When that happens we can't rely on the JSON body for the
        new bug ID; instead we:
          1) look at the Location header for a `/bugs/{id}` pointer, and
          2) failing that, query the newest bugs for the product and match
             by title to locate the row we just created.
        """
        body = {k: v for k, v in data.items() if k != "product"}
        url = f"{self._api_base}/products/{product_id}/bugs"
        try:
            resp = httpx.post(url, json=body, headers=self._headers(), timeout=_DEFAULT_TIMEOUT)
            if resp.status_code not in (200, 201):
                raise ZentaoAPIError(resp.status_code, resp.text[:500])
            result = _parse_write_response(resp)
        except ZentaoAPIError:
            raise
        except Exception as e:
            logger.warning("ZentaoClient.create_bug error: %s", e)
            raise ZentaoAPIError(0, str(e))

        if self._extract_bug_id_from_result(result):
            return result

        location = resp.headers.get("Location") or resp.headers.get("location") or ""
        m = re.search(r'/bugs?/(\d+)', location)
        if m:
            return {"id": int(m.group(1))}

        # Fallback: fetch newest bugs for this product and match title.
        title = str(body.get("title") or "").strip()
        if title:
            latest = self._find_latest_bug_by_title(product_id, title)
            if latest:
                logger.info("ZentaoClient.create_bug: recovered id via title lookup: %s", latest.get("id"))
                return latest

        return result

    @staticmethod
    def _extract_bug_id_from_result(result: Any) -> int | None:
        if not isinstance(result, dict):
            return None
        for candidate in (
            result,
            result.get("bug") if isinstance(result.get("bug"), dict) else None,
            result.get("data") if isinstance(result.get("data"), dict) else None,
        ):
            if not isinstance(candidate, dict):
                continue
            for key in ("id", "bugID", "bug_id"):
                val = candidate.get(key)
                try:
                    iv = int(val) if val is not None else None
                except (TypeError, ValueError):
                    iv = None
                if iv:
                    return iv
        return None

    def _find_latest_bug_by_title(self, product_id: int, title: str) -> dict | None:
        """
        Return the most recently created bug in the given product whose
        title matches `title`. Used to recover the bug ID when the
        create-bug response had an empty body.
        """
        try:
            resp = self.get(f"products/{product_id}/bugs", params={"orderBy": "id_desc", "limit": 30})
        except ZentaoAPIError:
            return None
        if not isinstance(resp, dict):
            return None
        bugs = resp.get("bugs") if isinstance(resp.get("bugs"), list) else None
        if not bugs:
            data = resp.get("data")
            if isinstance(data, list):
                bugs = data
        if not bugs:
            return None
        target = title.strip()
        for b in bugs:
            if not isinstance(b, dict):
                continue
            if str(b.get("title") or "").strip() == target:
                return b
        return None

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

        Zentao v1 accepts multipart POSTs at `/v1/files`. The expected field
        name varies by version (`files[]` for 18.x, `files` for 16.x, `file`
        for some IPD forks), so we try them in order on failure.

        Returns a normalized dict with at least: id (int), title, pathname,
        extension. Raises ZentaoAPIError on failure.
        """
        # Ensure filename has a recognizable extension. Zentao rejects uploads
        # when the extension isn't in its allowed list, and some browsers /
        # clipboards produce File objects with a bare name (e.g. "blob",
        # "image"). Derive an extension from the MIME type if missing.
        safe_name = self._ensure_filename_extension(filename, content_type)

        url = f"{self._api_base}/files"
        headers = {"Token": self.token}
        data = {"objectType": object_type, "objectID": str(object_id)}

        last_error: Exception | None = None
        last_body: str = ""
        # Order matters: modern Zentao (18.x+) uses the bracketed form; some
        # older / IPD forks use the singular forms; `imgFile` is the field
        # name the legacy `file.ajaxUpload` module expects — some Zentao
        # deployments route /v1/files through that handler internally.
        field_candidates = ("files[]", "files", "file", "imgFile")
        for field_name in field_candidates:
            files = {field_name: (safe_name, file_bytes, content_type)}
            try:
                resp = httpx.post(url, headers=headers, files=files, data=data, timeout=30.0)
                last_body = (resp.text or "")[:500]
                logger.info(
                    "ZentaoClient.upload_file field=%s filename=%s ct=%s "
                    "status=%s body=%s",
                    field_name, safe_name, content_type, resp.status_code, last_body,
                )
                if resp.status_code not in (200, 201):
                    last_error = ZentaoAPIError(resp.status_code, last_body)
                    continue
                try:
                    payload = _parse_write_response(resp)
                except ZentaoAPIError as pe:
                    last_error = pe
                    continue

                # Some Zentao versions return HTTP 200 with an `error` field
                # when the file is rejected (extension check, size limit).
                if isinstance(payload, dict) and payload.get("error"):
                    last_error = ZentaoAPIError(400, str(payload.get("error")).strip() or last_body)
                    continue

                entry = self._extract_uploaded_entry(payload)
                if not entry or not entry.get("id"):
                    last_error = ZentaoAPIError(502, f"禅道上传文件返回异常: {str(payload)[:200]}")
                    continue

                file_id = int(entry.get("id"))
                pathname = str(entry.get("pathname") or "")
                title = str(entry.get("title") or entry.get("name") or safe_name)
                extension = str(entry.get("extension") or "").lstrip(".").lower()
                if not extension and "." in safe_name:
                    extension = safe_name.rsplit(".", 1)[-1].lower()
                return {
                    "id": file_id,
                    "title": title,
                    "pathname": pathname,
                    "extension": extension,
                }
            except ZentaoAPIError as e:
                last_error = e
            except Exception as e:
                logger.warning("ZentaoClient.upload_file field=%s error: %s", field_name, e)
                last_error = ZentaoAPIError(0, str(e))

        if isinstance(last_error, ZentaoAPIError):
            raise last_error
        raise ZentaoAPIError(0, f"禅道文件上传失败（所有字段名都失败）: {last_body}")

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
