from jose import jwt
from fastapi import HTTPException

from app.api.deps import get_current_user
from app.core.config import settings
from app.models import User, UserRole
from app.services.auth_service import AuthService
from app.services.user_service import UserService


def _create_user(db_session, username: str = "tester", password: str = "pass123", role=UserRole.USER) -> User:
    user = User(
        username=username,
        password_hash=User.hash_password(password),
        role=role,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


def test_login_success_sets_session_token(db_session):
    user = _create_user(db_session)
    result = AuthService.login_with_form(db_session, user.username, "pass123")
    assert result["token_type"] == "bearer"

    db_session.refresh(user)
    assert user.session_token is not None


def test_login_failure_raises_http_error(db_session):
    user = _create_user(db_session)
    try:
        AuthService.login_with_form(db_session, user.username, "wrong-pass")
    except HTTPException as exc:
        assert exc.status_code == 400
        assert exc.detail == "Incorrect username or password"
    else:
        raise AssertionError("Expected HTTPException")


def test_single_session_kicks_old_token(db_session):
    user = _create_user(db_session)
    first = AuthService.login_with_form(db_session, user.username, "pass123")
    first_token = first["access_token"]

    second = AuthService.login_with_form(db_session, user.username, "pass123")
    second_token = second["access_token"]

    old_payload = jwt.decode(first_token, settings.secret_key, algorithms=[settings.algorithm])
    new_payload = jwt.decode(second_token, settings.secret_key, algorithms=[settings.algorithm])
    assert old_payload["session"] != new_payload["session"]

    try:
        get_current_user(first_token, db_session)
    except HTTPException as exc:
        assert exc.status_code == 401
    else:
        raise AssertionError("Old token should be rejected")

    current = get_current_user(second_token, db_session)
    assert current.username == user.username


def test_change_my_password_clears_session(db_session):
    user = _create_user(db_session)
    user.session_token = "active-session"
    db_session.commit()

    result = UserService.change_my_password(db_session, user, "pass123", "newpass123")
    db_session.refresh(user)

    assert result["message"] == "密码修改成功，请重新登录"
    assert user.session_token is None
    assert user.verify_password("newpass123")


def test_admin_reset_password_clears_target_session(db_session):
    user = _create_user(db_session, username="target", password="oldpass123")
    user.session_token = "target-session"
    db_session.commit()

    result = UserService.reset_user_password(db_session, user.id, "resetpass123", actor_id=999)
    db_session.refresh(user)

    assert "已重置" in result["message"]
    assert user.session_token is None
    assert user.verify_password("resetpass123")
