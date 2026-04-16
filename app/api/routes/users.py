from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.models import UserRole
from app.services.permission_service import ALL_TAB_KEYS, ensure_admin
from app.services.user_service import UserService

router = APIRouter()


class UserCreatePayload(BaseModel):
    username: str = Field(min_length=3, max_length=50)
    password: str = Field(min_length=3, max_length=128)
    role: UserRole = UserRole.USER


class UserRoleUpdatePayload(BaseModel):
    role: UserRole


class TeamStatusPayload(BaseModel):
    is_team_member: bool


class DisplayNamePayload(BaseModel):
    display_name: str = Field(min_length=1, max_length=80)


class PasswordChangePayload(BaseModel):
    old_password: str
    new_password: str = Field(min_length=3, max_length=128)


class PasswordResetPayload(BaseModel):
    new_password: str = Field(min_length=3, max_length=128)


class TabPermissionsPayload(BaseModel):
    allowed_tabs: list[str] = Field(default_factory=list, description=f"支持的 tab: {', '.join(ALL_TAB_KEYS)}")


@router.get("/users")
def list_users(current_user=Depends(get_current_user), db: Session = Depends(get_db)):
    return UserService.list_users(db)


@router.post("/users", status_code=201)
def create_user(payload: UserCreatePayload, current_user=Depends(get_current_user), db: Session = Depends(get_db)):
    ensure_admin(current_user)
    return UserService.create_user(db, payload.username, payload.password, payload.role)


@router.put("/users/{user_id}/role")
def update_user_role(user_id: int, payload: UserRoleUpdatePayload, current_user=Depends(get_current_user), db: Session = Depends(get_db)):
    ensure_admin(current_user)
    return UserService.update_role(db, user_id, payload.role, actor_id=current_user.id)


@router.put("/users/{user_id}/team-status")
def update_user_team_status(user_id: int, payload: TeamStatusPayload, current_user=Depends(get_current_user), db: Session = Depends(get_db)):
    ensure_admin(current_user)
    return UserService.update_team_status(db, user_id, payload.is_team_member, actor_id=current_user.id)


@router.put("/users/{user_id}/display-name")
def update_user_display_name(user_id: int, payload: DisplayNamePayload, current_user=Depends(get_current_user), db: Session = Depends(get_db)):
    ensure_admin(current_user)
    return UserService.update_display_name(db, user_id, payload.display_name, actor_id=current_user.id)


@router.delete("/users/{user_id}")
def delete_user(user_id: int, current_user=Depends(get_current_user), db: Session = Depends(get_db)):
    ensure_admin(current_user)
    if user_id == current_user.id:
        raise HTTPException(status_code=400, detail="不能删除当前登录用户")
    return UserService.delete_user(db, user_id, actor_id=current_user.id)


@router.put("/auth/password")
def change_my_password(payload: PasswordChangePayload, current_user=Depends(get_current_user), db: Session = Depends(get_db)):
    return UserService.change_my_password(db, current_user, payload.old_password, payload.new_password)


@router.put("/users/{user_id}/password")
def reset_user_password(user_id: int, payload: PasswordResetPayload, current_user=Depends(get_current_user), db: Session = Depends(get_db)):
    ensure_admin(current_user)
    return UserService.reset_user_password(db, user_id, payload.new_password, actor_id=current_user.id)


@router.put("/users/{user_id}/tab-permissions")
def update_user_tab_permissions(user_id: int, payload: TabPermissionsPayload, current_user=Depends(get_current_user), db: Session = Depends(get_db)):
    ensure_admin(current_user)
    return UserService.update_tab_permissions(db, user_id, payload.allowed_tabs, actor_id=current_user.id)
