from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.deps import get_current_user, get_db
from app.api.routes import zentao_bug_actions
from app.models import User, UserRole
from app.models.user_zentao_binding import UserZentaoBinding


class _FakeZentaoClient:
    def get_execution_context(self, execution_id: int) -> dict:
        return {"product_ids": [15], "project_id": None}

    def list_project_executions(self, project_id: int) -> list[dict]:
        return []

    def get_create_bug_meta(self, product_id: int, execution_id: int = 0) -> dict:
        return {
            "__meta_scope__": "execution",
            "users": {"chenwenbo": "陈文博"},
            "builds": {"4531": "4.0.3.1.260420(40301032)"},
            "moduleOptionMenu": {"669": "/项目"},
            "stories": {},
            "typeList": {"codeerror": "代码错误"},
        }

    def get_execution_build_ids(self, execution_id: int) -> list[str]:
        return ["4531"]

    def probe_bug_create_access(self, product_id: int, execution_id: int = 0) -> dict:
        return {
            "ok": False,
            "reason": "access_denied",
            "path": f"bug-create-{product_id}-{execution_id}.html",
            "preview": "window.alert('您无权访问该产品')",
        }

    def create_bug(self, product_id: int, data: dict) -> dict:
        return {"message": "success"}


def _create_user(db_session) -> User:
    row = User(username="admin_bug", password_hash="x", role=UserRole.ADMIN, display_name="admin")
    db_session.add(row)
    db_session.commit()
    db_session.refresh(row)
    return row


def _bind_user(db_session, user_id: int) -> None:
    binding = UserZentaoBinding(
        user_id=user_id,
        base_url="http://zentao.example/zentao",
        zentao_account="chenwenbo",
        token_value="token",
    )
    db_session.add(binding)
    db_session.commit()


def _make_client(db_session, current_user: User) -> TestClient:
    app = FastAPI()
    app.include_router(zentao_bug_actions.router)

    def _override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_current_user] = lambda: current_user
    return TestClient(app)


def test_create_meta_returns_access_denied_message(db_session, monkeypatch):
    user = _create_user(db_session)
    _bind_user(db_session, user.id)
    client = _make_client(db_session, user)
    monkeypatch.setattr(zentao_bug_actions, "_get_client", lambda user_id, db: _FakeZentaoClient())

    try:
        resp = client.get("/zentao/bugs/create-meta?execution_id=1647")
        assert resp.status_code == 200
        data = resp.json()
        assert data["product_ids"] == [15]
        assert data["create_access"]["reason"] == "access_denied"
        assert "无权访问产品 15" in data["create_access"]["message"]
    finally:
        zentao_bug_actions.router.dependency_overrides_provider = None
        app = client.app
        app.dependency_overrides.clear()


def test_create_bug_returns_diagnostic_when_empty_success_has_no_id(db_session, monkeypatch):
    user = _create_user(db_session)
    _bind_user(db_session, user.id)
    client = _make_client(db_session, user)
    monkeypatch.setattr(zentao_bug_actions, "_get_client", lambda user_id, db: _FakeZentaoClient())

    payload = {
        "product_id": 15,
        "execution_id": 1647,
        "title": "probe bug",
        "severity": 3,
        "pri": 3,
        "steps": "<p>probe</p>",
        "assigned_to": "chenwenbo",
        "opened_build": ["4531"],
        "bug_type": "codeerror",
        "module_id": "669",
        "major_version_id": 8,
    }
    try:
        resp = client.post("/zentao/bugs", json=payload)
        assert resp.status_code == 502
        detail = resp.json()["detail"]
        assert "无权访问产品 15" in detail
        assert '{"message": "success"}' in detail
    finally:
        app = client.app
        app.dependency_overrides.clear()
