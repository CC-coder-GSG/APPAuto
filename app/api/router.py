from fastapi import APIRouter

from app.api.routes import admin, auth, bugs, executions, export, feedback, push, reports, requirements, retest, stage5, users, versions

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(users.router)
api_router.include_router(versions.router)
api_router.include_router(requirements.router)
api_router.include_router(executions.router)
api_router.include_router(bugs.router)
api_router.include_router(admin.router)
api_router.include_router(reports.router)
api_router.include_router(retest.router)
api_router.include_router(stage5.router)
api_router.include_router(push.router)
api_router.include_router(export.router)
api_router.include_router(feedback.router)
