from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.deps import get_current_user, get_db
from app.api.routes import jenkins
from app.models import User, UserJenkinsBinding
from app.models.enums import UserRole
from app.services import jenkins_service


def _make_user(db_session) -> User:
    user = User(username="tester", display_name="Tester", password_hash="x", role=UserRole.USER)
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


def _make_client(db_session, current_user: User) -> TestClient:
    app = FastAPI()
    app.include_router(jenkins.router)

    def _override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_current_user] = lambda: current_user
    return TestClient(app)


def test_binding_absent_returns_null(db_session):
    user = _make_user(db_session)
    client = _make_client(db_session, user)
    resp = client.get("/jenkins/binding/me")
    assert resp.status_code == 200
    body = resp.json()
    assert body["binding"] is None
    assert "base_url" in body["defaults"]


def test_upsert_binding_encrypts_token_and_verifies(db_session, monkeypatch):
    user = _make_user(db_session)
    # Avoid hitting a real Jenkins during the post-save verification.
    monkeypatch.setattr(jenkins_service, "test_credentials", lambda *a, **k: (True, "ok"))
    client = _make_client(db_session, user)

    resp = client.put(
        "/jenkins/binding/me",
        json={
            "base_url": "http://jenkins.example:8080/",
            "jenkins_account": "chenwenbo",
            "jenkins_token": "secret-token",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["verified"] is True
    assert body["binding"]["jenkins_account"] == "chenwenbo"
    assert body["binding"]["base_url"] == "http://jenkins.example:8080"

    stored = db_session.query(UserJenkinsBinding).filter_by(user_id=user.id).first()
    assert stored.jenkins_token_ciphertext
    assert "secret-token" not in (stored.jenkins_token_ciphertext or "")


def test_list_jobs_requires_binding(db_session):
    user = _make_user(db_session)
    client = _make_client(db_session, user)
    resp = client.get("/jenkins/jobs")
    assert resp.status_code == 400


def test_list_jobs_uses_user_client(db_session, monkeypatch):
    user = _make_user(db_session)
    jenkins_service.upsert_binding(
        db_session, user.id, base_url="http://jenkins.example:8080", account="chenwenbo", token="tok"
    )

    class FakeClient:
        def list_view_jobs(self, view_name):
            return [{"name": "自动化-冒烟", "url": "http://j/job/x", "color": "blue", "buildable": True}]

    monkeypatch.setattr(jenkins_service, "get_client_for_user", lambda db, uid: FakeClient())
    client = _make_client(db_session, user)
    resp = client.get("/jenkins/jobs")
    assert resp.status_code == 200
    body = resp.json()
    assert body["jobs"][0]["name"] == "自动化-冒烟"


def test_trigger_job_returns_queue_url(db_session, monkeypatch):
    user = _make_user(db_session)
    jenkins_service.upsert_binding(
        db_session, user.id, base_url="http://jenkins.example:8080", account="chenwenbo", token="tok"
    )

    class FakeClient:
        def trigger_build(self, job_name, params=None):
            assert job_name == "自动化-冒烟"
            return "http://jenkins.example:8080/queue/item/42/"

    monkeypatch.setattr(jenkins_service, "get_client_for_user", lambda db, uid: FakeClient())
    client = _make_client(db_session, user)
    resp = client.post("/jenkins/jobs/自动化-冒烟/build", json={})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["queue_item_url"].endswith("/queue/item/42/")
