from __future__ import annotations

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from app.api.deps import get_current_user, get_db
from app.api.routes import auth, competitor_analysis, users
from app.core.exceptions import AppError, PermissionDenied
from app.models import CompetitorAnalysisReport, User, UserRole
from app.services.competitor_analysis_service import CompetitorAnalysisService


def _user(db_session, username: str, display_name: str) -> User:
    user = User(
        username=username,
        display_name=display_name,
        password_hash=User.hash_password("pass123"),
        role=UserRole.USER,
        tab_permissions='["competitor-analysis"]',
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


def _client(db_session, current_user: User) -> TestClient:
    app = FastAPI()
    app.include_router(competitor_analysis.router)
    app.include_router(auth.router)
    app.include_router(users.router)

    @app.exception_handler(AppError)
    async def app_error(request, error):
        return JSONResponse(status_code=error.status_code, content={"detail": error.message})

    def _override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_current_user] = lambda: current_user
    return TestClient(app)


def test_first_load_imports_initial_report_once(db_session):
    user = _user(db_session, "competitor_reader", "竞品读者")
    service = CompetitorAnalysisService(db_session)

    first = service.get_report(user)
    second = service.get_report(user)

    assert first["version"] == 1
    assert first["data"]["meta"]["title"] == "Survey Master 竞品功能分析"
    assert first == second
    assert db_session.query(CompetitorAnalysisReport).count() == 1


def test_save_increments_version_and_records_editor(db_session):
    user = _user(db_session, "competitor_editor", "竞品编辑者")
    service = CompetitorAnalysisService(db_session)
    report = service.get_report(user)
    report["data"]["meta"]["title"] = "更新后的竞品分析"

    saved = service.save_report(report["data"], report["version"], user)

    assert saved["version"] == 2
    assert saved["updatedBy"] == "竞品编辑者"
    assert saved["data"]["meta"]["title"] == "更新后的竞品分析"


def test_stale_version_is_rejected_without_overwriting(db_session):
    first_user = _user(db_session, "competitor_a", "编辑者A")
    second_user = _user(db_session, "competitor_b", "编辑者B")
    service = CompetitorAnalysisService(db_session)
    initial = service.get_report(first_user)

    winning_data = dict(initial["data"])
    winning_data["marker"] = "A"
    service.save_report(winning_data, initial["version"], first_user)

    stale_data = dict(initial["data"])
    stale_data["marker"] = "B"
    with pytest.raises(HTTPException) as exc_info:
        service.save_report(stale_data, initial["version"], second_user)

    assert exc_info.value.status_code == 409
    current = service.get_report(second_user)
    assert current["version"] == 2
    assert current["data"]["marker"] == "A"


def test_report_api_uses_adapter_contract(db_session):
    user = _user(db_session, "competitor_api", "接口编辑者")
    client = _client(db_session, user)

    loaded = client.get("/competitor-analysis/report")
    assert loaded.status_code == 200
    body = loaded.json()

    body["data"]["apiMarker"] = True
    saved = client.put(
        "/competitor-analysis/report",
        json={"data": body["data"], "baseVersion": body["version"]},
    )
    assert saved.status_code == 200
    assert saved.json()["version"] == body["version"] + 1
    assert saved.json()["updatedBy"] == "接口编辑者"

    conflict = client.put(
        "/competitor-analysis/report",
        json={"data": body["data"], "baseVersion": body["version"]},
    )
    assert conflict.status_code == 409


@pytest.mark.parametrize("tabs", [None, '[]', '["mine"]'])
def test_ungranted_user_cannot_initialize_report(db_session, tabs):
    user = _user(db_session, "ungranted", "未授权用户")
    user.tab_permissions = tabs
    db_session.commit()
    service = CompetitorAnalysisService(db_session)
    with pytest.raises(PermissionDenied):
        service.get_report(user)
    with pytest.raises(PermissionDenied):
        service.save_report({"marker": "denied"}, 1, user)
    assert db_session.query(CompetitorAnalysisReport).count() == 0


def test_data_console_can_grant_and_revoke_competitor_access(db_session):
    admin = _user(db_session, "permission_admin", "管理员")
    admin.role = UserRole.ADMIN
    admin.tab_permissions = '[]'
    reader = _user(db_session, "permission_reader", "普通用户")
    reader.tab_permissions = '["mine"]'
    db_session.commit()
    admin_client = _client(db_session, admin)
    reader_client = _client(db_session, reader)
    url = f"/users/{reader.id}/tab-permissions"
    report = admin_client.get('/competitor-analysis/report').json()

    for tabs, expected_status in ((["mine"], 403), (["mine", "competitor-analysis"], 200), (["mine"], 403), ([], 403)):
        response = admin_client.put(url, json={"allowed_tabs": tabs})
        assert response.status_code == 200, response.text
        assert response.json()["allowed_tabs"] == tabs
        assert reader_client.get('/auth/me').json()["allowed_tabs"] == tabs
        loaded = reader_client.get('/competitor-analysis/report')
        assert loaded.status_code == expected_status
        response = reader_client.put('/competitor-analysis/report', json={
            "data": report["data"], "baseVersion": report["version"],
        })
        assert response.status_code == expected_status
        if expected_status == 200:
            report = response.json()
        assert admin_client.get('/competitor-analysis/report').json() == report

    response = reader_client.put(url, json={"allowed_tabs": ["competitor-analysis"]})
    assert response.status_code == 403
    assert reader_client.get('/auth/me').json()["allowed_tabs"] == []


def test_external_user_cannot_access_competitor_report(db_session):
    user = _user(db_session, "external_reader", "外部用户")
    user.account_type = "external"
    db_session.commit()
    client = _client(db_session, user)
    assert client.get('/competitor-analysis/report').status_code == 403
    assert client.put('/competitor-analysis/report', json={"data": {}, "baseVersion": 1}).status_code == 403
    assert db_session.query(CompetitorAnalysisReport).count() == 0
