from __future__ import annotations

from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.router import api_router
from app.db.session import SessionLocal
from app.db.init_db import init_db
from app.services.push_service import PushService

app = FastAPI(title="APPAuto", version="0.3.0")
scheduler = BackgroundScheduler(timezone="Asia/Shanghai")

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")


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
