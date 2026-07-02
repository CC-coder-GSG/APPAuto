"""
Zentao 网页会话客户端（cookie 登录，非 API Token）。

为什么需要它：本部署（禅道 ipd4.3）的任务「暂停」两条常规路径都不可用——
  1. REST `POST /v1/tasks/{id}/pause` 未实现：返回 200 + 任务原样，状态不变；
  2. 传统页面动作带 Token 头会被 ACL 拦截：`{"result":"fail","alert":"您无权访问该迭代！"}`
     （API Token 会话未加载用户的迭代访问权限，同一账号网页登录则正常）。

唯一生效的方式是模拟真实网页登录（zentaosid cookie 会话）后提交暂停表单。
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


def pause_task_via_web(login: ZentaoWebLogin, task_id: int, *, comment: str | None = None) -> dict:
    """网页会话暂停任务（status→pause）。成功返回禅道响应，失败抛 ZentaoWebSessionError。"""
    base = login.base_url.rstrip("/")
    with httpx.Client(
        timeout=_TIMEOUT,
        follow_redirects=True,
        headers={"User-Agent": _BROWSER_UA, "Referer": f"{base}/"},
    ) as client:
        _login(client, login)
        resp = client.post(
            f"{base}/task-pause-{task_id}.html",
            # status=pause 是暂停弹窗的隐藏字段，缺了服务端不执行动作
            files=_form({"comment": comment or "", "uid": "", "status": "pause"}),
            headers={"X-Requested-With": "XMLHttpRequest", "X-Zui-Modal": "true"},
        )
        try:
            payload = _loads_lenient(resp.text)
        except Exception:
            raise ZentaoWebSessionError(f"禅道暂停返回非 JSON：{(resp.text or '')[:200]}")
        if not (isinstance(payload, dict) and payload.get("result") == "success"):
            raise ZentaoWebSessionError(f"禅道暂停未执行：{_fail_reason(payload)}")
        try:
            client.get(f"{base}/user-logout.html")
        except Exception:
            pass
        return payload


__all__ = ["ZentaoWebLogin", "ZentaoWebSessionError", "pause_task_via_web"]
