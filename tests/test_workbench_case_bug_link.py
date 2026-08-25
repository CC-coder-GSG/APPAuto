"""禅道 Bug 关联用例 → 需求工作台用例栏展示链路（2026-07-07 修复回归）。

历史问题：
1. Bug 同步从不写 zentao_linked_case_id（禅道「相关用例」字段被丢弃）；
2. 展示端按镜像 'u#123' 精确匹配，历史落库的裸数字 '123' 永远匹配不上。
"""
from __future__ import annotations

from app.models import BugSourceType, BugTracking, Requirement, TestCase as _TestCaseModel, User, UserRole, Version, VersionType
from app.models.zentao_testcase_mirror import ZentaoTestCaseMirror
from app.services.overall_test_service import OverallTestService
from app.services.workbench_link_service import WorkbenchLinkService, _case_key


def _seed(db):
    user = User(username="cbl_user", password_hash="x", role=UserRole.USER)
    major = Version(version_no="V-CBL", version_type=VersionType.MAJOR)
    db.add_all([user, major])
    db.flush()
    req = Requirement(zentao_req_id="r#5923", title="需求", major_version_id=major.id, owner_id=user.id, zentao_story_id=5923)
    mirror = ZentaoTestCaseMirror(
        zentao_case_id="u#19712", zentao_case_numeric_id=19712,
        zentao_story_id=5923, title="用例", deleted=False,
    )
    db.add_all([req, mirror])
    db.commit()
    db.refresh(req)
    return user, major, req


def test_case_key_normalizes_both_formats():
    assert _case_key("u#19712") == "19712"
    assert _case_key("U#19712") == "19712"
    assert _case_key("19712") == "19712"
    assert _case_key(None) == ""


def test_bare_numeric_linked_case_id_matches_mirror(db_session):
    # 复现服务器数据：zentao_linked_case_id 存裸数字 '19712'，镜像是 'u#19712'
    user, major, req = _seed(db_session)
    bug = BugTracking(
        major_version_id=major.id, requirement_id=None, source_type=BugSourceType.MANUAL,
        bug_id="b#29660", zentao_bug_id="29660", zentao_linked_case_id="19712",
        created_by_id=user.id, zentao_deleted=False,
    )
    db_session.add(bug)
    db_session.commit()

    svc = WorkbenchLinkService(db_session)
    view = svc.build_requirement_case_view([req], {})
    cases = view[req.id]
    assert len(cases) == 1
    assert cases[0]["zentao_case_id"] == "u#19712"
    linked = cases[0]["bugs"]
    assert [b["bug_id"] for b in linked] == ["b#29660"]
    assert linked[0]["auto_linked"] is True


def test_prefixed_linked_case_id_matches_mirror(db_session):
    user, major, req = _seed(db_session)
    bug = BugTracking(
        major_version_id=major.id, requirement_id=None, source_type=BugSourceType.MANUAL,
        bug_id="b#30001", zentao_bug_id="30001", zentao_linked_case_id="u#19712",
        created_by_id=user.id, zentao_deleted=False,
    )
    db_session.add(bug)
    db_session.commit()
    view = WorkbenchLinkService(db_session).build_requirement_case_view([req], {})
    assert [b["bug_id"] for b in view[req.id][0]["bugs"]] == ["b#30001"]


def test_numeric_case_belongs_uses_readable_product_name_and_labeled_module_id(db_session):
    _, _, req = _seed(db_session)
    mirror = db_session.query(ZentaoTestCaseMirror).filter_by(zentao_case_id="u#19712").one()
    mirror.zentao_product_id = 310
    mirror.zentao_product_name = "310"
    mirror.zentao_module_id = 1107
    mirror.zentao_module_name = "1107"
    db_session.add(
        _TestCaseModel(
            requirement_id=req.id,
            zentao_case_id="u#99999",
            zentao_product_id="310",
            zentao_product_name="WoSense App",
        )
    )
    db_session.commit()

    cases = WorkbenchLinkService(db_session).build_requirement_case_view([req], {})[req.id]
    auto_case = next(case for case in cases if case["zentao_case_id"] == "u#19712")
    assert auto_case["belongs"] == "WoSense App / 模块 #1107"


def test_case_linked_bug_not_duplicated_in_free_bugs(db_session):
    """用例栏挂出的 Bug 不应再出现在自由 Bug 里（2026-07-08 修复回归）。"""
    user, major, req = _seed(db_session)
    case_bug = BugTracking(
        major_version_id=major.id, requirement_id=None, source_type=BugSourceType.MANUAL,
        bug_id="b#29660", zentao_bug_id="29660", zentao_linked_case_id="u#19712",
        zentao_story_id=5923, created_by_id=user.id, zentao_deleted=False,
    )
    free_bug = BugTracking(
        major_version_id=major.id, requirement_id=None, source_type=BugSourceType.MANUAL,
        bug_id="b#29661", zentao_bug_id="29661",
        zentao_story_id=5923, created_by_id=user.id, zentao_deleted=False,
    )
    db_session.add_all([case_bug, free_bug])
    db_session.commit()

    svc = WorkbenchLinkService(db_session)
    case_view = svc.build_requirement_case_view([req], {})
    assert [b["bug_id"] for b in case_view[req.id][0]["bugs"]] == ["b#29660"]

    free_map, auto_map = svc.build_requirement_free_bug_view(
        [req], {}, include_retest=False, exclude_case_view=case_view
    )
    free_ids = [b["bug_id"] for b in free_map.get(req.id, [])]
    assert free_ids == ["b#29661"]  # 挂用例的 b#29660 不再重复出现
    assert [b["bug_id"] for b in auto_map.get(req.id, [])] == ["b#29661"]

    # 不传排除集时保持旧行为（两条都归集）
    free_map_raw, _ = svc.build_requirement_free_bug_view([req], {}, include_retest=False)
    assert {b["bug_id"] for b in free_map_raw.get(req.id, [])} == {"b#29660", "b#29661"}


def test_normalize_zentao_bug_extracts_case_ref(db_session):
    svc = OverallTestService(db_session)
    raw = {
        "id": 29660, "title": "关联用例的Bug", "status": "active",
        "case": 19712, "story": 5923, "openedBy": {"account": "alice"},
    }
    normalized = svc._normalize_zentao_bug_summary(raw, "http://z/zentao")
    assert normalized["case_ref_id"] == 19712

    # dict 形态与 0/非法值
    raw["case"] = {"id": "19712"}
    assert svc._normalize_zentao_bug_summary(raw, "http://z")["case_ref_id"] == 19712
    raw["case"] = 0
    assert svc._normalize_zentao_bug_summary(raw, "http://z")["case_ref_id"] is None
    raw["case"] = "abc"
    assert svc._normalize_zentao_bug_summary(raw, "http://z")["case_ref_id"] is None


def test_write_bug_fields_persists_linked_case_in_mirror_format(db_session):
    user, major, req = _seed(db_session)
    svc = OverallTestService(db_session)
    bug_row = BugTracking(
        major_version_id=major.id, source_type=BugSourceType.MANUAL,
        bug_id="b#29777", created_by_id=user.id,
    )
    db_session.add(bug_row)
    db_session.flush()
    normalized = svc._normalize_zentao_bug_summary(
        {"id": 29777, "title": "x", "status": "active", "case": 19712, "openedBy": "alice"},
        "http://z/zentao",
    )
    svc._write_bug_fields_from_normalized(
        bug_row, normalized, execution_id=None, execution_name=None,
        sync_message="test", touched_if_existing=False,
    )
    db_session.commit()
    assert bug_row.zentao_linked_case_id == "u#19712"

    # 后续同步 payload 不带 case 字段 → 不清掉已有关联
    normalized2 = svc._normalize_zentao_bug_summary(
        {"id": 29777, "title": "x", "status": "active", "openedBy": "alice"},
        "http://z/zentao",
    )
    svc._write_bug_fields_from_normalized(
        bug_row, normalized2, execution_id=None, execution_name=None,
        sync_message="test", touched_if_existing=True,
    )
    assert bug_row.zentao_linked_case_id == "u#19712"
