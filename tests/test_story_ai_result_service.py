from __future__ import annotations

import json

from app.models import StoryAIResult
from app.services import story_ai_result_service


def _seed_pending(db_session, *, batch_id: str, story_id: int, title: str = "story") -> StoryAIResult:
    story_ai_result_service.create_pending_rows(
        db_session,
        batch_id=batch_id,
        user_id=1,
        execution_id=1645,
        execution_name="s40311",
        stories=[{"id": story_id, "title": title}],
    )
    return db_session.query(StoryAIResult).filter(StoryAIResult.batch_id == batch_id).one()


def test_save_ai_results_accepts_nested_results_and_alias_fields(db_session):
    row = _seed_pending(db_session, batch_id="batch_nested", story_id=6236, title="old title")

    response = {
        "data": {
            "results": [
                {
                    "storyId": 6236,
                    "title": "重庆在线坐标转换需求",
                    "briefing": "需求测试简报",
                    "module": "坐标转换",
                    "scene": "OA流程",
                    "stage": "developing",
                    "caseType": "功能测试",
                    "priority": "高",
                    "precondition": "已选择需求",
                    "steps": json.dumps([{"step": "提交生成", "expected": "返回用例"}], ensure_ascii=False),
                    "riskPoints": ["坐标精度异常"],
                    "questionsToConfirm": ["转换坐标系范围"],
                    "testcaseTemplate": "1. 打开页面",
                }
            ]
        }
    }

    summary = story_ai_result_service.save_ai_results(
        db_session,
        batch_id="batch_nested",
        n8n_response=response,
    )

    db_session.refresh(row)
    assert summary == {"success": 1, "failed": 0, "per_story": {6236: "success"}}
    assert row.ai_status == "success"
    assert row.title == "重庆在线坐标转换需求"
    assert row.module_name == "坐标转换"
    assert row.scene_name == "OA流程"
    assert row.stage_name == "developing"
    assert row.case_type == "功能测试"
    assert row.testcase_template == "1. 打开页面"
    assert json.loads(row.steps_json) == [{"step": "提交生成", "expected": "返回用例"}]
    assert json.loads(row.risk_points_json) == ["坐标精度异常"]
    assert json.loads(row.questions_to_confirm_json) == ["转换坐标系范围"]


def test_save_ai_results_accepts_top_level_list_payload(db_session):
    row = _seed_pending(db_session, batch_id="batch_list", story_id=7001)

    summary = story_ai_result_service.save_ai_results(
        db_session,
        batch_id="batch_list",
        n8n_response=[
            {
                "story_id": 7001,
                "briefing": "列表形式返回也应入库",
                "testcase_template": "case text",
            }
        ],
    )

    db_session.refresh(row)
    assert summary == {"success": 1, "failed": 0, "per_story": {7001: "success"}}
    assert row.ai_status == "success"
    assert row.briefing == "列表形式返回也应入库"


def test_save_ai_results_infers_story_id_for_single_item_batch(db_session):
    row = _seed_pending(db_session, batch_id="batch_single", story_id=8001)

    summary = story_ai_result_service.save_ai_results(
        db_session,
        batch_id="batch_single",
        n8n_response={"results": [{"briefing": "单条批次缺少 story_id 时自动匹配"}]},
    )

    db_session.refresh(row)
    assert summary == {"success": 1, "failed": 0, "per_story": {8001: "success"}}
    assert row.ai_status == "success"
    assert row.briefing == "单条批次缺少 story_id 时自动匹配"
