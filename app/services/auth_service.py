from __future__ import annotations

import uuid

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.auth import CLIENT_MOBILE, authenticate_user, build_user_token, normalize_client_type
from app.services.audit_service import audit


class AuthService:
    @staticmethod
    def login_with_form(db: Session, username: str, password: str, client_type: str = "web") -> dict:
        user = authenticate_user(db, username, password)
        if not user:
            raise HTTPException(status_code=400, detail="Incorrect username or password")

        client_type = normalize_client_type(client_type)
        new_token = str(uuid.uuid4())
        # 仅刷新当前客户端类型的 session，不影响其它终端；
        # 因此同类型再次登录会踢掉上一处，不同类型（web/mobile）可同时在线。
        if client_type == CLIENT_MOBILE:
            user.session_token_mobile = new_token
        else:
            user.session_token = new_token
        db.commit()
        audit(
            db,
            action="auth.login",
            target_type="user",
            actor_id=user.id,
            target_id=str(user.id),
            detail=f"client={client_type}",
        )
        return {"access_token": build_user_token(user, client_type), "token_type": "bearer"}
