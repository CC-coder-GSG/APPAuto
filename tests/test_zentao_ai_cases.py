from __future__ import annotations

import json
import uuid
from datetime import timedelta

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.api.deps import get_current_user, get_db
from app.api.routes.zentao_ai import router
from app.models import User
from app.models.story_ai_result import StoryAIResult
from app.models.user_zentao_binding import UserZentaoBinding
from app.models.zentao_ai_case import AICaseDraft, AICasePushBatch, AICasePushItem, AICaseWorkspace
from app.schemas.zentao_ai_case import CaseContent
from app.services import zentao_ai_case_service as svc
from app.services.zentao_client_service import ZentaoAPIError, ZentaoClient
from app.utils.time_utils import local_now


def _uid():
    return str(uuid.uuid4())


@pytest.fixture
def setup_cases(db_session, monkeypatch):
    user = User(username="case_editor", password_hash="unused", tab_permissions='["zentao-ai"]')
    db_session.add(user)
    db_session.flush()
    db_session.add(UserZentaoBinding(user_id=user.id, base_url="http://zentao.test", zentao_account="tester", zentao_password_ciphertext="unused"))
    result = StoryAIResult(batch_id=_uid(), story_id=42, title="需求", ai_status="success", created_by=user.id,
                           raw_ai_result_json=json.dumps({"test_cases": [
                               {"title": "关闭检查", "case_type": "功能测试", "priority": "中", "precondition": "已登录\n开关关闭", "keywords": "控制；开关", "steps": [{"step": "检查开关", "expected": "关闭"}]},
                               {"title": "开启检查", "case_type": "功能测试", "priority": "高", "precondition": "已登录\n开关开启", "keywords": "开关,开启", "steps": [{"step": "检查开关", "expected": "开启"}]},
                               {"title": "第三条", "case_type": "功能测试", "priority": "低", "precondition": "", "keywords": "", "steps": [{"step": "操作", "expected": "通过"}]},
                           ]}, ensure_ascii=False))
    db_session.add(result)
    db_session.commit()
    ctx = {"story_id": 42, "story_title": "需求", "product_id": 7, "product_name": "产品", "module_id": 2,
           "modules": [{"id": 0, "name": "根模块"}, {"id": 2, "name": "模块"}], "module_options_complete": True, "types": svc.TYPES, "stages": svc.STAGES}
    monkeypatch.setattr(svc, "context", lambda *args: ctx)
    workspace = svc.open_workspace(db_session, user.id, result.id)
    row = db_session.get(AICaseWorkspace, workspace["id"])
    return user, result, row, ctx


def add_all(db, row):
    return svc.add_sources(db, row, row.revision, [0, 1, 2])


def test_selected_sources_merge_all_fields_and_restore_originals(db_session, setup_cases):
    user, result, row, _ = setup_cases
    original = result.raw_ai_result_json
    view = add_all(db_session, row)
    ids = [x["id"] for x in view["drafts"][:2]]
    merged = svc.merge_drafts(db_session, row, view["revision"], ids)
    assert len(merged["drafts"]) == 2
    draft = next(d for d in merged["drafts"] if len(d["sources"]) == 2)
    content = draft["content"]
    assert content["keywords"] == "控制；开关；开启"
    assert content["precondition"].count("已登录") == 1
    assert "开关关闭" in content["precondition"] and "开关开启" in content["precondition"]
    assert [s["expected"] for s in content["steps"]] == ["关闭", "开启"]
    assert content["priority"] == 2
    assert content["steps"][0]["step"] != content["steps"][1]["step"]
    restored = svc.draft_action(db_session, row, merged["revision"], draft["id"], "unmerge")
    assert len(restored["drafts"]) == 3
    assert {d["id"] for d in restored["drafts"]}.issuperset(ids)
    assert db_session.get(StoryAIResult, result.id).raw_ai_result_json == original
    assert svc.open_workspace(db_session, user.id, result.id)["id"] == row.id


def test_duplicate_sources_and_stale_revision_are_rejected(db_session, setup_cases):
    _, _, row, _ = setup_cases
    view = add_all(db_session, row)
    with pytest.raises(HTTPException) as caught:
        svc.add_sources(db_session, row, view["revision"], [0])
    assert caught.value.status_code == 409
    db_session.rollback()
    with pytest.raises(HTTPException) as caught:
        svc.edit_draft(db_session, row, 1, view["drafts"][0]["id"], CaseContent.model_validate(view["drafts"][0]["content"]))
    assert caught.value.status_code == 409


def test_merge_conflicts_must_be_resolved_before_submission(db_session, setup_cases):
    user, _, row, _ = setup_cases
    view = add_all(db_session, row)
    draft = view["drafts"][1]
    edited = {**draft["content"], "case_type": "interface", "module_id": 0, "stage": "smoke"}
    view = svc.edit_draft(db_session, row, view["revision"], draft["id"], CaseContent.model_validate(edited))
    view = svc.merge_drafts(db_session, row, view["revision"], [view["drafts"][0]["id"], draft["id"]])
    merged = next(d for d in view["drafts"] if len(d["sources"]) == 2)
    assert merged["content"]["module_id"] is None
    assert merged["content"]["case_type"] == ""
    assert merged["content"]["stage"] == ""
    with pytest.raises(HTTPException):
        svc.push_cases(db_session, user.id, row, view["revision"], _uid(), [merged["id"]])
    db_session.rollback()
    assert db_session.query(AICasePushBatch).count() == 0


def test_edit_saved_content_is_frozen_and_duplicate_request_reuses_batch(db_session, setup_cases):
    user, _, row, _ = setup_cases
    view = add_all(db_session, row)
    draft = view["drafts"][0]
    content = {**draft["content"], "title": "人工修改标题", "keywords": "新的词", "steps": [{"step": "修改步骤", "expected": "修改预期"}]}
    view = svc.edit_draft(db_session, row, view["revision"], draft["id"], CaseContent.model_validate(content))
    request_id = _uid()
    batch = svc.push_cases(db_session, user.id, row, view["revision"], request_id, [draft["id"]])
    again = svc.push_cases(db_session, user.id, row, view["revision"], request_id, [draft["id"]])
    assert again["id"] == batch["id"]
    snapshot = svc.loads(db_session.query(AICasePushItem).one().snapshot_json)
    assert snapshot["payload"]["title"] == "人工修改标题"
    assert snapshot["payload"]["steps"] == [{"desc": "修改步骤", "expect": "修改预期"}]
    assert snapshot["payload"]["keywords"] == "新的词"
    with pytest.raises(HTTPException):
        svc.edit_draft(db_session, row, row.revision, draft["id"], CaseContent.model_validate(content))
    db_session.rollback()
    with pytest.raises(HTTPException):
        svc.push_cases(db_session, user.id, row, row.revision, _uid(), [draft["id"]])
    db_session.rollback()


def run_with_fake(db, setup, monkeypatch, outcomes, fail_readback=False):
    user, _, row, _ = setup
    view = add_all(db, row)
    batch = svc.push_cases(db, user.id, row, view["revision"], _uid(), [x["id"] for x in view["drafts"]])
    saved = {}
    calls = []

    class Client:
        base_url = "http://zentao.test"

        def create_testcase(self, product, payload):
            calls.append(payload)
            outcome = outcomes[len(calls) - 1]
            if isinstance(outcome, Exception):
                raise outcome
            saved[outcome] = {"id": outcome, "product": product, **payload}
            return {"id": outcome}

        def get_testcase(self, case_id):
            if fail_readback:
                raise ZentaoAPIError(0, "read timeout")
            return saved[case_id]

    client = Client()
    monkeypatch.setattr(svc.zentao_ai_service, "_get_client", lambda *args: client)
    monkeypatch.setattr(svc.zentao_ai_service, "_call_with_401_retry", lambda _u, _d, fn: fn(client))

    # A fresh session mirrors the actual background worker; keep the same test engine.
    from sqlalchemy.orm import sessionmaker
    factory = sessionmaker(bind=db.get_bind(), autoflush=False)
    svc.run_batch(batch["id"], factory)
    db.expire_all()
    return batch, calls, factory, saved


def test_partial_failure_unknown_and_worker_rerun_do_not_duplicate(db_session, setup_cases, monkeypatch):
    batch, calls, factory, _ = run_with_fake(db_session, setup_cases, monkeypatch, [100, ZentaoAPIError(422, "字段不合法"), ZentaoAPIError(0, "timeout")])
    user, _, row, _ = setup_cases
    view = svc.batch_view(db_session, user.id, batch["id"])
    assert (view["succeeded"], view["failed"], view["unknown"]) == (1, 1, 1)
    svc.run_batch(batch["id"], factory)
    assert len(calls) == 3
    failed = db_session.query(AICaseDraft).filter_by(workspace_id=row.id, submit_status="failed").one()
    retry = svc.push_cases(db_session, user.id, row, row.revision, _uid(), [failed.id])
    assert retry["total"] == 1
    unknown = db_session.query(AICaseDraft).filter_by(workspace_id=row.id, submit_status="unknown").one()
    with pytest.raises(HTTPException):
        svc.push_cases(db_session, user.id, row, row.revision, _uid(), [unknown.id])
    db_session.rollback()


def test_sync_failure_keeps_created_id_and_only_retries_read(db_session, setup_cases, monkeypatch):
    batch, calls, factory, saved = run_with_fake(db_session, setup_cases, monkeypatch, [201, 202, 203], fail_readback=True)
    user, _, row, _ = setup_cases
    view = svc.workspace_view(db_session, row)
    assert all(d["submit_status"] == "succeeded" and d["remote_case_id"] and d["warning"] for d in view["drafts"])
    monkeypatch.setattr(svc, "remote_call", lambda _d, _u, fn: type("ReadClient", (), {"get_testcase": lambda self, case_id: saved[case_id]})().get_testcase(view["drafts"][0]["remote_case_id"]))
    synced = svc.reconcile(db_session, user.id, row, view["drafts"][0]["id"])
    assert synced["drafts"][0]["warning"] is None
    svc.run_batch(batch["id"], factory)
    assert len(calls) == 3


def test_unknown_can_only_be_linked_to_matching_remote_case(db_session, setup_cases, monkeypatch):
    _, _, _, _ = run_with_fake(db_session, setup_cases, monkeypatch, [301, 302, ZentaoAPIError(0, "timeout")])
    user, _, row, _ = setup_cases
    unknown = db_session.query(AICaseDraft).filter_by(workspace_id=row.id, submit_status="unknown").one()
    item = db_session.get(AICasePushItem, unknown.push_item_id)
    matching = {"id": 303, "product": row.product_id, **svc.loads(item.snapshot_json)["payload"]}
    monkeypatch.setattr(svc, "remote_call", lambda *args: {**matching, "story": 999})
    with pytest.raises(HTTPException) as caught:
        svc.reconcile(db_session, user.id, row, unknown.id, 303)
    assert caught.value.status_code == 400
    monkeypatch.setattr(svc, "remote_call", lambda *args: matching)
    view = svc.reconcile(db_session, user.id, row, unknown.id, 303)
    draft = next(d for d in view["drafts"] if d["id"] == unknown.id)
    assert draft["submit_status"] == "succeeded" and draft["remote_case_id"] == 303


def test_ownership_binding_and_stale_submission(db_session, setup_cases):
    user, _, row, _ = setup_cases
    with pytest.raises(HTTPException) as caught:
        svc.get_workspace(db_session, user.id + 999, row.id)
    assert caught.value.status_code == 404
    view = add_all(db_session, row)
    batch = svc.push_cases(db_session, user.id, row, view["revision"], _uid(), [view["drafts"][0]["id"]])
    item = db_session.query(AICasePushItem).filter_by(batch_id=batch["id"]).one()
    svc.set_outcome(db_session, item, "submitting")
    item.updated_at = local_now() - timedelta(minutes=16)
    db_session.commit()
    svc.recover_stale_items(db_session)
    assert item.status == "unknown"
    binding = db_session.query(UserZentaoBinding).filter_by(user_id=user.id).one()
    binding.base_url = "http://other.test"
    db_session.commit()
    with pytest.raises(HTTPException) as caught:
        svc.get_workspace(db_session, user.id, row.id)
    assert caught.value.status_code == 409


def test_old_single_case_output_is_editable(db_session, setup_cases):
    user, result, row, _ = setup_cases
    result.raw_ai_result_json = "{}"
    result.case_type = "接口测试"
    result.priority = "高"
    result.steps_json = '[{"desc":"调用接口","expect":"成功"}]'
    db_session.commit()
    view = svc.open_workspace(db_session, user.id, result.id)
    assert len(view["sources"]) == 1
    assert view["sources"][0]["content"]["steps"] == [{"step": "调用接口", "expected": "成功"}]


def test_routes_validate_inputs_and_save_restore(db_session, setup_cases):
    user, result, _, _ = setup_cases
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_db] = lambda: db_session
    with TestClient(app) as client:
        opened = client.post(f"/zentao/ai/results/{result.id}/workspace")
        assert opened.status_code == 200
        w = opened.json()
        assert client.post(f"/zentao/ai/workspaces/{w['id']}/sources", json={"revision": w["revision"], "source_indexes": []}).status_code == 422
        added = client.post(f"/zentao/ai/workspaces/{w['id']}/sources", json={"revision": w["revision"], "source_indexes": [1]}).json()
        restored = client.get(f"/zentao/ai/workspaces/{w['id']}").json()
        assert len(restored["drafts"]) == 1
        assert restored["drafts"][0]["sources"] == [1]
        assert restored["revision"] == added["revision"]


@pytest.mark.parametrize("text,expected", [('{"status":"fail","message":"no access"}', 422), ('<html>id=123</html>', 502), ('', 502), ('{"status":"success"}', 502)])
def test_create_client_does_not_infer_success_from_bad_responses(monkeypatch, text, expected):
    class Response:
        status_code = 200

    Response.text = text
    monkeypatch.setattr("app.services.zentao_client_service.httpx.post", lambda *args, **kwargs: Response())
    with pytest.raises(ZentaoAPIError) as caught:
        ZentaoClient("http://test", "token").create_testcase(7, {})
    assert caught.value.status_code == expected


def test_create_client_endpoint_and_step_mapping(monkeypatch):
    captured = {}

    def post(url, **kwargs):
        captured.update(url=url, **kwargs)
        return type("Response", (), {"status_code": 201, "text": '{"data":{"id":901}}'})()

    monkeypatch.setattr("app.services.zentao_client_service.httpx.post", post)
    payload = {"title": "用例", "steps": [{"desc": "操作", "expect": "预期"}], "keywords": "词"}
    assert ZentaoClient("http://test", "token").create_testcase(7, payload)["id"] == 901
    assert captured["url"] == "http://test/api.php/v1/products/7/testcases"
    assert captured["json"] == payload


def test_two_sessions_cannot_queue_the_same_draft(db_session, setup_cases):
    from sqlalchemy.orm import sessionmaker
    user, _, row, _ = setup_cases
    view = add_all(db_session, row)
    factory = sessionmaker(bind=db_session.get_bind(), autoflush=False)
    with factory() as other:
        stale = other.get(AICaseWorkspace, row.id)
        revision = stale.revision
        svc.push_cases(db_session, user.id, row, view["revision"], _uid(), [view["drafts"][0]["id"]])
        with pytest.raises(HTTPException) as caught:
            svc.push_cases(other, user.id, stale, revision, _uid(), [view["drafts"][0]["id"]])
        assert caught.value.status_code == 409
        other.rollback()
    assert db_session.query(AICasePushItem).count() == 1


def test_worker_refreshes_only_current_item_after_401(db_session, setup_cases, monkeypatch):
    user, _, row, _ = setup_cases
    view = add_all(db_session, row)
    batch = svc.push_cases(db_session, user.id, row, view["revision"], _uid(), [x["id"] for x in view["drafts"]])
    from sqlalchemy.orm import sessionmaker
    calls = []
    cases = {}
    invalidations = []

    class Client:
        base_url = row.base_url
        def create_testcase(self, product, payload):
            calls.append(payload["title"])
            if len(calls) == 2:
                raise ZentaoAPIError(401, "expired")
            case_id = 500 + len(cases)
            cases[case_id] = {"id": case_id, "product": product, **payload}
            return {"id": case_id}
        def get_testcase(self, case_id):
            return cases[case_id]
    client = Client()
    monkeypatch.setattr(svc.zentao_ai_service, "_get_client", lambda *args: client)
    monkeypatch.setattr(svc.zentao_ai_service, "invalidate_token", lambda *args: invalidations.append(True))
    monkeypatch.setattr(svc.zentao_ai_service, "_call_with_401_retry", lambda _u, _d, fn: fn(client))
    svc.run_batch(batch["id"], sessionmaker(bind=db_session.get_bind(), autoflush=False))
    assert calls == ["关闭检查", "开启检查", "开启检查", "第三条"]
    assert len(invalidations) == 1
    db_session.expire_all()
    assert svc.batch_view(db_session, user.id, batch["id"])["succeeded"] == 3


def test_preflight_failure_sends_no_create_requests(db_session, setup_cases, monkeypatch):
    user, _, row, _ = setup_cases
    view = add_all(db_session, row)
    batch = svc.push_cases(db_session, user.id, row, view["revision"], _uid(), [x["id"] for x in view["drafts"]])
    from sqlalchemy.orm import sessionmaker
    def unavailable(*args):
        raise HTTPException(400, "禅道无需求访问权限")
    monkeypatch.setattr(svc, "context", unavailable)
    monkeypatch.setattr(svc.zentao_ai_service, "_get_client", lambda *args: pytest.fail("preflight failure must not acquire write client"))
    svc.run_batch(batch["id"], sessionmaker(bind=db_session.get_bind(), autoflush=False))
    db_session.expire_all()
    assert svc.batch_view(db_session, user.id, batch["id"])["failed"] == 3


def test_readback_missing_keywords_is_visible_and_cannot_be_resubmitted(db_session, setup_cases, monkeypatch):
    _, calls, _, saved = run_with_fake(db_session, setup_cases, monkeypatch, [601, 602, 603])
    user, _, row, _ = setup_cases
    view = svc.workspace_view(db_session, row)
    first = view["drafts"][0]
    saved[first["remote_case_id"]]["keywords"] = ""
    synced = svc.reconcile(db_session, user.id, row, first["id"])
    assert "keywords" in synced["drafts"][0]["warning"]
    assert synced["drafts"][0]["submit_status"] == "succeeded"
    assert len(calls) == 3


def test_merge_keeps_user_selected_order(db_session, setup_cases):
    _, _, row, _ = setup_cases
    view = add_all(db_session, row)
    view = svc.merge_drafts(db_session, row, view["revision"], [view["drafts"][1]["id"], view["drafts"][0]["id"]])
    merged = next(d for d in view["drafts"] if len(d["sources"]) == 2)
    assert merged["sources"] == [1, 0]
    assert [s["expected"] for s in merged["content"]["steps"]] == ["开启", "关闭"]


def test_user_verified_not_created_can_restore_only_unknown_item(db_session, setup_cases, monkeypatch):
    run_with_fake(db_session, setup_cases, monkeypatch, [701, 702, ZentaoAPIError(0, "timeout")])
    user, _, row, _ = setup_cases
    view = svc.workspace_view(db_session, row)
    unknown = next(d for d in view["drafts"] if d["submit_status"] == "unknown")
    restored = svc.draft_action(db_session, row, view["revision"], unknown["id"], "confirm-not-created")
    draft = next(d for d in restored["drafts"] if d["id"] == unknown["id"])
    assert draft["submit_status"] == "failed"
    assert "核实未创建" in draft["error"]
    batch = svc.push_cases(db_session, user.id, row, restored["revision"], _uid(), [unknown["id"]])
    assert batch["total"] == 1
    succeeded = next(d for d in restored["drafts"] if d["submit_status"] == "succeeded")
    with pytest.raises(HTTPException):
        svc.draft_action(db_session, row, row.revision, succeeded["id"], "confirm-not-created")
    db_session.rollback()
