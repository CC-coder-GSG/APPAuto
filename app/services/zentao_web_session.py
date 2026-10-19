"""
Zentao 网页会话客户端（cookie 登录，非 API Token）。

为什么需要它：本部署（禅道 ipd4.3）的任务动作 REST POST 有两类静默失败——
  1. **空 body 被静默忽略**：返回 200 + 任务原样、状态不变（pause_task/close_task
     已固定带 comment 字段规避，REST 是首选路径）；
  2. **token 被当 guest** 时同样 200 无效果；而传统页面动作带 Token 头会被 ACL
     拦截：`{"result":"fail","alert":"您无权访问该迭代！"}`（API Token 会话未加载
     用户的迭代访问权限，同一账号网页登录则正常）。

因此保留本模块作为 REST 未生效时的兜底：模拟真实网页登录（zentaosid cookie
会话）后提交暂停表单。
实测协议（2026-07-02 对 ipd4.3 抓包验证）：
  - 登录必须带浏览器 User-Agent，否则禅道静默拒绝、重渲染登录页；
  - rand 必须来自 `GET user-refreshRandom.html`（写入会话），密码为 md5(md5(明文)+rand)；
  - 暂停表单必须带隐藏字段 `status=pause`，否则服务端只当作渲染表单页，不执行。
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from collections import Counter
from dataclasses import dataclass, field
from urllib.parse import urlsplit, unquote

import httpx

from app.services.zentao_v2_protocol import ZentaoAPIError, normalize_base_url, relative_path

logger = logging.getLogger(__name__)

_TIMEOUT = 15.0
_EFFORT_WRITE_TIMEOUT = httpx.Timeout(45.0, connect=10.0)
_EFFORT_POLL_DELAYS = (0.0, 0.5, 1.0)
# 禅道登录对非浏览器 UA 直接打回登录页（无错误提示），必须伪装浏览器。
_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36"
)


@dataclass
class ZentaoWebLogin:
    base_url: str
    account: str
    password: str = field(repr=False)

    def __post_init__(self):
        self.base_url = normalize_base_url(self.base_url)


class ZentaoWebSessionError(Exception):
    def __init__(self, message: str, *, outcome_unknown: bool = False):
        super().__init__(message)
        self.outcome_unknown = outcome_unknown


def _md5(text: str) -> str:
    return hashlib.md5(text.encode()).hexdigest()


def _loads_lenient(text: str):
    return json.loads((text or "").strip("﻿ \t\r\n"))


def _form(fields: dict[str, str]) -> dict:
    """httpx 的 multipart 字段格式（禅道网页表单为 multipart/form-data）。"""
    return {k: (None, v) for k, v in fields.items()}


def _guarded_form_post(client, url: str, **kwargs):
    try:
        response = client.post(url, **kwargs)
    except Exception as exc:
        raise ZentaoWebSessionError("禅道表单请求结果不明，请刷新核实，停止重复提交", outcome_unknown=True) from exc
    if response.status_code != 200:
        raise ZentaoWebSessionError(f"禅道表单 HTTP {response.status_code}",
                                   outcome_unknown=response.status_code >= 500 or response.status_code == 408)
    return response


def _fail_reason(payload) -> str:
    if isinstance(payload, dict):
        load = payload.get("load")
        if isinstance(load, dict) and load.get("alert"):
            return str(load["alert"])
        if payload.get("message"):
            return str(payload["message"])
    return str(payload)[:200]


def _login(client: httpx.Client, login: ZentaoWebLogin) -> None:
    base = login.base_url.rstrip("/")
    client.get(f"{base}/user-login.html")
    rand = (client.get(f"{base}/user-refreshRandom.html").text or "").strip("﻿ \t\r\n")
    if not rand:
        raise ZentaoWebSessionError("禅道网页登录失败：获取 verifyRand 为空")
    resp = client.post(
        f"{base}/user-login.html",
        files=_form({
            "account": login.account,
            "password": _md5(_md5(login.password) + rand),
            "passwordStrength": "1",
            "referer": "/zentao/",
            "verifyRand": rand,
            "keepLogin": "0",
            "captcha": "",
        }),
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    try:
        payload = _loads_lenient(resp.text)
    except Exception:
        payload = None
    if not (isinstance(payload, dict) and payload.get("result") == "success"):
        raise ZentaoWebSessionError(
            f"禅道网页登录失败（账号 {login.account}）：{_fail_reason(payload) if payload else (resp.text or '')[:120]}"
        )


def _task_action_via_web(login: ZentaoWebLogin, task_id: int, *, action: str, status: str, comment: str | None = None) -> dict:
    """网页会话提交任务动作表单（pause/cancel 协议一致：隐藏字段 status 必填）。"""
    base = login.base_url.rstrip("/")
    zh = {"pause": "暂停", "cancel": "取消"}.get(action, action)
    with httpx.Client(
        timeout=_TIMEOUT,
        follow_redirects=True,
        headers={"User-Agent": _BROWSER_UA, "Referer": f"{base}/"},
        event_hooks={"request": [_same_site_request(base)]},
    ) as client:
        _login(client, login)
        resp = _guarded_form_post(client,
            f"{base}/task-{action}-{task_id}.html",
            # status 是动作弹窗的隐藏字段，缺了服务端只当作渲染表单页，不执行动作
            files=_form({"comment": comment or "", "uid": "", "status": status}),
            headers={"X-Requested-With": "XMLHttpRequest", "X-Zui-Modal": "true"},
        )
        try:
            payload = _loads_lenient(resp.text)
        except Exception:
            raise ZentaoWebSessionError(f"禅道{zh}返回非 JSON，结果待核实", outcome_unknown=True)
        if not (isinstance(payload, dict) and payload.get("result") == "success"):
            raise ZentaoWebSessionError(f"禅道{zh}未确认成功：{_fail_reason(payload)}",
                                       outcome_unknown=not isinstance(payload, dict) or payload.get('result') != 'fail')
        try:
            client.get(f"{base}/user-logout.html")
        except Exception:
            pass
        return payload


def pause_task_via_web(login: ZentaoWebLogin, task_id: int, *, comment: str | None = None) -> dict:
    """网页会话暂停任务（status→pause）。成功返回禅道响应，失败抛 ZentaoWebSessionError。"""
    return _task_action_via_web(login, task_id, action="pause", status="pause", comment=comment)


def cancel_task_via_web(login: ZentaoWebLogin, task_id: int, *, comment: str | None = None) -> dict:
    """网页会话取消任务（status→cancel）。协议与暂停一致，仅动作与隐藏 status 不同。"""
    return _task_action_via_web(login, task_id, action="cancel", status="cancel", comment=comment)


# ── 开始/继续/完成/关闭（2026-07-17：操作人归属修复）─────────────────────────
# 本人 API token 间歇性失效（过期刷新失败 / ipd4.3 token 被当 guest）时，这些
# 动作此前直接回退系统账号代操作 → 禅道动作历史显示成系统账号（如"陈文博开始
# 了任务"）。补上与 pause/cancel 同套路的本人网页 cookie 会话兜底，保证操作人
# 归属正确。协议要点：先 GET 动作表单页、把静态 input 默认值原样回传（模拟浏览
# 器，兜住各动作不同的隐藏字段），再覆盖业务字段提交。

_INPUT_TAG_RE = re.compile(r"<input\b[^>]*>", re.I)
_ATTR_RE = re.compile(r"""([\w-]+)\s*=\s*(?:"([^"]*)"|'([^']*)')""")


def _collect_form_defaults(html: str) -> dict[str, str]:
    """静态表单 input 的 name→value（隐藏字段/预填值）。跳过按钮与未勾选的单/复选。"""
    fields: dict[str, str] = {}
    for tag in _INPUT_TAG_RE.findall(html or ""):
        attrs = {m.group(1).lower(): (m.group(2) if m.group(2) is not None else m.group(3) or "") for m in _ATTR_RE.finditer(tag)}
        name = attrs.get("name")
        if not name:
            continue
        typ = (attrs.get("type") or "text").lower()
        if typ in {"submit", "button", "file", "image", "reset"}:
            continue
        if typ in {"checkbox", "radio"} and "checked" not in tag.lower():
            continue
        fields.setdefault(name, attrs.get("value", ""))
    return fields


def _task_form_action_via_web(
    login: ZentaoWebLogin,
    task_id: int,
    *,
    action: str,
    zh: str,
    overrides: dict,
    comment: str | None = None,
) -> dict:
    """通用任务动作表单提交：GET 表单页收集默认值 → 覆盖业务字段 → POST。"""
    base = login.base_url.rstrip("/")
    with httpx.Client(
        timeout=_TIMEOUT,
        follow_redirects=True,
        headers={"User-Agent": _BROWSER_UA, "Referer": f"{base}/"},
        event_hooks={"request": [_same_site_request(base)]},
    ) as client:
        _login(client, login)
        page = client.get(f"{base}/task-{action}-{task_id}.html")
        if "denyPage" in (page.text or "") or "访问受限" in (page.text or ""):
            raise ZentaoWebSessionError(f"禅道{zh}表单页访问受限（账号 {login.account}）")
        fields = _collect_form_defaults(page.text)
        fields.setdefault("uid", "")
        fields["comment"] = comment or ""
        for k, v in overrides.items():
            if v is not None:
                fields[k] = str(v)
        resp = _guarded_form_post(client,
            f"{base}/task-{action}-{task_id}.html",
            files=_form(fields),
            headers={"X-Requested-With": "XMLHttpRequest", "X-Zui-Modal": "true"},
        )
        try:
            payload = _loads_lenient(resp.text)
        except Exception:
            raise ZentaoWebSessionError(f"禅道{zh}返回非 JSON，结果待核实", outcome_unknown=True)
        if not (isinstance(payload, dict) and payload.get("result") == "success"):
            raise ZentaoWebSessionError(f"禅道{zh}未确认成功：{_fail_reason(payload)}",
                                       outcome_unknown=not isinstance(payload, dict) or payload.get('result') != 'fail')
        try:
            client.get(f"{base}/user-logout.html")
        except Exception:  # noqa: BLE001
            pass
        return payload


def start_task_via_web(
    login: ZentaoWebLogin, task_id: int, *, left: float, real_started: str | None = None, comment: str | None = None
) -> dict:
    """网页会话开始任务（status→doing）。表单实测字段：consumed/left（realStarted 由
    JS 日期控件渲染，显式覆盖）。"""
    return _task_form_action_via_web(
        login, task_id, action="start", zh="开始",
        overrides={"left": left, "realStarted": real_started}, comment=comment,
    )


def restart_task_via_web(
    login: ZentaoWebLogin, task_id: int, *, left: float, consumed: float | None = None,
    assigned_to: str | None = None, comment: str | None = None,
) -> dict:
    """网页会话继续暂停/重新激活任务（status→doing）。表单实测字段：assignedTo/consumed/left；
    consumed 传 None 时沿用表单预填的当前消耗值。"""
    overrides: dict = {"consumed": consumed, "left": left}
    if assigned_to:
        overrides["assignedTo"] = assigned_to
    return _task_form_action_via_web(login, task_id, action="restart", zh="继续", overrides=overrides, comment=comment)


def finish_task_via_web(
    login: ZentaoWebLogin, task_id: int, *, current_consumed: float,
    finished_date: str | None = None, comment: str | None = None,
) -> dict:
    """网页会话完成任务（status→done）。表单实测字段：currentConsumed/realStarted（隐藏
    预填，回传默认值即可；finishedDate 由 JS 渲染，显式覆盖）。"""
    return _task_form_action_via_web(
        login, task_id, action="finish", zh="完成",
        overrides={"currentConsumed": current_consumed, "finishedDate": finished_date}, comment=comment,
    )


def close_task_via_web(login: ZentaoWebLogin, task_id: int, *, comment: str | None = None) -> dict:
    """网页会话关闭任务（status→closed）。表单实测仅 uid + 评论。"""
    return _task_form_action_via_web(login, task_id, action="close", zh="关闭", overrides={}, comment=comment)


# ── 工时记录（effort 模块）────────────────────────────────────────────────────
# REST（ipd4.3）没有工时记录端点，只能走网页表单。2026-07-17 对本部署实测：
#   开源版的 task-recordEstimate 被 ACL 拦（denyType=nopriv），ipd 走 effort 模块——
#   查看：GET  effort-createForObject-task-{taskID}.json → data.efforts（按 id 键的 dict）
#   提交：POST effort-createForObject-task-{taskID}.html （zui2 批量表单，行字段
#         id[i]/objectType[i]=task/objectID[i]/dates[i]/work[i]/consumed[i]；
#         dates 支持补录任意日期；无 left 列，任务剩余工时不由此维护）
#   编辑：POST effort-edit-{effortID}.html （字段 objectType/objectID/consumed/left/work；
#         无日期字段 → 日期不可改，调用方需拦截改日期的请求）
#   删除：优先走官方 task-deleteWorkhour-{effortID}-yes.html（yes 用于确认删除最后一条
#         工时）；IPD effort 模块兼容 effort-delete-{effortID}.html。
# 所有写操作都「提交 → 同会话回读校验」，未生效抛 ZentaoWebSessionError，
# 由调用方决定兜底（如退回本地累计、完成时一次性提交）。


def _open_web_session(login: ZentaoWebLogin) -> httpx.Client:
    """打开已登录的网页会话（调用方负责 close；登出失败不致命）。"""
    client = httpx.Client(
        timeout=_TIMEOUT,
        follow_redirects=True,
        headers={"User-Agent": _BROWSER_UA, "Referer": f"{login.base_url.rstrip('/')}/"},
        event_hooks={"request": [_same_site_request(login.base_url)]},
    )
    try:
        _login(client, login)
    except Exception:
        client.close()
        raise
    return client


def _same_site_request(base: str):
    expected = urlsplit(base)

    def check(request):
        actual = urlsplit(str(request.url))
        decoded = unquote(actual.path).lower()
        if ((actual.scheme, actual.netloc) != (expected.scheme, expected.netloc)
                or not actual.path.startswith(expected.path.rstrip('/') + '/')
                or 'api.php' in decoded):
            raise ZentaoWebSessionError("禅道页面跳转超出站点范围")
    return check


def request_page_via_web(login: ZentaoWebLogin, path: str, *, params=None, data=None):
    """One authenticated page request. An uncertain write is never replayed here."""
    path = relative_path(path, page=True)
    base = login.base_url
    client = _open_web_session(login)
    writing = data is not None
    try:
        try:
            if writing:
                fields = {}
                for key, value in data.items():
                    if isinstance(value, (list, tuple)):
                        for index, item in enumerate(value):
                            fields[f"{key}[{index}]"] = str(item)
                    elif value is not None:
                        fields[key] = str(value)
                response = client.post(f"{base}/{path}", files=_form(fields),
                                       headers={"X-Requested-With": "XMLHttpRequest"})
            else:
                response = client.get(f"{base}/{path}", params=params)
        except Exception as exc:
            raise ZentaoAPIError(0, "禅道页面请求结果待核实", outcome_unknown=writing) from exc
        if response.status_code != 200:
            raise ZentaoAPIError(response.status_code, "禅道页面请求失败",
                                 outcome_unknown=writing and response.status_code >= 500)
        try:
            payload = _loads_lenient(response.text)
        except Exception as exc:
            raise ZentaoAPIError(502, "禅道页面未返回 JSON", outcome_unknown=writing) from exc
        if not isinstance(payload, dict):
            raise ZentaoAPIError(502, "禅道页面响应无效", outcome_unknown=writing)
        if payload.get('loginExpired') or payload.get('title') == '用户登录':
            raise ZentaoAPIError(401, "禅道网页会话已失效")
        if writing:
            if payload.get('result') == 'success' or payload.get('status') == 'success':
                return payload
            raise ZentaoAPIError(422, "禅道表单未确认成功",
                                 outcome_unknown=payload.get('result') != 'fail' and payload.get('status') != 'fail')
        if payload.get('status') == 'fail' or payload.get('result') == 'fail':
            raise ZentaoAPIError(403, "禅道页面拒绝访问")
        nested = payload.get('data')
        if isinstance(nested, str):
            try:
                nested = _loads_lenient(nested)
            except Exception as exc:
                raise ZentaoAPIError(502, "禅道页面 data 无效") from exc
        return nested if isinstance(nested, dict) else payload
    finally:
        _logout_quietly(client, base)
        client.close()


def fetch_file_via_web(login: ZentaoWebLogin, file_id: int):
    if int(file_id) <= 0:
        raise ZentaoAPIError(400, "附件编号无效")
    client = _open_web_session(login)
    try:
        response = client.get(f"{login.base_url}/file-read-{int(file_id)}.html")
        if response.status_code != 200:
            raise ZentaoAPIError(response.status_code, "禅道附件获取失败")
        if 'text/html' in response.headers.get('content-type', '').lower():
            raise ZentaoAPIError(502, "禅道附件返回了页面，请检查权限")
        response.read()
        return response
    finally:
        _logout_quietly(client, login.base_url)
        client.close()


def _logout_quietly(client: httpx.Client, base: str) -> None:
    try:
        client.get(f"{base}/user-logout.html")
    except Exception:  # noqa: BLE001
        pass


class ZentaoStoryChangeError(ZentaoWebSessionError):
    def __init__(self, message: str, status_code: int = 502):
        super().__init__(message)
        self.status_code = status_code


def _read_story_change_page(client: httpx.Client, base: str, path: str) -> dict:
    response = client.get(f"{base}/{path}")
    try:
        payload = _loads_lenient(response.text)
        if payload.get("status") != "success":
            raise ValueError("not a page response")
        data = payload.get("data")
        data = _loads_lenient(data) if isinstance(data, str) else data
        if not isinstance(data, dict) or data.get("loginExpired"):
            raise ValueError("no page data")
        return data
    except (ValueError, AttributeError, TypeError):
        raise ZentaoStoryChangeError("无法读取禅道需求变更内容，请检查账号权限或重新绑定禅道账号")


def _story_change_account(task: dict) -> str:
    account = task.get("assignedTo")
    if isinstance(account, dict):
        account = account.get("account")
    return str(account or "").strip().lower()


def _check_single_story_change_task(task: dict) -> None:
    # Team acknowledgement is per member; a task-level cache cannot represent it.
    if task.get("team") or task.get("mode") in {"linear", "multi"}:
        raise ZentaoStoryChangeError("多人任务请在禅道中按成员确认需求变更", 409)
    if not task.get("story"):
        raise ZentaoStoryChangeError("该任务未关联研发需求", 409)


def get_task_story_change_via_web(login: ZentaoWebLogin, task_id: int) -> dict:
    """Read the task and versioned story in the caller's web session. No writes."""
    base = login.base_url.rstrip("/")
    client = _open_web_session(login)
    try:
        task = _read_story_change_page(client, base, f"task-view-{task_id}.json").get("task")
        if not isinstance(task, dict) or str(task.get("id")) != str(task_id):
            raise ZentaoStoryChangeError("禅道任务不存在或无权访问", 404)
        _check_single_story_change_task(task)
        story_id = int(task["story"])
        latest = int(task.get("latestStoryVersion") or 0)
        version = int(task.get("storyVersion") or 0)
        if not latest or not version:
            raise ZentaoStoryChangeError("禅道未返回需求版本，暂时无法确认", 409)
        page = _read_story_change_page(client, base, f"story-view-{story_id}-{latest}.json")
        story = page.get("story")
        if not isinstance(story, dict) or str(story.get("id")) != str(story_id) or int(page.get("version") or 0) != latest:
            raise ZentaoStoryChangeError("读取的需求版本已发生变化，请刷新后查看", 409)
        previous = None
        previous_error = None
        if version != latest:
            try:
                old = _read_story_change_page(client, base, f"story-view-{story_id}-{version}.json")
                if int(old.get("version") or 0) != version or str((old.get("story") or {}).get("id")) != str(story_id):
                    raise ZentaoStoryChangeError("旧版需求内容暂不可用")
                previous = old["story"]
            except (ZentaoWebSessionError, httpx.HTTPError):
                previous_error = "旧版需求内容暂不可用，请查看最新版及禅道中的变更记录"
        return {"task": task, "story": story, "previous_story": previous, "previous_error": previous_error,
                "can_confirm": _story_change_account(task) == login.account.strip().lower(),
                "story_version": version, "latest_story_version": latest}
    finally:
        _logout_quietly(client, base)
        client.close()


def confirm_task_story_change_via_web(
    login: ZentaoWebLogin, task_id: int, *, story_id: int,
    expected_story_version: int, expected_latest_story_version: int,
) -> dict:
    """Precheck, acknowledge once, then read back in the same personal session.

    The upstream endpoint itself mutates on GET. Never use the form helper,
    whose initial GET would confirm before validating what the user read.
    """
    from app.services.zentao_task_status import task_story_change_pending

    base = login.base_url.rstrip("/")
    client = _open_web_session(login)
    try:
        task = _read_story_change_page(client, base, f"task-view-{task_id}.json").get("task")
        if not isinstance(task, dict) or str(task.get("id")) != str(task_id):
            raise ZentaoStoryChangeError("禅道任务不存在或无权访问", 404)
        _check_single_story_change_task(task)
        if _story_change_account(task) != login.account.strip().lower():
            raise ZentaoStoryChangeError("该禅道任务当前未指派给你，无法确认需求变更", 403)
        if int(task.get("story") or 0) != story_id or int(task.get("latestStoryVersion") or 0) != expected_latest_story_version:
            raise ZentaoStoryChangeError("需求关联或最新版本已发生变化，请刷新并阅读最新内容后再确认", 409)
        if task_story_change_pending(task) is False and int(task.get("storyVersion") or 0) == expected_latest_story_version:
            return {"task": task, "already_confirmed": True, "version_changed_during_confirm": False}
        if int(task.get("storyVersion") or 0) != expected_story_version:
            raise ZentaoStoryChangeError("任务已记录的需求版本发生变化，请刷新后再确认", 409)
        write_error = None
        try:
            response = client.get(f"{base}/task-confirmStoryChange-{task_id}.html",
                                  headers={"X-Requested-With": "XMLHttpRequest"})
            payload = _loads_lenient(response.text)
            if payload.get("result") != "success":
                write_error = "禅道未接受确认，请检查确认权限"
        except (httpx.HTTPError, ValueError, AttributeError):
            write_error = "确认响应未收到"
        # A failed or timed-out write may have taken effect. Do not send again.
        try:
            fresh = _read_story_change_page(client, base, f"task-view-{task_id}.json").get("task")
        except (ZentaoWebSessionError, httpx.HTTPError):
            raise ZentaoStoryChangeError("确认结果待核实，请刷新查看任务状态，勿重复提交")
        if not isinstance(fresh, dict) or str(fresh.get("id")) != str(task_id) or int(fresh.get("story") or 0) != story_id:
            raise ZentaoStoryChangeError("确认结果待核实：任务关联已发生变化，请刷新查看")
        confirmed_version = int(fresh.get("storyVersion") or 0)
        if confirmed_version < expected_latest_story_version:
            raise ZentaoStoryChangeError(write_error or "需求确认未生效，请刷新后查看")
        return {"task": fresh, "already_confirmed": False,
                "version_changed_during_confirm": confirmed_version != expected_latest_story_version
                or int(fresh.get("latestStoryVersion") or 0) != expected_latest_story_version
                or task_story_change_pending(fresh) is not False}
    finally:
        _logout_quietly(client, base)
        client.close()


def _parse_page_data(text: str) -> dict:
    """禅道页面 .json 响应：{"status":"success","data":"<json字符串>"} → data dict。"""
    payload = _loads_lenient(text)
    if not (isinstance(payload, dict) and payload.get("status") == "success"):
        raise ZentaoWebSessionError(f"禅道页面 JSON 状态异常：{str(payload)[:200]}")
    data = payload.get("data")
    if isinstance(data, str):
        data = _loads_lenient(data)
    if not isinstance(data, dict):
        raise ZentaoWebSessionError("禅道页面 JSON 缺少 data")
    return data


def _normalize_efforts(raw) -> list[dict]:
    """efforts 字段（dict 按 id 键 / list 均可能）→ 统一的记录列表。"""
    items = list(raw.values()) if isinstance(raw, dict) else (raw if isinstance(raw, list) else [])
    out: list[dict] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        try:
            out.append({
                "id": int(it.get("id") or 0),
                "date": str(it.get("date") or "")[:10],
                "consumed": float(it.get("consumed") or 0.0),
                "left": float(it.get("left") or 0.0),
                "account": str(it.get("account") or ""),
                "work": str(it.get("work") or ""),
            })
        except (TypeError, ValueError):
            continue
    out.sort(key=lambda r: (r["date"], r["id"]))
    return out


def _fetch_efforts(client: httpx.Client, base: str, task_id: int) -> list[dict]:
    try:
        resp = client.get(f"{base}/effort-createForObject-task-{task_id}.json")
    except httpx.HTTPError as exc:
        raise ZentaoWebSessionError('禅道工时读取失败，请稍后刷新') from exc
    if getattr(resp, 'status_code', 200) != 200:
        raise ZentaoWebSessionError("禅道工时读取失败，无法核对操作结果")
    try:
        data = _parse_page_data(resp.text)
    except (ValueError, TypeError) as exc:
        raise ZentaoWebSessionError('禅道工时响应格式异常，无法核对操作结果') from exc
    if ("efforts" not in data or data.get('denyType') or data.get('loginExpired')
            or data.get('title') == '用户登录'):
        # denyPage（无权限）或版本差异：明确报错，调用方走兜底
        raise ZentaoWebSessionError(f"禅道工时页面无 efforts 数据：{str({k: data.get(k) for k in ('title', 'denyType', 'module', 'method')})}")
    raw = data['efforts']
    if not isinstance(raw, (dict, list)):
        raise ZentaoWebSessionError('禅道工时列表格式异常，无法核对操作结果')
    rows = _normalize_efforts(raw)
    ids = [row['id'] for row in rows]
    if len(rows) != len(raw) or any(oid <= 0 for oid in ids) or len(ids) != len(set(ids)):
        raise ZentaoWebSessionError('禅道工时列表存在无效记录，无法核对操作结果')
    return rows


def _poll_efforts(client, base, task_id, matches):
    """Only retry reads. A failed read never establishes absence or success."""
    latest = None
    for delay in _EFFORT_POLL_DELAYS:
        if delay:
            time.sleep(delay)
        try:
            latest = _fetch_efforts(client, base, task_id)
        except Exception:
            latest = None
            continue
        if matches(latest):
            return latest, True
    return latest, False


def _effort_signature(row, account):
    return (str(row.get('account', account) or '').strip().lower(), str(row['date'])[:10],
            round(float(row['consumed']), 2), round(float(row.get('left') or 0), 2),
            str(row.get('work') or ''))


def _post_effort_and_reconcile(client, base, task_id, path, fields, matches, action):
    """Send once, including on timeout; confirm the requested state by reading."""
    write_error = None
    try:
        resp = _guarded_form_post(
            client, f'{base}/{path}', files=_form(fields), timeout=_EFFORT_WRITE_TIMEOUT,
            headers={"X-Requested-With": "XMLHttpRequest", "X-Zui-Modal": "true"},
        )
    except ZentaoWebSessionError as exc:
        if not exc.outcome_unknown:
            raise
        write_error = exc
    else:
        try:
            payload = _loads_lenient(resp.text)
        except Exception:
            payload = None
        if isinstance(payload, dict) and any(payload.get(k) in ('fail', 'failed', 'error')
                                             for k in ('result', 'status')):
            raise ZentaoWebSessionError(f'禅道拒绝{action}，请检查记录内容和账号权限')
    after, confirmed = _poll_efforts(client, base, task_id, matches)
    if confirmed:
        return after
    raise ZentaoWebSessionError(
        f'禅道{action}结果待核实，回读尚未确认；请先刷新工时记录，勿重复提交',
        outcome_unknown=True,
    ) from write_error


def list_task_efforts_via_web(login: ZentaoWebLogin, task_id: int) -> list[dict]:
    """查看任务的工时记录列表。失败抛 ZentaoWebSessionError。"""
    base = login.base_url.rstrip("/")
    client = _open_web_session(login)
    try:
        return _fetch_efforts(client, base, task_id)
    finally:
        _logout_quietly(client, base)
        client.close()


def record_task_efforts_via_web(login: ZentaoWebLogin, task_id: int, rows: list[dict]) -> list[dict]:
    """提交工时记录（可多行、各行可不同日期，dates 支持补录历史日期）。

    rows: [{"date": "YYYY-MM-DD", "consumed": float, "left": float, "work": str}]。
    任务类型的日志「剩余 left」必填（缺了报"剩余不能为空"），禅道会把 consumed
    累加进任务总消耗。只提交一次，超时后仍按新增 ID、本人、日期、工时、
    剩余和内容核对；未能确认时保留结果未知标记，禁止调用方自动补交。
    """
    rows = [r for r in rows if float(r.get("consumed") or 0.0) > 0]
    if not rows:
        return []
    base = login.base_url.rstrip("/")
    client = _open_web_session(login)
    try:
        before = _fetch_efforts(client, base, task_id)
        fields: dict[str, str] = {}
        for i, r in enumerate(rows, start=1):
            fields[f"id[{i}]"] = "0"
            fields[f"objectType[{i}]"] = "task"
            fields[f"objectID[{i}]"] = str(task_id)
            fields[f"dates[{i}]"] = str(r["date"])
            fields[f"work[{i}]"] = str(r.get("work") or "")
            fields[f"consumed[{i}]"] = str(r["consumed"])
            fields[f"left[{i}]"] = str(r.get("left") if r.get("left") is not None else 0)
        before_ids = {item['id'] for item in before}
        expected = Counter(_effort_signature(row, login.account) for row in rows)

        def matches(after):
            actual = Counter(_effort_signature(item, login.account) for item in after
                             if item['id'] > 0 and item['id'] not in before_ids)
            return all(actual[signature] == count for signature, count in expected.items())

        return _post_effort_and_reconcile(
            client, base, task_id, f'effort-createForObject-task-{task_id}.html',
            fields, matches, '新增工时',
        )
    finally:
        _logout_quietly(client, base)
        client.close()


def edit_task_effort_via_web(
    login: ZentaoWebLogin,
    task_id: int,
    effort_id: int,
    *,
    date: str,
    consumed: float,
    left: float,
    work: str = "",
) -> list[dict]:
    """编辑一条工时记录（日期/消耗小时/剩余/内容）。字段 2026-07-17 对 ipd4.3 实测：
    date（必填，日期控件 JS 渲染、静态页抓不到）+ objectType/objectID/consumed/left/work。
    回读校验日期与消耗确实变化，未生效抛 ZentaoWebSessionError。
    返回编辑后的完整记录列表。"""
    base = login.base_url.rstrip("/")
    client = _open_web_session(login)
    try:
        before = _fetch_efforts(client, base, task_id)
        if not any(row['id'] == int(effort_id) for row in before):
            raise ZentaoWebSessionError('工时记录不属于当前任务或已被删除')
        fields = {
            "date": str(date)[:10],
            "objectType": "task",
            "objectID": str(task_id),
            "consumed": str(consumed),
            "left": str(left),
            "work": work,
        }
        expected = _effort_signature(fields, login.account)

        def matches(after):
            hit = next((row for row in after if row['id'] == int(effort_id)), None)
            return hit is not None and _effort_signature(hit, login.account) == expected

        return _post_effort_and_reconcile(
            client, base, task_id, f'effort-edit-{effort_id}.html', fields, matches, '修改工时',
        )
    finally:
        _logout_quietly(client, base)
        client.close()


def delete_task_effort_via_web(
    login: ZentaoWebLogin,
    task_id: int,
    effort_id: int,
) -> list[dict]:
    """删除一条工时记录，并以禅道回读结果作为唯一成功依据。

    优先使用 IPD 5.6 effort 删除页的完整二次确认地址；旧版 task-deleteWorkhour
    作为兼容路径。两条路径始终只操作同一个 effort_id。目标记录仍存在则抛错，
    调用方绝不能把它当作删除成功。
    """
    base = login.base_url.rstrip("/")
    client = _open_web_session(login)
    try:
        before = _fetch_efforts(client, base, task_id)
        if not any(r["id"] == int(effort_id) for r in before):
            return before

        urls = [
            f"{base}/effort-delete-{effort_id}-yes-.html",
            f"{base}/task-deleteWorkhour-{effort_id}-yes.html",
        ]
        uncertain = False
        for url in urls:
            try:
                resp = client.get(
                    url, timeout=_EFFORT_WRITE_TIMEOUT,
                    headers={"X-Requested-With": "XMLHttpRequest", "X-Zui-Modal": "true"},
                )
                try:
                    payload = _loads_lenient(resp.text)
                except Exception:
                    payload = None
                rejected = isinstance(payload, dict) and any(payload.get(k) in ('fail', 'failed', 'error')
                                                            for k in ('result', 'status'))
                status = getattr(resp, 'status_code', 200)
                uncertain = uncertain or status >= 500 or (not rejected and status < 400)
            except Exception:
                uncertain = True
            after, confirmed = _poll_efforts(
                client, base, task_id, lambda rows: all(row['id'] != int(effort_id) for row in rows),
            )
            if confirmed:
                return after
            if after is None:
                raise ZentaoWebSessionError('禅道删除工时后无法回读，请刷新核实，勿重复提交', outcome_unknown=True)
            # A compatible delete may be attempted only for this exact same ID,
            # after a successful read still shows it. Never target another row.

        raise ZentaoWebSessionError(
            f"禅道删除工时未确认，记录 #{effort_id} 回读后仍存在，请刷新核实", outcome_unknown=uncertain,
        )
    finally:
        _logout_quietly(client, base)
        client.close()


__all__ = [
    "ZentaoWebLogin",
    "ZentaoWebSessionError",
    "pause_task_via_web",
    "cancel_task_via_web",
    "start_task_via_web",
    "restart_task_via_web",
    "finish_task_via_web",
    "close_task_via_web",
    "list_task_efforts_via_web",
    "record_task_efforts_via_web",
    "edit_task_effort_via_web",
    "delete_task_effort_via_web",
]
