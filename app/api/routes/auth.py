from __future__ import annotations

from fastapi import APIRouter, Depends, Header
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.services.auth_service import AuthService
from app.services.permission_service import get_allowed_tabs

router = APIRouter()


@router.post("/auth/token")
def login(
    form_data: OAuth2PasswordRequestForm = Depends(),
    x_client_type: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    # 客户端通过 X-Client-Type 头声明终端类型（mobile / web）。
    # 不传或非 mobile 一律按 web 处理，保证现有桌面端行为不变。
    return AuthService.login_with_form(db, form_data.username, form_data.password, client_type=x_client_type)


@router.get("/auth/me")
def auth_me(current_user=Depends(get_current_user)):
    return {
        "id": current_user.id,
        "username": current_user.username,
        "display_name": current_user.shown_name,
        "role": current_user.role.value,
        "is_team_member": getattr(current_user, "is_team_member", True),
        "allowed_tabs": get_allowed_tabs(current_user),
    }
