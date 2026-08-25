from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models import User
from app.services.permission_service import get_allowed_tabs, serialize_tab_permissions
from app.services.audit_service import audit


def _user_to_dict(user: User) -> dict:
    return {
        "id": user.id,
        "username": user.username,
        "display_name": user.shown_name,
        "role": user.role.value,
        "is_team_member": user.is_team_member,
        "allowed_tabs": get_allowed_tabs(user),
        "zentao_account": user.zentao_account,
        "created_at": user.created_at.isoformat(),
    }


class UserService:
    @staticmethod
    def list_users(db: Session) -> list[dict]:
        rows = db.query(User).order_by(User.id.asc()).all()
        return [_user_to_dict(u) for u in rows]

    @staticmethod
    def create_user(db: Session, username: str, password: str, role) -> dict:
        if db.query(User).filter(User.username == username).first():
            raise HTTPException(status_code=400, detail="Username already exists")
        user = User(
            username=username,
            display_name=username,
            password_hash=User.hash_password(password),
            role=role,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        audit(db, action="user.create", target_type="user", actor_id=user.id, target_id=str(user.id), detail=f"username={user.username}")
        return _user_to_dict(user)

    @staticmethod
    def update_display_name(db: Session, user_id: int, display_name: str | None, actor_id: int | None = None) -> dict:
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        clean_name = (display_name or "").strip()
        user.display_name = clean_name or user.username
        db.commit()
        audit(
            db,
            action="user.rename_display",
            target_type="user",
            actor_id=actor_id,
            target_id=str(user.id),
            detail=f"display_name={user.display_name}",
        )
        return {"message": "显示名称已更新", "display_name": user.display_name}

    @staticmethod
    def set_zentao_account(db: Session, user_id: int, zentao_account: str | None, actor_id: int | None = None) -> dict:
        """手动设置/清空某用户的禅道账号。"""
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        clean = (zentao_account or "").strip() or None
        user.zentao_account = clean
        db.commit()
        audit(
            db,
            action="user.set_zentao_account",
            target_type="user",
            actor_id=actor_id,
            target_id=str(user.id),
            detail=f"zentao_account={clean}",
        )
        return {"message": "禅道账号已更新", "zentao_account": clean}

    @staticmethod
    def auto_match_zentao_accounts(db: Session, account_to_name: dict[str, str], *, overwrite: bool = False, actor_id: int | None = None) -> dict:
        """按真实姓名/用户名把禅道账号映射回填到本地用户。

        account_to_name：{account: realname}（来自禅道任务创建页 JSON）。
        overwrite=False 时只填空缺；True 时强制覆盖。返回匹配/未匹配统计。
        """
        # realname -> account（重名时后者覆盖；无法消歧由人工纠偏）
        name_to_acc: dict[str, str] = {}
        for acc, name in account_to_name.items():
            n = (name or "").strip()
            if n:
                name_to_acc[n] = acc
        matched: list[dict] = []
        unmatched: list[dict] = []
        for user in db.query(User).all():
            if user.zentao_account and not overwrite:
                continue
            acc = name_to_acc.get((user.shown_name or "").strip())
            if not acc and (user.username or "").strip().lower() in {a.lower() for a in account_to_name}:
                # 用户名直接等于某禅道账号
                acc = next((a for a in account_to_name if a.lower() == user.username.strip().lower()), None)
            if acc:
                user.zentao_account = acc
                matched.append({"user_id": user.id, "name": user.shown_name, "zentao_account": acc})
            else:
                unmatched.append({"user_id": user.id, "name": user.shown_name})
        db.commit()
        audit(db, action="user.auto_match_zentao", target_type="user", actor_id=actor_id, target_id="*", detail=f"matched={len(matched)}")
        return {"matched": matched, "unmatched": unmatched}

    @staticmethod
    def update_role(db: Session, user_id: int, role, actor_id: int | None = None) -> dict:
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        old_role = user.role.value
        user.role = role
        db.commit()
        audit(db, action="user.update_role", target_type="user", actor_id=actor_id, target_id=str(user.id), detail=f"{old_role}->{user.role.value}")
        return {"message": "User role updated"}

    @staticmethod
    def update_team_status(db: Session, user_id: int, is_team_member: bool, actor_id: int | None = None) -> dict:
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        old_value = user.is_team_member
        user.is_team_member = is_team_member
        db.commit()
        audit(db, action="user.update_team_status", target_type="user", actor_id=actor_id, target_id=str(user.id), detail=f"{old_value}->{user.is_team_member}")
        return {"message": "Team member status updated"}

    @staticmethod
    def update_tab_permissions(db: Session, user_id: int, tab_keys: list[str], actor_id: int | None = None) -> dict:
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        user.tab_permissions = serialize_tab_permissions(tab_keys)
        db.commit()
        audit(
            db,
            action="user.update_tab_permissions",
            target_type="user",
            actor_id=actor_id,
            target_id=str(user.id),
            detail=user.tab_permissions or "[]",
        )
        return {"message": "User tab permissions updated", "allowed_tabs": get_allowed_tabs(user)}

    @staticmethod
    def delete_user(db: Session, user_id: int, actor_id: int | None = None) -> dict:
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        username = user.username
        db.delete(user)
        db.commit()
        audit(db, action="user.delete", target_type="user", actor_id=actor_id, target_id=str(user_id), detail=f"username={username}")
        return {"message": "User deleted"}

    @staticmethod
    def change_my_password(db: Session, current_user: User, old_password: str, new_password: str) -> dict:
        if not current_user.verify_password(old_password):
            raise HTTPException(status_code=400, detail="原密码输入错误")
        if old_password == new_password:
            raise HTTPException(status_code=400, detail="新密码不能与原密码相同")

        current_user.password_hash = User.hash_password(new_password)
        current_user.session_token = None
        db.commit()
        audit(db, action="auth.change_password", target_type="user", actor_id=current_user.id, target_id=str(current_user.id))
        return {"message": "密码修改成功，请重新登录"}

    @staticmethod
    def reset_user_password(db: Session, user_id: int, new_password: str, actor_id: int | None = None) -> dict:
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        user.password_hash = User.hash_password(new_password)
        user.session_token = None
        db.commit()
        audit(db, action="user.reset_password", target_type="user", actor_id=actor_id, target_id=str(user.id), detail=f"username={user.username}")
        return {"message": f"用户 {user.username} 的密码已重置，且已被强制下线"}
