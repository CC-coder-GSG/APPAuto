from __future__ import annotations

import csv
import io
import json
from datetime import timedelta

import pytest
from fastapi import Depends, FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from app.api.deps import get_current_user, get_db
from app.api.routes import auth, bugly, users
from app.core.exceptions import AppError
from app.models import User, UserRole
from app.models.bugly import BuglyGrant, BuglyIssue, BuglyReport, BuglyReportRevision, BuglyImportBatch
from app.services.auth_service import AuthService
from app.services.permission_service import get_allowed_tabs
from app.utils.time_utils import utc_now_naive


@pytest.fixture
def context(db_session):
    people = {}
    for name, role, kind in (("manager", UserRole.ADMIN, "internal"), ("reader", UserRole.USER, "internal"), ("partner", UserRole.USER, "external")):
        user = User(username=name, password_hash=User.hash_password("testpass123"), role=role,
                    account_type=kind, tab_permissions='["bugly"]')
        db_session.add(user)
        db_session.commit()
        token = AuthService.login_with_form(db_session, name, "testpass123")["access_token"]
        people[name] = (user, {"Authorization": f"Bearer {token}"})
    app = FastAPI()
    app.include_router(auth.router)
    app.include_router(users.router)
    app.include_router(bugly.router)

    @app.get("/api/sse/stream")
    def stream(user=Depends(get_current_user)):
        return {"internal": True}

    @app.exception_handler(AppError)
    async def app_error(request, error):
        return JSONResponse(status_code=error.status_code, content={"detail": error.message})

    def override_db():
        yield db_session
    app.dependency_overrides[get_db] = override_db
    client = TestClient(app)
    return client, people, db_session


def application(ctx, name="Android 正式版"):
    client, people, _ = ctx
    r = client.post("/api/bugly/applications", headers=people["manager"][1], json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def grant(ctx, app_id, name="partner", **kwargs):
    client, people, _ = ctx
    r = client.post("/api/bugly/grants", headers=people["manager"][1],
                    json={"user_id": people[name][0].id, "application_id": app_id, **kwargs})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def envelope(report_ids=("1001", "1002"), issue_id="12345", stack="Exception\n  at app.Main.run(Main.java:23)\n  at app.Start.main(Start.java:4)"):
    return {"schemaVersion": 1, "issue": {"issueId": issue_id, "title": "#12345 页面切换崩溃"},
            "totalSucceeded": len(report_ids), "totalDiscovered": len(report_ids) + 1, "totalFailed": 1,
            "failures": [{"reportId": "1009", "reason": "等待超时"}],
            "reports": [{"reportId": rid, "collectedAt": "2026-10-09T02:00:00Z", "pageUrl": "https://bugly.qq.com/test",
                         "listFields": {"版本": "1.2.3", "设备": "Pixel 9", "异常上报时间": "2026-10-08 12:00:00"},
                         "detailFields": {"用户ID": "secret-user-998", "系统版本": "Android 15"},
                         "stack": {"text": stack, "mode": "parsed", "frameCount": 2},
                         "rawDetailText": "private raw token=secret-123", "captureDiagnostics": {"domWaitTimedOut": False}}
                        for rid in report_ids]}


def preview(ctx, app_id, data=None, name="manager", filename="bugly.json", raw=None, issue_id=None):
    client, people, _ = ctx
    if raw is None:
        raw = json.dumps(data or envelope(), ensure_ascii=False).encode()
    form = {"application_id": app_id}
    if issue_id:
        form["issue_id"] = issue_id
    return client.post("/api/bugly/imports/preview", headers=people[name][1], data=form,
                       files={"file": (filename, raw, "application/octet-stream")})


def imported(ctx, app_id, data=None):
    client, people, db = ctx
    response = preview(ctx, app_id, data)
    assert response.status_code == 200, response.text
    batch_id = response.json()["id"]
    response = client.post(f"/api/bugly/imports/{batch_id}/confirm", headers=people["manager"][1])
    assert response.status_code == 200, response.text
    result = response.json()
    issue_id = next((i["issue_id"] for i in result["result"]["items"] if "issue_id" in i), None)
    return result, db.get(BuglyIssue, issue_id)


def published(ctx, issue_id):
    client, people, _ = ctx
    candidate = client.get(f"/api/bugly/issues/{issue_id}/publication-preview", headers=people["manager"][1])
    assert candidate.status_code == 200, candidate.text
    response = client.post(f"/api/bugly/issues/{issue_id}/publication", headers=people["manager"][1], json=candidate.json())
    assert response.status_code == 200, response.text
    return candidate.json()


def test_json_roundtrip_and_idempotent_confirmation(context):
    app_id = application(context)
    batch, issue = imported(context, app_id)
    client, people, db = context
    assert batch["result"]["created"] == 2
    assert len(batch["diagnostics"]["failures"]) == 1
    assert "payload" not in batch["items"][0]
    repeat = client.post(f"/api/bugly/imports/{batch['id']}/confirm", headers=people["manager"][1])
    assert repeat.json()["result"] == batch["result"]
    assert db.query(BuglyReport).count() == 2
    row = db.query(BuglyReport).first()
    assert row.occurred_at.isoformat() == "2026-10-08T04:00:00"
    assert "\n" in row.stack_text
    assert db.query(BuglyReportRevision).count() == 2


def test_duplicate_and_changed_capture_keeps_revisions(context):
    app_id = application(context)
    imported(context, app_id)
    repeated, issue = imported(context, app_id)
    assert repeated["result"]["skipped"] == 2
    changed, issue = imported(context, app_id, envelope(("1002", "1003"), stack="updated stack"))
    assert changed["result"]["updated"] == 1
    assert changed["result"]["created"] == 1
    db = context[2]
    assert db.query(BuglyReport).count() == 3
    assert db.query(BuglyReportRevision).count() == 4


def test_application_number_spaces_are_independent(context):
    first, second = application(context, "应用 A"), application(context, "应用 B")
    imported(context, first)
    imported(context, second)
    assert context[2].query(BuglyIssue).count() == 2
    assert context[2].query(BuglyReport).count() == 4


def test_csv_bom_quotes_multiline_and_missing_fields_preserved(context):
    app_id = application(context)
    _, issue = imported(context, app_id)
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(["issueId", "issueTitle", "reportId", "列表.版本", "stackMode", "frameCount", "stack"])
    writer.writerow(["12345", "Crash", "1001", "1.2.4", "parsed", 2, 'Exception "quoted"\n at file:12\n at file:13'])
    r = preview(context, app_id, filename="bugly.csv", raw=("\ufeff" + output.getvalue()).encode())
    assert r.status_code == 200, r.text
    context[0].post(f"/api/bugly/imports/{r.json()['id']}/confirm", headers=context[1]["manager"][1])
    row = context[2].query(BuglyReport).filter_by(source_report_id="1001").one()
    assert row.app_version == "1.2.4"
    assert '"quoted"\n' in row.stack_text
    assert row.payload["rawDetailText"] == "private raw token=secret-123"


def test_external_only_sees_approved_snapshot_and_no_internal_search(context):
    app_id = application(context)
    _, issue = imported(context, app_id, envelope(stack="secret-user-998 token=topsecret /home/build/private/file.cpp\n at app.Main:9"))
    grant(context, app_id)
    client, people, db = context
    external = people["partner"][1]
    assert client.get("/api/bugly/external/issues", headers=external).json()["total"] == 0
    candidate = published(context, issue.id)
    detail = client.get(f"/api/bugly/external/issues/{issue.id}", headers=external)
    assert detail.status_code == 200
    assert detail.headers["cache-control"] == "no-store"
    serialized = json.dumps(detail.json())
    for secret in ("secret-user-998", "topsecret", "/home/build", "rawDetailText", "captureDiagnostics", "detailFields", "pageUrl", "analysis"):
        assert secret not in serialized
    assert "已脱敏" in detail.json()["reports"][0]["stack_text"]
    row = db.get(BuglyIssue, issue.id)
    r = client.patch(f"/api/bugly/issues/{issue.id}", headers=people["manager"][1],
                     json={"expected_version": row.version, "title": "Unpublished Secret Title", "analysis": "internal secret"})
    assert r.status_code == 200, r.text
    changed = client.get(f"/api/bugly/external/issues/{issue.id}", headers=external).json()
    assert changed["title"] == candidate["snapshot"]["title"]
    assert client.get("/api/bugly/external/issues?q=Unpublished", headers=external).json()["total"] == 0


@pytest.mark.parametrize("path,method", [("/users", "get"), ("/api/sse/stream", "get"),
    ("/api/bugly/applications", "get"), ("/api/bugly/imports", "get"), ("/api/bugly/grants", "get"),
    ("/api/bugly/issues", "post")])
def test_external_account_cannot_use_internal_api(context, path, method):
    client, people, _ = context
    r = getattr(client, method)(path, headers=people["partner"][1])
    assert r.status_code == 403, r.text


def test_problem_level_grants_and_revocation(context):
    app_id = application(context)
    _, first = imported(context, app_id)
    _, second = imported(context, app_id, envelope(issue_id="54321"))
    published(context, first.id)
    published(context, second.id)
    grant_id = grant(context, app_id, issue_id=first.id)
    client, people, _ = context
    external = people["partner"][1]
    assert client.get("/api/bugly/external/issues", headers=external).json()["total"] == 1
    assert client.get(f"/api/bugly/external/issues/{second.id}", headers=external).status_code == 403
    client.delete(f"/api/bugly/grants/{grant_id}", headers=people["manager"][1])
    assert client.get(f"/api/bugly/external/issues/{first.id}", headers=external).status_code == 403
    assert client.get("/api/bugly/external/applications", headers=external).json() == []


def test_internal_viewer_cannot_edit_and_raw_fields_are_restricted(context):
    app_id = application(context)
    _, issue = imported(context, app_id)
    grant(context, app_id, "reader")
    client, people, db = context
    reader = people["reader"][1]
    assert client.get(f"/api/bugly/issues/{issue.id}", headers=reader).status_code == 200
    report = db.query(BuglyReport).first()
    data = client.get(f"/api/bugly/reports/{report.id}", headers=reader).json()
    assert "rawDetailText" not in data["payload"]
    assert "用户ID" not in data["payload"]["detailFields"]
    r = client.patch(f"/api/bugly/issues/{issue.id}", headers=reader, json={"expected_version":issue.version,"analysis":"overwrite"})
    assert r.status_code == 403
    assert preview(context, app_id, name="reader").status_code == 403


def test_grant_expiry_blocks_access(context):
    app_id = application(context)
    _, issue = imported(context, app_id)
    published(context, issue.id)
    grant_id = grant(context, app_id)
    row = context[2].get(BuglyGrant, grant_id)
    row.expires_at = utc_now_naive()-timedelta(seconds=1)
    context[2].commit()
    assert context[0].get("/api/bugly/external/issues", headers=context[1]["partner"][1]).json()["total"] == 0


def test_account_disable_and_expiry_invalidate_login(context):
    client, people, db = context
    r = client.patch(f"/api/bugly/accounts/{people['partner'][0].id}", headers=people["manager"][1], json={"is_active":False})
    assert r.status_code == 200
    assert client.get("/auth/me", headers=people["partner"][1]).status_code == 401
    assert client.post("/auth/token", data={"username":"partner","password":"testpass123"}).status_code == 401
    partner = db.get(User, people["partner"][0].id)
    partner.is_active=True;partner.expires_at=utc_now_naive()-timedelta(days=1);db.commit()
    assert client.post("/auth/token", data={"username":"partner","password":"testpass123"}).status_code == 401


def test_previews_detect_intervening_updates_and_preserve_atomicity(context):
    app_id = application(context)
    _, issue = imported(context, app_id)
    pending = preview(context, app_id, envelope(("1003", "1001"), stack="pending stack")).json()
    imported(context, app_id, envelope(("1001",), stack="newer stack"))
    r = context[0].post(f"/api/bugly/imports/{pending['id']}/confirm", headers=context[1]["manager"][1])
    assert r.status_code == 409, r.text
    assert context[2].query(BuglyReport).filter_by(source_report_id="1003").first() is None
    assert context[2].get(BuglyImportBatch,pending["id"]).status == "preview"


def test_invalid_rows_remain_visible_and_partial_import_is_explicit(context):
    app_id = application(context)
    data = envelope()
    data["reports"][0]["reportId"] = None
    batch, issue = imported(context, app_id, data)
    assert batch["result"]["failed"] == 1
    assert batch["result"]["created"] == 1


def test_unknown_issue_requires_assignment_and_fallback_is_kept(context):
    app_id = application(context)
    client, people, db = context
    manual = client.post("/api/bugly/issues", headers=people["manager"][1], json={"application_id":app_id,"title":"待归类问题"}).json()["id"]
    data = envelope(issue_id=None)
    data["reports"][0]["stack"]["mode"] = "fallback"
    assert preview(context,app_id,data).json()["items"][0].get("error")
    assigned = preview(context,app_id,data,issue_id=manual)
    assert assigned.status_code == 200, assigned.text
    assert assigned.json()["items"][0]["warnings"]
    assert client.post(f"/api/bugly/imports/{assigned.json()['id']}/confirm",headers=people["manager"][1]).status_code == 200
    assert db.query(BuglyReport).count() == 2


def test_withdraw_publish_version_conflict_and_snapshot_whitelist(context):
    app_id = application(context)
    _, issue = imported(context, app_id)
    grant(context,app_id)
    client, people, db = context
    candidate = published(context, issue.id)
    candidate["snapshot"]["rawDetailText"] = "leak"
    assert client.post(f"/api/bugly/issues/{issue.id}/publication",headers=people["manager"][1],json=candidate).status_code == 422
    candidate["snapshot"].pop("rawDetailText")
    assert client.post(f"/api/bugly/issues/{issue.id}/publication",headers=people["manager"][1],json=candidate).status_code == 409
    client.delete(f"/api/bugly/issues/{issue.id}/publication",headers=people["manager"][1])
    assert client.get(f"/api/bugly/external/issues/{issue.id}",headers=people["partner"][1]).status_code == 404


def test_external_cannot_receive_write_sensitive_or_publishing_grant(context):
    app_id=application(context)
    client,people,_=context
    for extra in ({"role":"editor"},{"can_sensitive":True},{"can_publish":True}):
        r=client.post('/api/bugly/grants',headers=people['manager'][1],json={"user_id":people['partner'][0].id,"application_id":app_id,**extra})
        assert r.status_code==422


def test_logout_revokes_sessions_and_null_session_is_not_valid(context):
    client,people,_=context
    external=people['partner'][1]
    assert client.post('/auth/logout',headers=external).status_code==200
    assert client.get('/auth/me',headers=external).status_code==401


def test_pagination_filters_and_duplicate_application_errors(context):
    app_id=application(context)
    imported(context,app_id)
    client,people,_=context
    assert client.get('/api/bugly/issues?app_version=1.2.3&device=Pixel',headers=people['manager'][1]).json()['total']==1
    assert client.get('/api/bugly/issues?q=nonexistent',headers=people['manager'][1]).json()['total']==0
    assert client.get('/api/bugly/issues?page=0',headers=people['manager'][1]).status_code==422
    assert client.post('/api/bugly/applications',headers=people['manager'][1],json={"name":"Android 正式版"}).status_code==409


def test_empty_tab_permissions_do_not_restore_default_access(context):
    user=context[1]['reader'][0]
    user.tab_permissions='[]';context[2].commit()
    assert get_allowed_tabs(user)==[]
    assert context[0].get('/api/bugly/issues',headers=context[1]['reader'][1]).status_code==403


def test_data_console_can_grant_and_revoke_bugly_page_access(context):
    app_id = application(context)
    _, issue = imported(context, app_id)
    grant(context, app_id, "reader")
    client, people, db = context
    reader, reader_headers = people["reader"]
    manager_headers = people["manager"][1]
    permission_url = f"/users/{reader.id}/tab-permissions"

    for tabs, expected_status in ((["mine"], 403), (["mine", "bugly"], 200), (["mine"], 403), ([], 403)):
        response = client.put(permission_url, headers=manager_headers, json={"allowed_tabs": tabs})
        assert response.status_code == 200, response.text
        assert response.json()["allowed_tabs"] == tabs
        assert client.get('/auth/me', headers=reader_headers).json()["allowed_tabs"] == tabs
        assert json.loads(db.get(User, reader.id).tab_permissions) == tabs
        assert client.get('/api/bugly/issues', headers=reader_headers).status_code == expected_status
        assert client.get(f'/api/bugly/issues/{issue.id}', headers=reader_headers).status_code == expected_status

    response = client.put(permission_url, headers=reader_headers, json={"allowed_tabs": ["bugly"]})
    assert response.status_code == 403
    assert get_allowed_tabs(reader) == []


def test_global_report_view_and_revision_permissions(context):
    app_id=application(context)
    imported(context,app_id)
    imported(context,app_id,envelope(("1001",),stack="new revision stack"))
    grant(context,app_id,"reader")
    client,people,_=context
    reports=client.get('/api/bugly/reports?device=Pixel',headers=people['reader'][1]).json()
    assert reports['total']==2
    report_id=reports['items'][0]['id']
    assert client.get(f'/api/bugly/reports/{report_id}/revisions',headers=people['reader'][1]).status_code==403
    revisions=client.get(f'/api/bugly/reports/{report_id}/revisions',headers=people['manager'][1])
    assert revisions.status_code==200
    assert client.get('/api/bugly/reports',headers=people['partner'][1]).status_code==403


def test_deleted_user_grants_are_removed(context):
    app_id=application(context)
    grant(context,app_id)
    client,people,db=context
    target=people['partner'][0].id
    response=client.delete(f'/users/{target}',headers=people['manager'][1])
    assert response.status_code==200,response.text
    assert db.query(BuglyGrant).filter_by(user_id=target).count()==0


def test_account_expiry_roundtrips_with_utc_timezone(context):
    client,people,_=context
    target=people['partner'][0].id
    r=client.patch(f'/api/bugly/accounts/{target}',headers=people['manager'][1],json={"is_active":True,"expires_at":"2030-01-01T12:00:00+09:00"})
    assert r.status_code==200
    user=next(u for u in client.get('/users',headers=people['manager'][1]).json() if u['id']==target)
    assert user['expires_at']=='2030-01-01T03:00:00Z'
    assert client.patch(f'/api/bugly/accounts/{target}',headers=people['manager'][1],json={"is_active":False,"expires_at":user['expires_at']}).status_code==200


def test_manual_issue_can_add_source_id_but_cannot_take_existing_id(context):
    app_id=application(context)
    _,existing=imported(context,app_id)
    client,people,db=context
    manager=people['manager'][1]
    manual=client.post('/api/bugly/issues',headers=manager,json={"application_id":app_id,"title":"手工问题"}).json()['id']
    row=db.get(BuglyIssue,manual)
    assert client.patch(f'/api/bugly/issues/{manual}',headers=manager,json={"expected_version":row.version,"source_issue_id":"12345"}).status_code==409
    r=client.patch(f'/api/bugly/issues/{manual}',headers=manager,json={"expected_version":row.version,"source_issue_id":"#00123"})
    assert r.status_code==200,r.text
    assert r.json()['source_issue_id']=='00123'
    assert client.patch(f'/api/bugly/issues/{manual}',headers=manager,json={"expected_version":r.json()['version'],"source_issue_id":"99999"}).status_code==422


def test_sensitive_values_inside_stack_are_masked_for_internal_reader(context):
    app_id=application(context)
    imported(context,app_id,envelope(stack="Exception for secret-user-998\n at app.Main:11"))
    grant(context,app_id,"reader")
    client,people,db=context
    report=db.query(BuglyReport).first()
    r=client.get(f'/api/bugly/reports/{report.id}',headers=people['reader'][1])
    assert r.status_code==200
    assert 'secret-user-998' not in r.json()['stack_text']
