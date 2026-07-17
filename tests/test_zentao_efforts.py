"""工时记录服务（分段提交 / 查看 / 编辑，2026-07-17）。全部用假网页会话，无网络。"""
from __future__ import annotations

from datetime import date

import pytest
from fastapi import HTTPException

from app.models import User, UserRole
import app.services.zentao_effort_service as zes
from app.services.zentao_web_session import ZentaoWebLogin


def _login(account="alice"):
    return ZentaoWebLogin(base_url="http://z", account=account, password="p")


def _user(db, username="alice_eff", account="alice"):
    u = User(username=username, password_hash="x", role=UserRole.USER, zentao_account=account)
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def test_merge_extra_hours_lumps_onto_target_day():
    rows = [(date(2026, 6, 29), 3.5), (date(2026, 7, 1), 1.5)]
    merged = zes.merge_extra_hours(rows, 2.0, date(2026, 7, 1))
    assert merged == [(date(2026, 6, 29), 3.5), (date(2026, 7, 1), 3.5)]
    # 目标日没有行时新增一行；extra<=0 原样返回
    assert zes.merge_extra_hours(rows, 1.0, date(2026, 6, 30)) == [
        (date(2026, 6, 29), 3.5), (date(2026, 6, 30), 1.0), (date(2026, 7, 1), 1.5),
    ]
    assert zes.merge_extra_hours(rows, 0.0, date(2026, 7, 1)) == rows


def test_submit_day_efforts_declining_left(monkeypatch):
    sent = {}

    def fake_record(login, task_id, rows):
        sent["rows"] = rows
        return rows

    monkeypatch.setattr(zes, "record_task_efforts_via_web", fake_record)
    total = zes.submit_day_efforts(
        _login(), 42,
        [(date(2026, 6, 29), 3.5), (date(2026, 6, 30), 7.83), (date(2026, 7, 1), 1.5)],
        left_before=10.0, note="备注",
    )
    assert total == 12.83
    assert [r["left"] for r in sent["rows"]] == [6.5, 0.0, 0.0]  # 递减、不为负
    assert [r["date"] for r in sent["rows"]] == ["2026-06-29", "2026-06-30", "2026-07-01"]
    assert all(r["work"] == "备注" for r in sent["rows"])


def test_submit_day_efforts_skips_zero_rows(monkeypatch):
    called = []
    monkeypatch.setattr(zes, "record_task_efforts_via_web", lambda *a: called.append(a))
    assert zes.submit_day_efforts(_login(), 42, [(date(2026, 6, 29), 0.0)], left_before=5.0) == 0.0
    assert called == []  # 没有可提交行时不动网页会话


def test_list_efforts_marks_can_edit(db_session, monkeypatch):
    alice = _user(db_session)
    efforts = [
        {"id": 1, "date": "2026-06-29", "consumed": 3.5, "left": 6.5, "account": "alice", "work": "x"},
        {"id": 2, "date": "2026-06-30", "consumed": 2.0, "left": 4.5, "account": "bob", "work": "y"},
    ]
    monkeypatch.setattr(zes, "get_user_zentao_web_login", lambda uid, db: _login())
    monkeypatch.setattr(zes, "get_system_zentao_web_login", lambda db: None)
    monkeypatch.setattr(zes, "list_task_efforts_via_web", lambda login, tid: [dict(e) for e in efforts])
    res = zes.list_efforts_for_user(db_session, 42, alice)
    assert res["ok"] is True
    assert res["can_edit_any"] is True
    assert [e["can_edit"] for e in res["efforts"]] == [True, False]


def test_list_efforts_falls_back_to_system_login_readonly(db_session, monkeypatch):
    alice = _user(db_session, username="alice_ro")
    monkeypatch.setattr(zes, "get_user_zentao_web_login", lambda uid, db: None)
    monkeypatch.setattr(zes, "get_system_zentao_web_login", lambda db: _login("admin"))
    monkeypatch.setattr(zes, "list_task_efforts_via_web", lambda login, tid: [
        {"id": 1, "date": "2026-06-29", "consumed": 3.5, "left": 6.5, "account": "alice", "work": ""},
    ])
    res = zes.list_efforts_for_user(db_session, 42, alice)
    assert res["ok"] is True
    # 本人凭据缺失 → 整体只读（编辑必须以本人身份提交）
    assert res["can_edit_any"] is False


def test_edit_effort_rejects_others_record(db_session, monkeypatch):
    alice = _user(db_session, username="alice_403")
    monkeypatch.setattr(zes, "get_user_zentao_web_login", lambda uid, db: _login())
    monkeypatch.setattr(zes, "list_task_efforts_via_web", lambda login, tid: [
        {"id": 9, "date": "2026-06-29", "consumed": 2.0, "left": 4.0, "account": "bob", "work": ""},
    ])
    with pytest.raises(HTTPException) as ei:
        zes.edit_effort_for_user(db_session, 42, 9, alice, new_date="2026-06-30", consumed=1.5)
    assert ei.value.status_code == 403


def test_edit_effort_requires_self_login(db_session, monkeypatch):
    alice = _user(db_session, username="alice_nologin")
    monkeypatch.setattr(zes, "get_user_zentao_web_login", lambda uid, db: None)
    with pytest.raises(HTTPException) as ei:
        zes.edit_effort_for_user(db_session, 42, 9, alice, new_date="2026-06-30", consumed=1.5)
    assert ei.value.status_code == 400


def test_edit_effort_happy_path(db_session, monkeypatch):
    alice = _user(db_session, username="alice_edit")
    monkeypatch.setattr(zes, "get_user_zentao_web_login", lambda uid, db: _login())
    monkeypatch.setattr(zes, "list_task_efforts_via_web", lambda login, tid: [
        {"id": 9, "date": "2026-06-29", "consumed": 2.0, "left": 4.0, "account": "alice", "work": "旧"},
    ])
    edited = {}

    def fake_edit(login, task_id, effort_id, *, date, consumed, left, work):
        edited.update(task_id=task_id, effort_id=effort_id, date=date, consumed=consumed, left=left, work=work)
        return [{"id": 9, "date": date, "consumed": consumed, "left": left, "account": "alice", "work": work}]

    monkeypatch.setattr(zes, "edit_task_effort_via_web", fake_edit)
    res = zes.edit_effort_for_user(db_session, 42, 9, alice, new_date="2026-06-30", consumed=1.5)
    assert res["ok"] is True
    assert edited == {"task_id": 42, "effort_id": 9, "date": "2026-06-30", "consumed": 1.5, "left": 4.0, "work": "旧"}
    assert res["efforts"][0]["can_edit"] is True
