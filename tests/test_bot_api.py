import pytest

from app.models import BugTracking, Requirement, RequirementStatus, Version, VersionType
from app.services.bot_api_service import BotApiService


def _major(db_session, no: str, software_id: int = 1) -> Version:
    row = Version(version_no=no, version_type=VersionType.MAJOR, software_id=software_id)
    db_session.add(row)
    db_session.commit()
    db_session.refresh(row)
    return row


def _minor(db_session, parent: Version, no: str) -> Version:
    row = Version(version_no=no, version_type=VersionType.MINOR, parent_id=parent.id, software_id=parent.software_id)
    db_session.add(row)
    db_session.commit()
    db_session.refresh(row)
    return row


def test_resolve_plain_digits_to_major(db_session):
    """'40315' 应解析为 V4.0.3.15（前 3 位 a.b.c，其余尾段）。"""
    target = _major(db_session, "V4.0.3.15")
    _major(db_session, "V4.0.3.1")  # 干扰项
    res = BotApiService(db_session).resolve_version("40315 这个版本怎么样了")
    assert res["resolved"] is True
    assert res["matches"][0]["major_version_id"] == target.id


def test_resolve_v_dotted_form(db_session):
    target = _major(db_session, "V4.0.3.1")
    res = BotApiService(db_session).resolve_version("V4.0.3.1 进度")
    assert res["resolved"] is True
    assert res["matches"][0]["major_version_id"] == target.id


def test_resolve_build_code_back_to_major(db_session):
    """给出子版本构建号，应回溯到父大版本并带回精确子版本 id。"""
    major = _major(db_session, "V4.0.3.0")
    minor = _minor(db_session, major, "4.0.3.0.260310_XINGHUI(40300103)")
    res = BotApiService(db_session).resolve_version("构建 40300103")
    assert res["resolved"] is True
    top = res["matches"][0]
    assert top["major_version_id"] == major.id
    assert top["minor_version_id"] == minor.id


def test_resolve_unknown_returns_empty(db_session):
    _major(db_session, "V4.0.3.0")
    res = BotApiService(db_session).resolve_version("V9.9.9.9")
    assert res["resolved"] is False
    assert res["matches"] == []


def test_version_status_aggregates(db_session):
    major = _major(db_session, "V4.0.3.2")
    db_session.add(Requirement(zentao_req_id="r#1", title="需求", major_version_id=major.id, status=RequirementStatus.PENDING, case_completed=True))
    db_session.commit()
    out = BotApiService(db_session).version_status("4032")
    assert out["resolved"] is True
    assert out["major_version_id"] == major.id
    assert out["progress"]["requirement_total"] == 1
    assert out["progress"]["case_completed"] == 1
    assert "by_status" in out["bugs"]
    assert "by_status" in out["feedback"]


def _bug(db_session, major, *, zentao_bug_id, title, bug_id="b#1", assignee="张三", live="active"):
    row = BugTracking(
        major_version_id=major.id,
        bug_id=bug_id,
        zentao_bug_id=zentao_bug_id,
        zentao_bug_title=title,
        zentao_assigned_to_name=assignee,
        zentao_live_status=live,
    )
    db_session.add(row)
    db_session.commit()
    db_session.refresh(row)
    return row


def _req(db_session, major, *, zentao_req_id, title, status=RequirementStatus.PENDING):
    row = Requirement(zentao_req_id=zentao_req_id, title=title, major_version_id=major.id, status=status)
    db_session.add(row)
    db_session.commit()
    db_session.refresh(row)
    return row


def test_search_bugs_by_id_exact(db_session):
    major = _major(db_session, "V4.0.3.0")
    _bug(db_session, major, zentao_bug_id="29875", title="地图加载崩溃")
    _bug(db_session, major, zentao_bug_id="30001", title="导出失败", bug_id="b#2")
    out = BotApiService(db_session).search_bugs("b#29875")
    assert out["match_count"] == 1
    assert out["matches"][0]["zentao_bug_id"] == "29875"
    assert out["matches"][0]["match"] == "id_exact"


def test_search_bugs_by_title_fuzzy(db_session):
    major = _major(db_session, "V4.0.3.0")
    _bug(db_session, major, zentao_bug_id="29875", title="地图加载时偶发崩溃")
    _bug(db_session, major, zentao_bug_id="30001", title="导出 Excel 失败", bug_id="b#2")
    out = BotApiService(db_session).search_bugs("地图崩溃")
    assert out["match_count"] >= 1
    assert out["matches"][0]["zentao_bug_id"] == "29875"
    assert out["matches"][0]["match"] == "title_fuzzy"


def test_bug_detail_rich(db_session):
    major = _major(db_session, "V4.0.3.0")
    _bug(db_session, major, zentao_bug_id="29875", title="地图崩溃", assignee="李四")
    out = BotApiService(db_session).bug_detail("29875")
    assert out["zentao_bug_id"] == "29875"
    assert out["title"] == "地图崩溃"
    assert out["assigned_to"] == "李四"
    assert out["major_version_no"] == "V4.0.3.0"
    assert out["status"] == "active"


def test_bug_detail_not_found(db_session):
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as ei:
        BotApiService(db_session).bug_detail("99999")
    assert ei.value.status_code == 404


def test_search_requirements_fuzzy_and_detail(db_session):
    major = _major(db_session, "V4.0.3.0")
    _req(db_session, major, zentao_req_id="r#5604", title="支持野外离线地图缓存")
    _req(db_session, major, zentao_req_id="r#5605", title="导出报表加水印")
    svc = BotApiService(db_session)

    found = svc.search_requirements("离线地图")
    assert found["matches"][0]["zentao_req_id"] == "r#5604"

    by_id = svc.search_requirements("5604")
    assert by_id["match_count"] == 1
    assert by_id["matches"][0]["match"] == "id_exact"

    detail = svc.requirement_detail("r#5604")
    assert detail["resolved"] is True
    assert detail["title"] == "支持野外离线地图缓存"
    assert detail["major_version_no"] == "V4.0.3.0"


def test_requirement_detail_ambiguous_across_majors(db_session):
    a = _major(db_session, "V4.0.3.0")
    b = _major(db_session, "V4.0.3.1")
    _req(db_session, a, zentao_req_id="r#5604", title="离线地图 v0")
    _req(db_session, b, zentao_req_id="r#5604", title="离线地图 v1")
    out = BotApiService(db_session).requirement_detail("r#5604")
    assert out["resolved"] is False
    assert out["ambiguous"] is True
    assert len(out["candidates"]) == 2


def test_version_status_ambiguous(db_session):
    """一个不完整的构建号片段命中多个大版本的子版本时应判定为 ambiguous，回候选而非强行选一个。"""
    a = _major(db_session, "V4.0.3.0")
    _minor(db_session, a, "4.0.3.0.260310(40300103)")
    b = _major(db_session, "V4.0.3.1")
    _minor(db_session, b, "4.0.3.1.260311(40300999)")
    out = BotApiService(db_session).version_status("40300")  # 5 位片段，两边子版本都含
    assert out["resolved"] is False
    assert out["ambiguous"] is True
    assert len(out["candidates"]) >= 2
