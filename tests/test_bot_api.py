from app.models import Requirement, RequirementStatus, Version, VersionType
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
