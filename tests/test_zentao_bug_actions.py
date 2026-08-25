from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from datetime import datetime

from app.api.deps import get_current_user, get_db
from app.api.routes import zentao_bug_actions
from app.models import User, UserRole
from app.models.bug import BugTracking, BugSourceType
from app.models.enums import VersionType
from app.models.stage5 import BugStage5Record
from app.models.user_zentao_binding import UserZentaoBinding
from app.models.version import Version


class _FakeZentaoClient:
    def get_execution_context(self, execution_id: int) -> dict:
        return {"product_ids": [391], "project_id": 134}

    def list_project_executions(self, project_id: int) -> list[dict]:
        if project_id == 134:
            return [
                {"id": 1647, "name": "Survey Master 5.0/s4031"},
                {"id": 1770, "name": "Survey Master 5.0/UniSurvey"},
            ]
        return []

    def get_create_bug_meta(self, product_id: int, execution_id: int = 0) -> dict:
        return {
            "__meta_scope__": "execution",
            "products": {"15": "Survey Master", "391": "UniSurvey"},
            "projects": {"134": "Survey Master 5.0"},
            "executions": {
                "1647": "Survey Master 5.0/s4031",
                "1770": "Survey Master 5.0/UniSurvey",
            },
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


class _EmptyMetaClient(_FakeZentaoClient):
    def get_create_bug_meta(self, product_id: int, execution_id: int = 0) -> dict:
        return {}

    def probe_bug_create_access(self, product_id: int, execution_id: int = 0) -> dict:
        return {
            "ok": False,
            "reason": "login_required",
            "path": f"bug-create-{product_id}-{execution_id}.json",
            "preview": "loginExpired",
        }


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
        assert data["selected_product_id"] == 15
        assert data["selected_project_id"] == 134
        assert data["products"]["15"] == "Survey Master"
        assert data["projects"]["134"] == "Survey Master 5.0"
        assert data["create_access"]["reason"] == "access_denied"
        assert "无权访问产品 15" in data["create_access"]["message"]
    finally:
        zentao_bug_actions.router.dependency_overrides_provider = None
        app = client.app
        app.dependency_overrides.clear()


def test_create_meta_refreshes_stale_token_when_first_meta_is_empty(db_session, monkeypatch):
    user = _create_user(db_session)
    _bind_user(db_session, user.id)
    client = _make_client(db_session, user)
    clients = [_EmptyMetaClient(), _FakeZentaoClient()]
    invalidated = []

    def _fake_get_client(user_id, db):
        assert clients
        return clients.pop(0)

    monkeypatch.setattr(zentao_bug_actions, "_get_client", _fake_get_client)
    monkeypatch.setattr(zentao_bug_actions, "invalidate_token", lambda user_id, db: invalidated.append(user_id))

    try:
        resp = client.get("/zentao/bugs/create-meta?execution_id=1647")
        assert resp.status_code == 200
        data = resp.json()
        assert data["products"]["15"] == "Survey Master"
        assert data["projects"]["134"] == "Survey Master 5.0"
        assert data["selected_product_id"] == 15
        assert invalidated == [user.id]
    finally:
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


class _ActivateClient:
    """Fake client for the reactivate route: active_bug succeeds and the
    read-back returns an active bug with a fresh assignee."""

    def __init__(self, live_status="active"):
        self.live_status = live_status
        self.active_calls = []

    def active_bug(self, bug_id, assigned_to="", opened_build=None, comment=""):
        self.active_calls.append({"bug_id": bug_id, "assigned_to": assigned_to})
        return {"message": "success"}

    def get_bug_with_fallback(self, bug_id):
        return {
            "id": bug_id,
            "status": self.live_status,
            "assignedTo": {"account": "chenwenbo", "realname": "陈文博"},
        }


def _create_closed_zentao_bug(db_session, user_id: int) -> BugTracking:
    major = Version(version_no="9.9.9", version_type=VersionType.MAJOR)
    db_session.add(major)
    db_session.commit()
    row = BugTracking(
        major_version_id=major.id,
        source_type=BugSourceType.MANUAL,
        bug_id="b#777",
        zentao_bug_id="777",
        zentao_live_status="closed",
        closed=True,
        closed_by_id=user_id,
        zentao_closed_by_account="chenwenbo",
        zentao_closed_by_name="陈文博",
        zentao_close_date=datetime(2026, 7, 1, 10, 0),
        zentao_close_comment="done",
        zentao_assigned_to_account="closed",
        zentao_assigned_to_name="Closed",
    )
    db_session.add(row)
    db_session.commit()
    db_session.add(BugStage5Record(
        bug_tracking_id=row.id,
        user_id=user_id,
        test_done=True,
        resolution="fixed",
        source="zentao_sync",
    ))
    db_session.commit()
    db_session.refresh(row)
    return row


def test_active_bug_resets_all_local_closed_traces(db_session, monkeypatch):
    user = _create_user(db_session)
    _bind_user(db_session, user.id)
    row = _create_closed_zentao_bug(db_session, user.id)
    client = _make_client(db_session, user)
    fake = _ActivateClient()
    monkeypatch.setattr(zentao_bug_actions, "_get_client", lambda user_id, db: fake)

    try:
        resp = client.post("/zentao/bugs/777/active", json={
            "assigned_to": "chenwenbo",
            "opened_build": [],
            "comment": "回归失败重开",
        })
        assert resp.status_code == 200
        assert resp.json()["live_status"] == "active"
        assert fake.active_calls and fake.active_calls[0]["assigned_to"] == "chenwenbo"

        db_session.refresh(row)
        assert row.closed is False
        assert row.closed_by_id is None
        assert row.zentao_live_status == "active"
        assert row.zentao_close_date is None
        assert (row.zentao_close_comment or "") == ""
        assert (row.zentao_closed_by_name or "") == ""
        # 关闭时缓存的 "closed" 占位指派必须被真实指派人覆盖，
        # 否则前端 isS5BugEffectivelyClosed 仍会把该行渲染成已关闭。
        assert row.zentao_assigned_to_account == "chenwenbo"
        assert row.zentao_assigned_to_name == "陈文博"

        record = db_session.query(BugStage5Record).filter(
            BugStage5Record.bug_tracking_id == row.id,
        ).one()
        assert record.test_done is False
    finally:
        app = client.app
        app.dependency_overrides.clear()


def test_active_bug_rejects_when_zentao_still_closed(db_session, monkeypatch):
    user = _create_user(db_session)
    _bind_user(db_session, user.id)
    row = _create_closed_zentao_bug(db_session, user.id)
    client = _make_client(db_session, user)
    fake = _ActivateClient(live_status="closed")
    monkeypatch.setattr(zentao_bug_actions, "_get_client", lambda user_id, db: fake)

    try:
        resp = client.post("/zentao/bugs/777/active", json={
            "assigned_to": "chenwenbo",
            "opened_build": [],
            "comment": "",
        })
        assert resp.status_code == 502
        assert "禅道未成功激活" in resp.json()["detail"]

        db_session.refresh(row)
        assert row.closed is True
        assert row.zentao_live_status == "closed"
        record = db_session.query(BugStage5Record).filter(
            BugStage5Record.bug_tracking_id == row.id,
        ).one()
        assert record.test_done is True
    finally:
        app = client.app
        app.dependency_overrides.clear()
