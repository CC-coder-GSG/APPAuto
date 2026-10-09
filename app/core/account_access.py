from fastapi import HTTPException

from app.utils.time_utils import utc_now_naive


def ensure_active_account(user):
    if not user.is_active or (user.expires_at and user.expires_at <= utc_now_naive()):
        raise HTTPException(status_code=401, detail="账号已停用或已到期")


def ensure_account_route(user, request):
    """An external session never authorizes any of the existing internal APIs."""
    if user.account_type != "external":
        return
    path, method = request.url.path, request.method
    if (method, path) in {
        ("GET", "/auth/me"), ("PUT", "/auth/password"), ("POST", "/auth/logout"),
    }:
        return
    if method == "GET" and path.startswith("/api/bugly/external/"):
        return
    raise HTTPException(status_code=403, detail="外部账号仅可访问获授权的 Bugly 发布内容")
