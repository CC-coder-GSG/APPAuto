from __future__ import annotations

from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
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

app = FastAPI(title="APPAuto", version="0.3.0")
scheduler = BackgroundScheduler(timezone="Asia/Shanghai")

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


@app.get("/", include_in_schema=False)
def root_page():
    return FileResponse(str(FRONTEND_DIR / "index.html"))


@app.get("/login", include_in_schema=False)
def login_page():
    return FileResponse(str(FRONTEND_DIR / "login.html"))


@app.get("/dashboard", include_in_schema=False)
def dashboard_page():
    return FileResponse(str(FRONTEND_DIR / "index.html"))


def _push_daily_report() -> None:
    db = SessionLocal()
    try:
        import asyncio

        service = PushService(db)
        asyncio.run(service.send_markdown(service.build_daily_report_message()))
    finally:
        db.close()


@app.on_event("startup")
def on_startup() -> None:
    init_db()
    if not scheduler.running:
        scheduler.add_job(_push_daily_report, CronTrigger(hour=18, minute=0), id="daily_report", replace_existing=True)
        scheduler.start()


@app.on_event("shutdown")
def on_shutdown() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)


app.include_router(api_router)
