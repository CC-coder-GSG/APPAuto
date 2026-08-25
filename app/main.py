from __future__ import annotations

import logging
import sys
from pathlib import Path

# Windows + Python 3.12 ProactorEventLoop: 客户端在 AcceptEx 握手期间发 RST
# 会导致 finish_accept 抛 OSError [WinError 64] "指定的网络名不再可用"，
# 默认的 _start_serving 看到任意 OSError 就直接 sock.close() 把监听端口关掉，
# 整个进程虽然还活着但已不再 accept，NSSM 也无法感知 → 服务"僵尸化"。
# 这里把 _start_serving 换成一个对常见瞬时 winerror 只重试、不关监听的版本。
if sys.platform == "win32":
    from asyncio import exceptions as _aio_exc
    from asyncio import trsock as _trsock
    from asyncio import windows_events as _win_events
    import logging as _logging

    _win_accept_log = _logging.getLogger("uvicorn.error")
    _TRANSIENT_WINERRS = {53, 64, 121, 1236, 10053, 10054, 10060}

    def _patched_start_serving(
        self,
        protocol_factory,
        sock,
        sslcontext=None,
        server=None,
        backlog=100,
        ssl_handshake_timeout=None,
        ssl_shutdown_timeout=None,
    ):
        trsock_view = _trsock.TransportSocket(sock)

        def accept_loop(f=None):
            try:
                if f is not None:
                    conn, addr = f.result()
                    protocol = protocol_factory()
                    if sslcontext is not None:
                        self._make_ssl_transport(
                            conn,
                            protocol,
                            sslcontext,
                            server_side=True,
                            extra={"peername": addr},
                            server=server,
                            ssl_handshake_timeout=ssl_handshake_timeout,
                            ssl_shutdown_timeout=ssl_shutdown_timeout,
                        )
                    else:
                        self._make_socket_transport(
                            conn,
                            protocol,
                            extra={"peername": addr},
                            server=server,
                        )
                if self.is_closed():
                    return
                f = self._proactor.accept(sock)
            except OSError as exc:
                winerr = getattr(exc, "winerror", None)
                if winerr in _TRANSIENT_WINERRS and sock.fileno() != -1:
                    _win_accept_log.warning(
                        "IOCP accept transient error (winerror=%s): %s — keeping listener alive",
                        winerr,
                        exc,
                    )
                    self.call_soon(accept_loop)
                    return
                if sock.fileno() != -1:
                    self.call_exception_handler(
                        {
                            "message": "Accept failed on a socket",
                            "exception": exc,
                            "socket": trsock_view,
                        }
                    )
                    sock.close()
            except _aio_exc.CancelledError:
                sock.close()
            else:
                self._accept_futures[sock.fileno()] = f
                f.add_done_callback(accept_loop)

        self.call_soon(accept_loop)

    _win_events.ProactorEventLoop._start_serving = _patched_start_serving

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.router import api_router
from app.core.config import settings
from app.core.exceptions import AppError
from app.db.session import SessionLocal
from app.db.init_db import init_db
from app.services.push_service import PushService
from app.services.zentao_background_sync_service import ZentaoBackgroundSyncService

app = FastAPI(title="测量软件测试平台", version="0.3.0")
scheduler = BackgroundScheduler(timezone=settings.scheduler_timezone)
logger = logging.getLogger("uvicorn.error")

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
_cors_origins = settings.cors_allowed_origins
if _cors_origins == ["*"]:
    # Starlette 1.x 只在请求带 Cookie 时才将 allow_all_origins 的响应从 * 换成具体 Origin。
    # Tampermonkey GM_xmlhttpRequest 不带 Cookie，因此用 allow_origin_regex='.*' 走
    # "非通配符但匹配任意来源"路径，Starlette 会始终回显请求中的具体 Origin，
    # 配合 allow_credentials=True 输出合法 CORS 响应。
    _cors_kw: dict = {"allow_origins": [], "allow_origin_regex": ".*", "allow_credentials": True}
else:
    _cors_kw = {"allow_origins": _cors_origins, "allow_credentials": True}
app.add_middleware(
    CORSMiddleware,
    **_cors_kw,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")


@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.message, "code": exc.code})


# HTML 入口页永不强缓存：作为缓存锚点必须每次回源校验，否则浏览器启发式缓存
# 会用旧 index.html（连带加载旧版 JS 模块，导致前端改动「拉了也不生效」）。
_NO_CACHE_HTML = {"Cache-Control": "no-cache, must-revalidate"}


@app.get("/", include_in_schema=False)
def root_page():
    return FileResponse(str(FRONTEND_DIR / "index.html"), headers=_NO_CACHE_HTML)


@app.get("/login", include_in_schema=False)
def login_page():
    return FileResponse(str(FRONTEND_DIR / "login.html"), headers=_NO_CACHE_HTML)


@app.get("/dashboard", include_in_schema=False)
def dashboard_page():
    return FileResponse(str(FRONTEND_DIR / "index.html"), headers=_NO_CACHE_HTML)


@app.get("/competitor-analysis", include_in_schema=False)
def competitor_analysis_page():
    return FileResponse(
        str(FRONTEND_DIR / "competitor-analysis" / "index.html"),
        headers=_NO_CACHE_HTML,
    )


def _push_daily_report() -> None:
    db = SessionLocal()
    try:
        import asyncio

        service = PushService(db)
        asyncio.run(service.send_markdown(service.build_daily_report_message()))
    finally:
        db.close()


def _run_recent_zentao_sync() -> None:
    db = SessionLocal()
    try:
        result = ZentaoBackgroundSyncService(db).run_recent_sync_for_all_software()
        logger.info(
            "background recent zentao sync finished | total=%s success=%s failed=%s",
            result.get("total"),
            result.get("success"),
            result.get("failed"),
        )
    finally:
        db.close()


def _run_nightly_full_zentao_sync() -> None:
    db = SessionLocal()
    try:
        result = ZentaoBackgroundSyncService(db).run_nightly_full_sync_for_all_software()
        logger.info(
            "background nightly zentao sync finished | total=%s success=%s failed=%s",
            result.get("total"),
            result.get("success"),
            result.get("failed"),
        )
    finally:
        db.close()


@app.on_event("startup")
def on_startup() -> None:
    init_db()
    if not scheduler.running:
        scheduler.add_job(_push_daily_report, CronTrigger(hour=18, minute=0), id="daily_report", replace_existing=True)
        if settings.zentao_background_sync_enabled:
            if settings.zentao_workbench_recent_sync_interval_minutes > 0:
                # IntervalTrigger keeps a clean N-minute cadence regardless of
                # whether N divides 60. CronTrigger("*/N") would only fire
                # at minute marks divisible by N, which silently drops to
                # once-per-hour for values like 7 or 13.
                scheduler.add_job(
                    _run_recent_zentao_sync,
                    IntervalTrigger(minutes=settings.zentao_workbench_recent_sync_interval_minutes),
                    id="zentao_recent_sync",
                    replace_existing=True,
                    max_instances=1,
                    coalesce=True,
                )
            scheduler.add_job(
                _run_nightly_full_zentao_sync,
                CronTrigger(
                    hour=settings.zentao_nightly_full_sync_hour,
                    minute=settings.zentao_nightly_full_sync_minute,
                ),
                id="zentao_nightly_full_sync",
                replace_existing=True,
                max_instances=1,
                coalesce=True,
            )
        scheduler.start()


@app.on_event("shutdown")
def on_shutdown() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)


app.include_router(api_router)
