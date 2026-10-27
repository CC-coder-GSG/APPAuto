from __future__ import annotations

import io
import json
import sqlite3
from contextlib import contextmanager

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from app.api.deps import get_current_user,get_db
from app.api.routes import database_compare
from app.core.config import settings
from app.core.exceptions import AppError
from app.models import User,UserRole
from app.models.database_compare import DatabaseCompareInput, DatabaseCompareTask
from app.services.database_compare.service import owned_path
from app.services.database_compare.worker import recover_interrupted,run_next,worker_lock


@pytest.fixture()
def context(db_session,tmp_path,monkeypatch):
    monkeypatch.setattr(settings,"database_compare_root",str(tmp_path/"storage"))
    app = FastAPI()
    app.include_router(database_compare.router)
    @app.exception_handler(AppError)
    async def error(request,exc):
        return JSONResponse({"detail":exc.message},status_code=exc.status_code)
    app.dependency_overrides[get_db] = lambda:db_session
    user = User(username="db_compare",password_hash="unused",role=UserRole.USER,
                tab_permissions='["database-compare"]')
    db_session.add(user);db_session.commit();db_session.refresh(user)
    app.dependency_overrides[get_current_user] = lambda:user
    path = tmp_path/"sample.db"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE data(id INTEGER PRIMARY KEY,value TEXT)")
        db.execute("INSERT INTO data VALUES(1,'old')")
    @contextmanager
    def factory():
        yield db_session
    return TestClient(app),user,path,factory


def upload(client,path,name="sample.db"):
    response = client.post("/api/database-compare/input-sets",files=[("files",(name,path.read_bytes(),"application/octet-stream"))])
    assert response.status_code == 200,response.text
    return response.json()["id"]


def test_full_api_worker_and_exports(context,db_session):
    client,user,path,factory = context
    old = upload(client,path)
    with sqlite3.connect(path) as db:
        db.execute("UPDATE data SET value='new'")
    new = upload(client,path)
    payload = {"old_input_id":old,"new_input_id":new,"old_label":"v1","new_label":"v2"}
    preview = client.post("/api/database-compare/previews",json={k:v for k,v in payload.items() if k.endswith("_id")})
    assert preview.status_code == 200 and preview.json()["tables"][0]["key"] == ["id"]
    created = client.post("/api/database-compare/tasks",json=payload)
    assert created.status_code == 201,created.text
    identity = created.json()["id"]
    endpoint = f"/api/database-compare/tasks/{identity}"
    assert client.get(endpoint+"/differences").status_code == 409
    assert run_next(factory)
    db_session.expire_all()
    task = client.get(endpoint).json()
    assert task["status"] == "succeeded" and task["summary"]["rows_modified"] == 1
    assert client.get(endpoint+"/tables").json()["items"][0]["match_mode"] == "key"
    diffs = client.get(endpoint+"/differences?scope=data&page_size=1").json()
    assert diffs["total"] == 1 and diffs["items"][0]["details"]["new"]["value"] == "new"
    assert client.get(endpoint+"/differences?kind=row_added").json()["total"] == 0
    for format,suffix in (("json","json"),("excel","zip")):
        report = client.post(endpoint+"/exports",json={"format":format})
        assert report.status_code == 200,report.text
        response = client.get(report.json()["download_url"])
        assert response.status_code == 200 and report.json()["export_id"].endswith(suffix)
        if format == "json":
            assert response.json()["summary"]["old_label"] == "v1"
    assert client.get("/api/database-compare/tasks").json()["total"] == 1
    assert not run_next(factory)


def test_permissions_ownership_and_untrusted_paths(context,db_session):
    client,user,path,factory = context
    old = upload(client,path)
    created = client.post("/api/database-compare/tasks",json={"old_input_id":old,"new_input_id":old}).json()
    user.tab_permissions = '["mine"]';db_session.commit()
    assert client.get("/api/database-compare/tasks").status_code == 403
    assert client.post("/api/database-compare/input-sets",files=[("files",("x.db",path.read_bytes()))]).status_code == 403
    user.tab_permissions = '["database-compare"]';db_session.commit()
    other = User(id=999999,username="other",password_hash="unused",role=UserRole.USER,tab_permissions='["database-compare"]')
    client.app.dependency_overrides[get_current_user] = lambda:other
    assert client.get(f"/api/database-compare/tasks/{created['id']}").status_code == 404
    assert client.post("/api/database-compare/tasks",json={"old_input_id":old,"new_input_id":old}).status_code == 404
    assert client.get("/api/database-compare/tasks").json()["total"] == 0
    client.app.dependency_overrides[get_current_user] = lambda:user
    for name in ("../escape.db","NUL.db","bad:stream.db"):
        assert client.post("/api/database-compare/input-sets",files=[("files",(name,path.read_bytes()))]).status_code == 400


def test_reject_invalid_upload_and_incomplete_mapping(context):
    client,user,path,factory = context
    response = client.post("/api/database-compare/input-sets",files=[("files",("bad.db",b"not sqlite"))])
    assert response.status_code == 400
    old = upload(client,path)
    new = upload(client,path)
    payload = {"old_input_id":old,"new_input_id":new,"pairs":[{"name":"sample","old":"sample.db","new":None}]}
    assert client.post("/api/database-compare/tasks",json=payload).status_code == 400
    payload.pop("pairs")
    payload["rules"] = {"ignored_tables":["data"]}
    assert client.post("/api/database-compare/tasks",json=payload).status_code == 400


def test_cancellation_and_worker_recovery(context,db_session):
    client,user,path,factory = context
    old = upload(client,path)
    payload = {"old_input_id":old,"new_input_id":old}
    first = client.post("/api/database-compare/tasks",json=payload).json()
    endpoint = f"/api/database-compare/tasks/{first['id']}"
    assert client.post(endpoint+"/cancel").json()["status"] == "cancelled"
    assert not run_next(factory)
    second = client.post("/api/database-compare/tasks",json=payload).json()
    item = db_session.get(DatabaseCompareTask,second["id"])
    item.status = "running";db_session.commit()
    recover_interrupted(factory)
    db_session.expire_all()
    assert client.get(f"/api/database-compare/tasks/{second['id']}").json()["status"] == "interrupted"
    with worker_lock() as first_lock:
        assert first_lock
        with worker_lock() as second_lock:
            assert not second_lock


def test_modified_uploaded_file_is_rejected(context,db_session):
    client,user,path,factory = context
    old = upload(client,path)
    created = client.post("/api/database-compare/tasks",json={"old_input_id":old,"new_input_id":old}).json()
    with sqlite3.connect(owned_path("inputs",old)/"sample.db") as db:
        db.execute("INSERT INTO data VALUES(2,'changed')")
    assert run_next(factory)
    db_session.expire_all()
    task = client.get(f"/api/database-compare/tasks/{created['id']}").json()
    assert task["status"] == "failed" and "发生变化" in task["error"]


def test_explicit_key_is_validated_before_queueing(context):
    client,user,path,factory = context
    with sqlite3.connect(path) as db:
        db.executescript("CREATE TABLE duplicate(id INTEGER,value TEXT); INSERT INTO duplicate VALUES(1,'a'),(1,'b')")
    source = upload(client,path)
    payload = {"old_input_id":source,"new_input_id":source,"rules":{
        "tables":{"sample.db.duplicate":{"match_mode":"key","key":["id"]}}}}
    for endpoint in ("/previews","/tasks"):
        response = client.post("/api/database-compare"+endpoint,json=payload)
        assert response.status_code == 400 and "重复" in response.json()["detail"]


def test_malformed_pair_values_are_rejected(context):
    client,user,path,factory = context
    source = upload(client,path)
    payload = {"old_input_id":source,"new_input_id":source,"pairs":[{"name":"sample","old":["sample.db"],"new":"sample.db"}]}
    assert client.post("/api/database-compare/tasks",json=payload).status_code == 400


def test_delete_releases_reports_and_only_unreferenced_snapshots(context, db_session):
    client, user, path, factory = context
    old, new = upload(client, path), upload(client, path)
    payload = {"old_input_id": old, "new_input_id": new}
    first, second = [client.post("/api/database-compare/tasks", json=payload).json() for _ in range(2)]
    assert run_next(factory)
    assert run_next(factory)
    db_session.expire_all()
    endpoint = f"/api/database-compare/tasks/{first['id']}"
    report = client.post(endpoint + "/exports", json={"format": "json"}).json()
    directory = owned_path("tasks", first["id"])
    assert (directory / "result.sqlite").is_file()
    assert (directory / "exports" / report["export_id"]).is_file()
    response = client.delete(endpoint)
    assert response.status_code == 200, response.text
    assert response.json()["deleted_input_ids"] == []
    assert not directory.exists()
    assert client.get(endpoint).status_code == 404
    assert client.get(report["download_url"]).status_code == 404
    for identity in (old, new):
        assert owned_path("inputs", identity).is_dir()
        assert db_session.get(DatabaseCompareInput, identity) is not None
    # The retained task can still reuse the snapshots after deleting its sibling.
    third = client.post("/api/database-compare/tasks", json=payload).json()
    assert client.post(f"/api/database-compare/tasks/{third['id']}/cancel").status_code == 200
    response = client.post("/api/database-compare/tasks/cleanup", json={"task_ids": [second["id"], third["id"]]})
    assert response.status_code == 200, response.text
    assert response.json()["deleted_count"] == 2
    assert set(response.json()["deleted_input_ids"]) == {old, new}
    for identity in (old, new):
        assert not owned_path("inputs", identity).exists()
        assert db_session.get(DatabaseCompareInput, identity) is None


@pytest.mark.parametrize("status", ["queued", "running"])
def test_cleanup_rejects_active_selection_before_removing_any_files(context, db_session, status):
    client, user, path, factory = context
    source = upload(client, path)
    first, second = [client.post("/api/database-compare/tasks", json={"old_input_id": source, "new_input_id": source}).json() for _ in range(2)]
    client.post(f"/api/database-compare/tasks/{first['id']}/cancel")
    db_session.get(DatabaseCompareTask, second["id"]).status = status
    db_session.commit()
    assert client.delete(f"/api/database-compare/tasks/{second['id']}").status_code == 409
    response = client.post("/api/database-compare/tasks/cleanup", json={"task_ids": [first["id"], second["id"]]})
    assert response.status_code == 409
    assert owned_path("tasks", first["id"]).is_dir()
    assert client.get("/api/database-compare/tasks").json()["total"] == 2
    response = client.post("/api/database-compare/tasks/cleanup", json={})
    assert response.json()["deleted_task_ids"] == [first["id"]]
    assert owned_path("inputs", source).is_dir()


def test_cleanup_all_spans_pages_and_respects_ownership(context, db_session):
    client, user, path, factory = context
    source = upload(client, path)
    statuses = ["succeeded", "partial", "failed", "cancelled", "interrupted"]
    for i in range(12):
        task = client.post("/api/database-compare/tasks", json={"old_input_id": source, "new_input_id": source}).json()
        item = db_session.get(DatabaseCompareTask, task["id"])
        item.status = statuses[i % len(statuses)]
        if i == 11:
            item.owner_id = 999999
        db_session.commit()
    listing = client.get("/api/database-compare/tasks?page_size=10").json()
    assert listing["total"] == listing["finished_total"] == 11
    other_id = task["id"]
    assert client.delete(f"/api/database-compare/tasks/{other_id}").status_code == 404
    response = client.post("/api/database-compare/tasks/cleanup", json={"task_ids": [listing["items"][0]["id"], other_id]})
    assert response.status_code == 404
    assert client.get("/api/database-compare/tasks").json()["total"] == 11
    user.tab_permissions = '["mine"]'; db_session.commit()
    assert client.post("/api/database-compare/tasks/cleanup", json={}).status_code == 403
    assert client.delete(f"/api/database-compare/tasks/{other_id}").status_code == 403
    user.tab_permissions = '["database-compare"]'; db_session.commit()
    response = client.post("/api/database-compare/tasks/cleanup", json={})
    assert response.json()["deleted_count"] == 11
    assert client.get("/api/database-compare/tasks").json()["total"] == 0
    assert owned_path("inputs", source).is_dir()  # Referenced by another owner's task.
    user.role = UserRole.ADMIN; db_session.commit()
    assert client.post("/api/database-compare/tasks/cleanup", json={}).json()["deleted_count"] == 1
    assert not owned_path("inputs", source).exists()
    assert client.post("/api/database-compare/tasks/cleanup", json={}).json()["deleted_count"] == 0
    assert client.post("/api/database-compare/tasks/cleanup", json={"task_ids": []}).status_code == 422


def test_cleanup_file_failure_keeps_history_for_retry(context, db_session, monkeypatch):
    client, user, path, factory = context
    source = upload(client, path)
    task = client.post("/api/database-compare/tasks", json={"old_input_id": source, "new_input_id": source}).json()
    endpoint = f"/api/database-compare/tasks/{task['id']}"
    client.post(endpoint + "/cancel")
    with monkeypatch.context() as patch:
        def locked(_):
            raise PermissionError("file in use")
        patch.setattr(database_compare.service.shutil, "rmtree", locked)
        assert client.delete(endpoint).status_code == 409
    assert client.get(endpoint).status_code == 200
    assert db_session.get(DatabaseCompareInput, source) is not None
    # Missing artifact directories are tolerated so interrupted cleanup can be retried.
    database_compare.service.shutil.rmtree(owned_path("tasks", task["id"]))
    assert client.delete(endpoint).status_code == 200
    assert not owned_path("inputs", source).exists()
