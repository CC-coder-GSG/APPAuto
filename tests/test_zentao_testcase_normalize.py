from __future__ import annotations

from app.services.zentao_testcase_service import ZentaoTestCaseService


def test_numeric_product_and_module_ids_are_not_saved_as_names(db_session):
    normalized = ZentaoTestCaseService(db_session)._normalize_testcase(
        {"caseID": 21564, "title": "定位", "product": 15, "module": "1107"},
        base_url="http://zentao.example",
    )

    assert normalized is not None
    assert normalized["zentao_product_id"] == 15
    assert normalized["zentao_product_name"] is None
    assert normalized["zentao_module_id"] == 1107
    assert normalized["zentao_module_name"] is None


def test_structured_product_and_module_names_are_preserved(db_session):
    normalized = ZentaoTestCaseService(db_session)._normalize_testcase(
        {
            "caseID": 21565,
            "title": "定位",
            "product": {"id": 15, "name": "Survey Master App"},
            "module": {"id": 1107, "name": "设备连接"},
        },
        base_url="http://zentao.example",
    )

    assert normalized is not None
    assert normalized["zentao_product_name"] == "Survey Master App"
    assert normalized["zentao_module_name"] == "设备连接"
