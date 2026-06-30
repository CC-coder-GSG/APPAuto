from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.models import UserRole, Version
from app.services.permission_service import ALL_TAB_KEYS, ensure_admin
from app.services.user_service import UserService
from app.services.zentao_system_client import get_system_zentao_client

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


class ZentaoAccountPayload(BaseModel):
    zentao_account: Optional[str] = None


class ZentaoAccountMatchPayload(BaseModel):
    # 大版本 id，用于从其禅道执行的任务创建页拉可指派用户列表
    major_version_id: int
    overwrite: bool = False


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


@router.put("/users/{user_id}/zentao-account")
def set_user_zentao_account(user_id: int, payload: ZentaoAccountPayload, current_user=Depends(get_current_user), db: Session = Depends(get_db)):
    ensure_admin(current_user)
    return UserService.set_zentao_account(db, user_id, payload.zentao_account, actor_id=current_user.id)


@router.post("/users/zentao-account/auto-match")
def auto_match_zentao_accounts(payload: ZentaoAccountMatchPayload, current_user=Depends(get_current_user), db: Session = Depends(get_db)):
    """从指定大版本的禅道执行拉可指派用户，按姓名把禅道账号回填到本地用户。"""
    ensure_admin(current_user)
    major = db.query(Version).filter(Version.id == payload.major_version_id).first()
    if not major or not major.zentao_execution_id:
        raise HTTPException(status_code=400, detail="该大版本未绑定禅道执行")
    client = get_system_zentao_client(db)
    if not client:
        raise HTTPException(status_code=400, detail="找不到可用的禅道账号绑定")
    try:
        account_to_name = client.list_assignable_users(int(major.zentao_execution_id))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"拉取禅道用户列表失败：{exc}")
    return UserService.auto_match_zentao_accounts(
        db, account_to_name, overwrite=payload.overwrite, actor_id=current_user.id
    )
