"""
Jenkins integration API.

每个用户用自己的 Jenkins 账号 + API Token 绑定，由本系统代为触发"自动化测试"视图下
已配置好的 Job：

- GET    /api/jenkins/binding/me        — 查看当前绑定（不回传 Token）
- PUT    /api/jenkins/binding/me        — 创建 / 更新绑定
- POST   /api/jenkins/binding/me/test   — 测试凭据（不保存）
- DELETE /api/jenkins/binding/me        — 删除绑定
- GET    /api/jenkins/jobs              — 列视图下的 Job（默认 APP_JENKINS_DEFAULT_VIEW）
- GET    /api/jenkins/jobs/{job}        — 取单个 Job 状态 / 参数
- POST   /api/jenkins/jobs/{job}/build  — 触发构建（可带参数）
- GET    /api/jenkins/queue?url=...     — 查询触发后的队列项 → 真实 build number
"""
from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Optional
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse
from jose import JWTError, jwt
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.core.auth import normalize_client_type, session_token_for
from app.core.config import settings
from app.models import User
from app.services import jenkins_service
from app.services.audit_service import audit
from app.services.jenkins_client import JenkinsError

logger = logging.getLogger(__name__)

router = APIRouter()

# Path under which the Jenkins reverse proxy is mounted. The embedded native
# build page (Active Choices 级联参数) loads through here so it is same-origin
# (绕过 X-Frame-Options) 且由后端注入 Basic Auth，前端不接触 Jenkins 凭据。
_PROXY_PREFIX = "/jenkins/proxy"
_PROXY_AUTH_COOKIE = "jp_auth"

# Root-relative Jenkins path segments that must be re-pointed at the proxy when
# they appear inside proxied HTML / JS, so all assets + AJAX (含 stapler 级联)
# 仍然走我们的代理。
_REWRITE_SEGMENTS = (
    "static|adjuncts|jsbundles|scripts|images|i18n|ajax|sse-gateway|fingerprint|"
    r"userContent|\$stapler|descriptorByName|crumbIssuer|job|view|user|me|computer|"
    "asynchPeople|search|checkJobName|api"
)
_REWRITE_RE = re.compile(r'(["\'(=])/(' + _REWRITE_SEGMENTS + r')(["\'/?#])')


def _rewrite_body(text: str) -> str:
    text = _REWRITE_RE.sub(lambda m: f'{m.group(1)}{_PROXY_PREFIX}/{m.group(2)}{m.group(3)}', text)
    # Jenkins JS derives AJAX roots from data-rooturl (empty = site root).
    text = text.replace('data-rooturl=""', f'data-rooturl="{_PROXY_PREFIX}"')
    return text


def _user_from_token_value(token: str | None, db: Session) -> User | None:
    if not token:
        return None
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm])
    except JWTError:
        return None
    username = payload.get("sub")
    session_token = payload.get("session")
    client_type = normalize_client_type(payload.get("client"))
    if not username:
        return None
    user = db.query(User).filter(User.username == username).first()
    if not user or session_token_for(user, client_type) != session_token:
        return None
    return user


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class JenkinsBindingPayload(BaseModel):
    base_url: str = Field(min_length=1, max_length=500, description="Jenkins 地址，如 http://192.168.2.229:8080")
    jenkins_account: str = Field(min_length=1, max_length=120)
    jenkins_token: str = Field(min_length=1, max_length=256, description="Jenkins API Token（明文，服务端加密存储）")


class JenkinsBuildPayload(BaseModel):
    params: Optional[dict] = Field(default=None, description="可选的构建参数键值对")


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _serialize_binding(b) -> dict:
    return {
        "id": b.id,
        "user_id": b.user_id,
        "base_url": b.base_url,
        "jenkins_account": b.jenkins_account,
        "has_token": bool(b.jenkins_token_ciphertext),
        "last_check_at": _iso(b.last_check_at),
        "last_check_status": b.last_check_status,
        "last_error_message": b.last_error_message,
        "created_at": _iso(b.created_at),
        "updated_at": _iso(b.updated_at),
    }


def _require_client(db: Session, user_id: int):
    client = jenkins_service.get_client_for_user(db, user_id)
    if not client:
        raise HTTPException(status_code=400, detail="当前用户尚未配置 Jenkins 绑定，请先在「自动化测试」中绑定账号")
    return client


# ---------------------------------------------------------------------------
# Binding endpoints
# ---------------------------------------------------------------------------

@router.get("/jenkins/binding/me")
def get_my_jenkins_binding(current_user=Depends(get_current_user), db: Session = Depends(get_db)):
    binding = jenkins_service.get_binding(db, current_user.id)
    return {
        "binding": _serialize_binding(binding) if binding else None,
        "defaults": {
            "base_url": settings.jenkins_default_base_url,
            "view": settings.jenkins_default_view,
        },
    }


@router.put("/jenkins/binding/me")
def upsert_my_jenkins_binding(
    payload: JenkinsBindingPayload,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    binding = jenkins_service.upsert_binding(
        db,
        current_user.id,
        base_url=payload.base_url,
        account=payload.jenkins_account,
        token=payload.jenkins_token,
    )
    # Validate the freshly saved credential and record the result.
    ok, message = jenkins_service.test_credentials(
        payload.base_url, payload.jenkins_account, payload.jenkins_token
    )
    jenkins_service.record_check_result(db, current_user.id, ok=ok, message=message)
    db.refresh(binding)
    return {"ok": True, "verified": ok, "message": message, "binding": _serialize_binding(binding)}


@router.post("/jenkins/binding/me/test")
def test_my_jenkins_binding(payload: JenkinsBindingPayload, current_user=Depends(get_current_user)):
    ok, message = jenkins_service.test_credentials(
        payload.base_url, payload.jenkins_account, payload.jenkins_token
    )
    return {"ok": ok, "message": message}


@router.delete("/jenkins/binding/me")
def delete_my_jenkins_binding(current_user=Depends(get_current_user), db: Session = Depends(get_db)):
    removed = jenkins_service.delete_binding(db, current_user.id)
    if not removed:
        raise HTTPException(status_code=404, detail="当前用户未配置 Jenkins 绑定")
    return {"ok": True, "message": "Jenkins 绑定已删除"}


# ---------------------------------------------------------------------------
# Job endpoints
# ---------------------------------------------------------------------------

@router.get("/jenkins/jobs")
def list_jenkins_jobs(
    view: Optional[str] = Query(default=None, description="Jenkins 视图名，默认取配置的自动化测试视图"),
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    client = _require_client(db, current_user.id)
    view_name = (view or settings.jenkins_default_view or "").strip()
    if not view_name:
        raise HTTPException(status_code=400, detail="未指定 Jenkins 视图名")
    try:
        jobs = client.list_view_jobs(view_name)
    except JenkinsError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    return {"view": view_name, "jobs": jobs}


@router.get("/jenkins/jobs/{job_name:path}")
def get_jenkins_job(job_name: str, current_user=Depends(get_current_user), db: Session = Depends(get_db)):
    client = _require_client(db, current_user.id)
    try:
        return client.get_job(job_name)
    except JenkinsError as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@router.post("/jenkins/jobs/{job_name:path}/build")
def trigger_jenkins_job(
    job_name: str,
    payload: JenkinsBuildPayload | None = None,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    client = _require_client(db, current_user.id)
    params = payload.params if payload else None
    try:
        queue_url = client.trigger_build(job_name, params=params)
    except JenkinsError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    return {
        "ok": True,
        "message": f"已触发 Job「{job_name}」构建",
        "queue_item_url": queue_url,
        "triggered_by": current_user.shown_name,
    }


@router.get("/jenkins/queue")
def get_jenkins_queue_item(
    url: str = Query(..., description="触发返回的 queue item URL"),
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    client = _require_client(db, current_user.id)
    try:
        data = client.get_queue_item(url)
    except JenkinsError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    executable = data.get("executable") if isinstance(data, dict) else None
    build_number = executable.get("number") if isinstance(executable, dict) else None
    build_url = executable.get("url") if isinstance(executable, dict) else None
    return {
        "cancelled": bool(data.get("cancelled")) if isinstance(data, dict) else False,
        "why": data.get("why") if isinstance(data, dict) else None,
        "started": build_number is not None,
        "build_number": build_number,
        "build_url": build_url,
    }


# ---------------------------------------------------------------------------
# View / build detail / artifact download
# ---------------------------------------------------------------------------

@router.get("/jenkins/views")
def list_jenkins_views(current_user=Depends(get_current_user), db: Session = Depends(get_db)):
    client = _require_client(db, current_user.id)
    try:
        views = client.list_views()
    except JenkinsError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    return {"views": views, "default_view": settings.jenkins_default_view}


@router.get("/jenkins/build")
def get_jenkins_build_detail(
    job: str = Query(..., description="Job 全名"),
    number: int = Query(..., description="构建号"),
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    client = _require_client(db, current_user.id)
    try:
        return client.get_build(job, number)
    except JenkinsError as exc:
        raise HTTPException(status_code=502, detail=str(exc))


_PERMALINKS = {"lastSuccessfulBuild", "lastBuild", "lastStableBuild", "lastCompletedBuild"}


@router.get("/jenkins/download")
async def download_jenkins_artifact(
    request: Request,
    job: str = Query(..., description="Job 全名"),
    path: str = Query(..., description="产物相对路径 relativePath"),
    number: str = Query("lastSuccessfulBuild", description="构建号或 permalink"),
    db: Session = Depends(get_db),
):
    """后端流式代理下载 Jenkins 产物，凭据由后端注入，前端不接触 Jenkins 账号。"""
    user = _resolve_flexible_user(request, db)
    if not user:
        raise HTTPException(status_code=401, detail="未认证")
    creds = jenkins_service.get_binding_creds(db, user.id)
    if not creds:
        raise HTTPException(status_code=400, detail="当前用户未配置 Jenkins 绑定")
    base_url, account, tok = creds

    if ".." in path or path.startswith("/"):
        raise HTTPException(status_code=400, detail="非法的产物路径")
    seg = number if (number.isdigit() or number in _PERMALINKS) else "lastSuccessfulBuild"
    job_path = "/".join(f"job/{quote(s)}" for s in job.split("/") if s)
    art_url = f"{base_url}/{job_path}/{seg}/artifact/{path}"

    client = httpx.AsyncClient(auth=(account, tok), timeout=httpx.Timeout(30.0, read=None))
    upstream = await client.send(client.build_request("GET", art_url), stream=True)
    if upstream.status_code >= 400:
        code = upstream.status_code
        await upstream.aclose()
        await client.aclose()
        raise HTTPException(
            status_code=code if code in (401, 403, 404) else 502,
            detail=f"Jenkins 产物下载失败（{code}）",
        )

    audit(
        db, action="jenkins.artifact.download", target_type="jenkins_artifact",
        actor_id=user.id, target_id=job, detail=f"{seg}/{path}",
    )

    async def _stream():
        try:
            async for chunk in upstream.aiter_bytes(65536):
                yield chunk
        finally:
            await upstream.aclose()
            await client.aclose()

    filename = path.split("/")[-1]
    headers = {"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"}
    cl = upstream.headers.get("content-length")
    if cl:
        headers["Content-Length"] = cl
    media = (upstream.headers.get("content-type") or "application/octet-stream").split(";")[0]
    return StreamingResponse(_stream(), media_type=media, headers=headers)


# ---------------------------------------------------------------------------
# Reverse proxy (embeds the native Jenkins parameterized-build page)
# ---------------------------------------------------------------------------

def _resolve_flexible_user(request: Request, db: Session) -> User | None:
    """Resolve the user from Bearer header, the proxy auth cookie, or ?__jp_auth=."""
    token = request.query_params.get("__jp_auth") or request.cookies.get(_PROXY_AUTH_COOKIE)
    auth_header = request.headers.get("authorization", "")
    if auth_header.lower().startswith("bearer "):
        token = token or auth_header[7:]
    return _user_from_token_value(token, db)


# 每个用户保留一份服务端 Jenkins 会话 cookie jar。Stapler 的 st:bind 对象绑定在
# 渲染页面时的 HTTP 会话上，后续 Active Choices 的 getChoicesForUI/doUpdate AJAX
# 必须命中同一会话；若把 Jenkins 的 Set-Cookie 透传给浏览器，任何一个未带 cookie 的
# 子资源请求都会让 Jenkins 另起新会话并覆盖掉浏览器 cookie，导致绑定对象 404。
# 因此会话只保留在后端、对浏览器隐藏。
_proxy_cookie_jars: dict[int, httpx.Cookies] = {}

# 不向 Jenkins 转发的请求头（逐跳/由我们重设的）。其余全部透传，确保 Jenkins-Crumb
# 等 Active Choices 需要的头不被丢掉。
_PROXY_DROP_REQ_HEADERS = {
    "host", "authorization", "cookie", "content-length", "accept-encoding", "connection",
}


@router.api_route("/jenkins/proxy/{path:path}", methods=["GET", "POST"])
async def jenkins_reverse_proxy(path: str, request: Request, db: Session = Depends(get_db)):
    user = _resolve_flexible_user(request, db)
    if not user:
        raise HTTPException(status_code=401, detail="未认证")
    creds = jenkins_service.get_binding_creds(db, user.id)
    if not creds:
        raise HTTPException(status_code=400, detail="当前用户未配置 Jenkins 绑定")
    base_url, account, tok = creds

    query_params = [(k, v) for k, v in request.query_params.multi_items() if k != "__jp_auth"]
    target = f"{base_url}/{path}"

    fwd_headers = {k: v for k, v in request.headers.items() if k.lower() not in _PROXY_DROP_REQ_HEADERS}
    fwd_headers["accept-encoding"] = "identity"  # 取未压缩响应，便于改写

    jar = _proxy_cookie_jars.setdefault(user.id, httpx.Cookies())
    body = await request.body()
    try:
        async with httpx.AsyncClient(auth=(account, tok), cookies=jar, timeout=60.0, follow_redirects=False) as ac:
            upstream = await ac.request(
                request.method, target, params=query_params,
                content=body or None, headers=fwd_headers,
            )
            _proxy_cookie_jars[user.id] = ac.cookies  # 持久化更新后的会话
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"代理 Jenkins 失败：{exc}")

    ct = upstream.headers.get("content-type", "")
    content = upstream.content
    if ("text/html" in ct) or ("javascript" in ct) or ("text/css" in ct):
        try:
            content = _rewrite_body(content.decode(upstream.encoding or "utf-8", errors="replace")).encode("utf-8")
        except Exception:  # noqa: BLE001
            content = upstream.content

    media = ct.split(";")[0] if ct else "application/octet-stream"
    out = Response(content=content, status_code=upstream.status_code, media_type=media)

    location = upstream.headers.get("location")
    if location:
        if location.startswith(base_url):
            location = _PROXY_PREFIX + location[len(base_url):]
        elif location.startswith("/"):
            location = _PROXY_PREFIX + location
        out.headers["location"] = location

    # 注意：刻意不把 Jenkins 的 Set-Cookie 透传给浏览器（见上方说明）。
    inline_token = request.query_params.get("__jp_auth")
    if inline_token:
        out.set_cookie(
            _PROXY_AUTH_COOKIE, inline_token, path=_PROXY_PREFIX,
            httponly=True, samesite="lax",
        )
    return out


__all__ = ["router"]
