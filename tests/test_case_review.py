from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.models import Requirement, TestCase, TestCaseReview, User, Version
from app.models.enums import UserRole, VersionType
from app.services.case_review_service import CaseReviewService
from app.services.workbench_service import WorkbenchService


def _mk_user(db, name: str) -> User:
    u = User(username=name, password_hash="x", role=UserRole.USER, display_name=name)
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def _mk_req_with_case(db, owner: User, case_no: str = "u#19712"):
    major = Version(version_no="V9.0", version_type=VersionType.MAJOR)
    db.add(major)
    db.commit()
    req = Requirement(zentao_req_id="r#9100", title="审查目标需求", major_version_id=major.id, owner_id=owner.id)
    db.add(req)
    db.commit()
    case = TestCase(requirement_id=req.id, zentao_case_id=case_no, zentao_case_title="登录用例", zentao_creator_name="王五")
    db.add(case)
    db.commit()
    return major, req, case


def test_review_pass_fail_independent_and_fix_archives(db_session):
    owner = _mk_user(db_session, "owner1")
    alice = _mk_user(db_session, "alice")
    bob = _mk_user(db_session, "bob")
    major, req, case = _mk_req_with_case(db_session, owner)
    svc = CaseReviewService(db_session)

    # 两人独立审查：alice 通过、bob 不通过
    svc.submit_review(zentao_case_id="u#19712", requirement_id=req.id, status="passed", opinion=None, current_user=alice)
    svc.submit_review(zentao_case_id="19712", requirement_id=req.id, status="failed", opinion="步骤缺前置条件", current_user=bob)

    m = svc.reviews_by_case_keys({"19712"})["19712"]
    assert len(m["active"]) == 2
    statuses = {r["reviewer_name"]: r["status"] for r in m["active"]}
    assert statuses == {"alice": "passed", "bob": "failed"}

    # 同一人重复审查：顶替自己旧记录，不叠加
    svc.submit_review(zentao_case_id="u#19712", requirement_id=req.id, status="failed", opinion="补充意见", current_user=alice)
    m = svc.reviews_by_case_keys({"19712"})["19712"]
    assert len(m["active"]) == 2
    assert all(r["status"] == "failed" for r in m["active"])
    assert len(m["history"]) == 3  # 归档的 passed 也在历史里

    # 修改完成：全部 active 归档 + fixed 历史，回到未审查
    svc.submit_fix(zentao_case_id="u#19712", requirement_id=req.id, content="已补前置条件", current_user=owner)
    m = svc.reviews_by_case_keys({"19712"})["19712"]
    assert m["active"] == []
    assert m["history"][0]["status"] == "fixed"
    assert m["history"][0]["opinion"] == "已补前置条件"
    assert len(m["history"]) == 4


def test_review_validation(db_session):
    owner = _mk_user(db_session, "owner2")
    alice = _mk_user(db_session, "alice2")
    major, req, case = _mk_req_with_case(db_session, owner, case_no="u#20001")
    svc = CaseReviewService(db_session)

    # 不通过必须填意见
    with pytest.raises(HTTPException) as e:
        svc.submit_review(zentao_case_id="u#20001", requirement_id=req.id, status="failed", opinion="  ", current_user=alice)
    assert e.value.status_code == 400

    # 没有未通过审查时不能修改完成
    with pytest.raises(HTTPException) as e:
        svc.submit_fix(zentao_case_id="u#20001", requirement_id=req.id, content="改了", current_user=alice)
    assert e.value.status_code == 400


def test_review_workbench_returns_all_owners_and_reviews(db_session):
    owner = _mk_user(db_session, "owner3")
    other = _mk_user(db_session, "other3")
    major, req, case = _mk_req_with_case(db_session, owner, case_no="u#20100")
    # 另一个负责人的需求也应出现在审查工作台
    req2 = Requirement(zentao_req_id="r#9101", title="他人需求", major_version_id=major.id, owner_id=other.id)
    db_session.add(req2)
    db_session.commit()

    CaseReviewService(db_session).submit_review(
        zentao_case_id="u#20100", requirement_id=req.id, status="passed", opinion=None, current_user=other,
    )

    rows = WorkbenchService(db_session).get_review_workbench(major_version_id=major.id)
    assert {r["zentao_req_id"] for r in rows} == {"r#9100", "r#9101"}
    target = next(r for r in rows if r["zentao_req_id"] == "r#9100")
    assert target["owner_name"] == "owner3"
    case_view = target["test_cases"][0]
    assert case_view["case_key"] == "20100"
    assert case_view["title"] == "登录用例"
    assert case_view["creator"] == "王五"
    assert case_view["bugs"] == []  # 审查台不带 Bug
    assert case_view["reviews"][0]["reviewer_name"] == "other3"

    # 需求工作台（负责人视角）同样带审查标签（联动展示）
    mine = WorkbenchService(db_session).get_my_workbench(current_user=owner, major_version_id=major.id, mode="version")
    mine_case = next(r for r in mine if r["zentao_req_id"] == "r#9100")["test_cases"][0]
    assert mine_case["reviews"][0]["status"] == "passed"
