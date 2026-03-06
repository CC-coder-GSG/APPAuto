from fastapi import APIRouter

from app.api.routes import auth, bugs, requirements, users, versions

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(users.router)
api_router.include_router(versions.router)
api_router.include_router(requirements.router)
api_router.include_router(bugs.router)
