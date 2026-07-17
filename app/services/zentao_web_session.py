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
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)

_TIMEOUT = 15.0
# 禅道登录对非浏览器 UA 直接打回登录页（无错误提示），必须伪装浏览器。
_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36"
)


@dataclass
class ZentaoWebLogin:
    base_url: str
    account: str
    password: str  # 明文（来自绑定密文解密）


class ZentaoWebSessionError(Exception):
    pass


def _md5(text: str) -> str:
    return hashlib.md5(text.encode()).hexdigest()


def _loads_lenient(text: str):
    return json.loads((text or "").strip("﻿ \t\r\n"))


def _form(fields: dict[str, str]) -> dict:
    """httpx 的 multipart 字段格式（禅道网页表单为 multipart/form-data）。"""
    return {k: (None, v) for k, v in fields.items()}


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
    ) as client:
        _login(client, login)
        resp = client.post(
            f"{base}/task-{action}-{task_id}.html",
            # status 是动作弹窗的隐藏字段，缺了服务端只当作渲染表单页，不执行动作
            files=_form({"comment": comment or "", "uid": "", "status": status}),
            headers={"X-Requested-With": "XMLHttpRequest", "X-Zui-Modal": "true"},
        )
        try:
            payload = _loads_lenient(resp.text)
        except Exception:
            raise ZentaoWebSessionError(f"禅道{zh}返回非 JSON：{(resp.text or '')[:200]}")
        if not (isinstance(payload, dict) and payload.get("result") == "success"):
            raise ZentaoWebSessionError(f"禅道{zh}未执行：{_fail_reason(payload)}")
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


# ── 工时记录（effort / taskestimate）───────────────────────────────────────────
# REST（ipd4.3）没有工时记录端点，只能走网页表单：
#   查看：GET  task-recordEstimate-{taskID}.json   → data.estimates
#   提交：POST task-recordEstimate-{taskID}.html   → dates[i]/consumed[i]/left[i]/work[i]
#   编辑：POST task-editEstimate-{effortID}.html   → dates|date/consumed/left/work
# ⚠️ 表单字段名未对本部署抓包实测（协议按开源禅道建模），因此所有写操作都
# 「提交 → 同会话回读校验」，未生效抛 ZentaoWebSessionError（编辑另备字段名变体重试），
# 由调用方决定兜底（如退回本地累计、完成时一次性提交）。


def _open_web_session(login: ZentaoWebLogin) -> httpx.Client:
    """打开已登录的网页会话（调用方负责 close；登出失败不致命）。"""
    client = httpx.Client(
        timeout=_TIMEOUT,
        follow_redirects=True,
        headers={"User-Agent": _BROWSER_UA, "Referer": f"{login.base_url.rstrip('/')}/"},
    )
    try:
        _login(client, login)
    except Exception:
        client.close()
        raise
    return client


def _logout_quietly(client: httpx.Client, base: str) -> None:
    try:
        client.get(f"{base}/user-logout.html")
    except Exception:  # noqa: BLE001
        pass


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
    """estimates 字段（dict 按 id 键 / list 均可能）→ 统一的记录列表。"""
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
    resp = client.get(f"{base}/task-recordEstimate-{task_id}.json")
    data = _parse_page_data(resp.text)
    return _normalize_efforts(data.get("estimates"))


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
    """提交工时记录（可多行、各行可不同日期）。

    rows: [{"date": "YYYY-MM-DD", "consumed": float, "left": float, "work": str}]，
    禅道会把 consumed 累加进任务总消耗、用最后一行的 left 更新任务剩余。
    回读校验记录条数确实增加，未生效抛 ZentaoWebSessionError。
    返回提交后的完整记录列表。
    """
    rows = [r for r in rows if float(r.get("consumed") or 0.0) > 0]
    if not rows:
        return []
    base = login.base_url.rstrip("/")
    client = _open_web_session(login)
    try:
        before = _fetch_efforts(client, base, task_id)
        fields: dict[str, str] = {"uid": ""}
        for i, r in enumerate(rows, start=1):
            fields[f"id[{i}]"] = "0"
            fields[f"dates[{i}]"] = str(r["date"])
            fields[f"consumed[{i}]"] = str(r["consumed"])
            fields[f"left[{i}]"] = str(r.get("left") if r.get("left") is not None else 0)
            fields[f"work[{i}]"] = str(r.get("work") or "")
        resp = client.post(
            f"{base}/task-recordEstimate-{task_id}.html",
            files=_form(fields),
            headers={"X-Requested-With": "XMLHttpRequest", "X-Zui-Modal": "true"},
        )
        try:
            payload = _loads_lenient(resp.text)
        except Exception:
            raise ZentaoWebSessionError(f"禅道记录工时返回非 JSON：{(resp.text or '')[:200]}")
        if not (isinstance(payload, dict) and payload.get("result") == "success"):
            raise ZentaoWebSessionError(f"禅道记录工时未执行：{_fail_reason(payload)}")
        after = _fetch_efforts(client, base, task_id)
        if len(after) < len(before) + len(rows):
            raise ZentaoWebSessionError(
                f"禅道记录工时未生效：提交 {len(rows)} 条，记录数 {len(before)} → {len(after)}"
            )
        return after
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
    """编辑一条工时记录。回读校验生效；字段名按「dates → date」两个变体依次尝试
    （编辑表单字段名未实测，禅道版本间有差异）。返回编辑后的完整记录列表。"""
    base = login.base_url.rstrip("/")
    client = _open_web_session(login)
    try:
        last_reason = ""
        for date_field in ("dates", "date"):
            fields = {date_field: date, "consumed": str(consumed), "left": str(left), "work": work, "uid": ""}
            resp = client.post(
                f"{base}/task-editEstimate-{effort_id}.html",
                files=_form(fields),
                headers={"X-Requested-With": "XMLHttpRequest", "X-Zui-Modal": "true"},
            )
            try:
                payload = _loads_lenient(resp.text)
            except Exception:
                last_reason = f"返回非 JSON：{(resp.text or '')[:200]}"
                continue
            if not (isinstance(payload, dict) and payload.get("result") == "success"):
                last_reason = _fail_reason(payload)
                continue
            after = _fetch_efforts(client, base, task_id)
            hit = next((r for r in after if r["id"] == int(effort_id)), None)
            if hit and hit["date"] == date[:10] and abs(hit["consumed"] - float(consumed)) < 0.005:
                return after
            last_reason = f"提交成功但记录未变化（当前：{hit}）"
        raise ZentaoWebSessionError(f"禅道编辑工时未生效：{last_reason}")
    finally:
        _logout_quietly(client, base)
        client.close()


__all__ = [
    "ZentaoWebLogin",
    "ZentaoWebSessionError",
    "pause_task_via_web",
    "cancel_task_via_web",
    "list_task_efforts_via_web",
    "record_task_efforts_via_web",
    "edit_task_effort_via_web",
]
