from __future__ import annotations

import uuid

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.auth import authenticate_user, build_user_token
from app.services.audit_service import audit


class AuthService:
    @staticmethod
    def login_with_form(db: Session, username: str, password: str) -> dict:
        user = authenticate_user(db, username, password)
        if not user:
            raise HTTPException(status_code=400, detail="Incorrect username or password")

        user.session_token = str(uuid.uuid4())
        db.commit()
        audit(db, action="auth.login", target_type="user", actor_id=user.id, target_id=str(user.id))
        return {"access_token": build_user_token(user), "token_type": "bearer"}
