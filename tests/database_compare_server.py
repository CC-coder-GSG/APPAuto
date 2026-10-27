"""Isolated browser-test harness; never connects to the application's live database."""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

if "DATABASE_COMPARE_TEST_ROOT" not in os.environ:
    raise RuntimeError("This harness requires DATABASE_COMPARE_TEST_ROOT")
scratch = Path(os.environ["DATABASE_COMPARE_TEST_ROOT"]).resolve()
if not scratch.is_relative_to(ROOT / ".codex-tmp"):
    raise RuntimeError("Test root must be inside the workspace .codex-tmp directory")
os.environ["APP_DATABASE_URL"] = "sqlite:///" + (scratch / "platform.db").as_posix()
os.environ["APP_DATABASE_COMPARE_ROOT"] = str(scratch / "storage")
os.environ["APP_DATABASE_COMPARE_WORKER_ENABLED"] = "true"

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

from app.api.deps import get_current_user
from app.api.routes.database_compare import router
from app.core.exceptions import AppError
from app.database import Base, SessionLocal, engine
from app.models import User, UserRole
from app.services.database_compare.worker import start_managed_worker

Base.metadata.create_all(bind=engine)
with SessionLocal() as db:
    user = User(username="compare_e2e", password_hash="not-used", role=UserRole.USER,
                tab_permissions='["database-compare"]')
    db.add(user)
    db.commit()

app = FastAPI()
app.include_router(router)
app.mount("/static", StaticFiles(directory=ROOT / "frontend"), name="static")


def test_user():
    with SessionLocal() as db:
        return db.query(User).filter_by(username="compare_e2e").one()


app.dependency_overrides[get_current_user] = test_user


@app.exception_handler(AppError)
async def error(request, exc):
    return JSONResponse({"detail": exc.message}, status_code=exc.status_code)


@app.get("/")
def index():
    template = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
    tools_section = '<section id="tab-tools"' + template.split('<section id="tab-tools"', 1)[1].split('</section>', 1)[0] + '</section>'
    tools_section = tools_section.replace('class="hidden card"', 'class="card"', 1)
    return HTMLResponse("""<!doctype html><meta charset="utf-8">
      <link rel="stylesheet" href="/static/css/main.css">
      <link rel="stylesheet" href="/static/css/weave.css">
      <style>body{padding:24px;background:#f8fafc}.hidden{display:none!important}</style>
      """ + tools_section + """
      <script type="module">
      import '/static/js/tabs/tools.js';
      await window.OmniQAToolsTab.activate(); window.testReady=true;
      </script>""")


@app.post("/__test__/shutdown")
def stop_server():
    server.should_exit = True
    return {"stopping": True}


worker = None


@app.on_event("startup")
def startup():
    global worker
    worker = start_managed_worker()


@app.on_event("shutdown")
def shutdown():
    if worker is not None and worker.poll() is None:
        worker.terminate()
        worker.wait(timeout=5)


if __name__ == "__main__":
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=int(sys.argv[1]), log_level="warning"))
    server.run()
