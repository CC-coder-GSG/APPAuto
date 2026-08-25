from __future__ import annotations

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.api.deps import get_current_user, get_db
from app.api.routes import competitor_analysis
from app.models import CompetitorAnalysisReport, User, UserRole
from app.services.competitor_analysis_service import CompetitorAnalysisService


def _user(db_session, username: str, display_name: str) -> User:
    user = User(
        username=username,
        display_name=display_name,
        password_hash=User.hash_password("pass123"),
        role=UserRole.USER,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


def _client(db_session, current_user: User) -> TestClient:
    app = FastAPI()
    app.include_router(competitor_analysis.router)

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
