"""全量同步入口回归（2026-07-15 修复）。

历史问题：1ad2e4a 插入 _reconcile_deleted_bugs 时误删了
`def _fetch_product_bugs_with_retry(...)` 的函数签名，导致
sync_all_zentao_bugs_by_software 一进线程池就 AttributeError → 前端
「强制同步禅道并刷新」按钮 HTTP 500，每晚定时全量对账也一直失败。
本测试端到端走一遍全量同步管线（仅 mock 禅道 HTTP 层），确保入口方法
链完整、能正常落库返回统计。
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException
from sqlalchemy import text

from app.db.seed import UNCLASSIFIED_MAJOR_VERSION_NO
from app.models import SoftwareProduct, User, UserRole, Version, VersionType
from app.services.overall_test_service import OverallTestService
from app.services.zentao_client_service import ZentaoAPIError


class _FakeZentaoClient:
    def get_bug(self, bug_id):  # 对账探测用；本测试无候选，不应被调用
        raise AssertionError("get_bug should not be called when nothing to reconcile")


def test_sync_all_zentao_bugs_pipeline_end_to_end(db_session, monkeypatch):
    user = User(username="fullsync_user", password_hash="x", role=UserRole.ADMIN)
    software = SoftwareProduct(name="FullSync 产品", zentao_product_id=15)
    other_software = SoftwareProduct(name="Other product", zentao_product_id=99)
    db_session.add_all([user, software, other_software])
    db_session.flush()
    other_unclassified = Version(
        version_no=UNCLASSIFIED_MAJOR_VERSION_NO,
        version_type=VersionType.MAJOR,
        software_id=other_software.id,
    )
    db_session.add(other_unclassified)
    db_session.commit()

    svc = OverallTestService(db_session)

    # 关键回归点：这两个方法都必须存在（1ad2e4a 曾把前者的 def 行弄丢）
    assert callable(getattr(svc, "_fetch_product_bugs_with_retry", None))
    assert callable(getattr(svc, "_reconcile_deleted_bugs", None))

    monkeypatch.setattr(
        svc, "_get_zentao_client_ctx",
        lambda user_id: (_FakeZentaoClient(), "http://z/zentao"),
    )
    # 只 mock 最底层的 HTTP 拉取，让 _fetch_product_bugs_with_retry 真实执行
    monkeypatch.setattr(
        svc, "_fetch_bug_collection",
        lambda client, path, **kw: [
            {"id": 61001, "title": "全量同步Bug", "status": "active",
             "openedBy": {"account": "alice"}},
        ],
    )

    result = svc.sync_all_zentao_bugs_by_software(
        software_id=software.id, current_user=user, force=True,
    )

    assert result["cached"] is False
    assert result["zentao_products"] == [15]
    assert result["remote_total"] == 1
    assert result["created"] == 1
    # 无 openedBuild/execution/affectedVersion → 落入未归类大版本
    assert result["unclassified"] == 1
    current_unclassified = (
        db_session.query(Version)
        .filter(
            Version.software_id == software.id,
            Version.version_no == UNCLASSIFIED_MAJOR_VERSION_NO,
            Version.version_type == VersionType.MAJOR,
        )
        .one()
    )
    assert current_unclassified.id != other_unclassified.id

    # 再跑一次应命中 upsert 更新分支，且锁已正确释放（不会 409）
    result2 = svc.sync_all_zentao_bugs_by_software(
        software_id=software.id, current_user=user, force=True,
    )
    assert result2["created"] == 0
    assert result2["remote_total"] == 1


def test_sync_all_reports_product_fetch_failure_and_releases_lock(db_session, monkeypatch):
    user = User(username="failed_fullsync", password_hash="x", role=UserRole.ADMIN)
    software = SoftwareProduct(name="Failed FullSync", zentao_product_id=311)
    db_session.add_all([user, software])
    db_session.commit()
    svc = OverallTestService(db_session)
    monkeypatch.setattr(
        svc,
        "_get_zentao_client_ctx",
        lambda user_id: (_FakeZentaoClient(), "http://z/zentao"),
    )

    def _fail(*args, **kwargs):
        raise ZentaoAPIError(500, "fatal")

    monkeypatch.setattr(svc, "_fetch_bug_collection", _fail)
    with pytest.raises(HTTPException) as exc_info:
        svc.sync_all_zentao_bugs_by_software(
            software_id=software.id,
            current_user=user,
            force=True,
        )

    assert exc_info.value.status_code == 502
    assert db_session.execute(
        text("SELECT COUNT(*) FROM sync_locks WHERE key = :key"),
        {"key": f"bug_full:{software.id}"},
    ).scalar_one() == 0


def test_extract_build_names_from_list_api_string(db_session):
    """产品 Bug 列表接口的 openedBuild 是构建名称字符串（2026-07-15 修复）。"""
    svc = OverallTestService(db_session)
    # 单名称 + 架构后缀剥离
    assert svc._extract_build_names("4.0.4.0.260715(40400034)(64-bit)") == ["4.0.4.0.260715(40400034)"]
    # 多构建逗号分隔
    assert svc._extract_build_names(
        "4.0.4.0.260714(40400033)(64-bit),4.0.3.20.260709_JFJ(40320016)"
    ) == ["4.0.4.0.260714(40400033)", "4.0.3.20.260709_JFJ(40320016)"]
    # 纯数字（真实构建 ID）与 trunk 不算名称
    assert svc._extract_build_names("4484") == []
    assert svc._extract_build_names("trunk") == []
    assert svc._extract_build_names(None) == []


def test_normalize_keeps_build_name_and_affected_fallback(db_session):
    svc = OverallTestService(db_session)
    normalized = svc._normalize_zentao_bug_summary(
        {"id": 31776, "title": "x", "status": "active",
         "openedBuild": "4.0.4.0.260715(40400034)(64-bit)",
         "openedBy": {"account": "alice"}},
        "http://z/zentao",
    )
    assert normalized["opened_build_names"] == ["4.0.4.0.260715(40400034)"]
    assert normalized["opened_build_ids"] == []
    # v1/v2 为空时，affected_version 回退用构建名 → 前端「发现于」不再是未知
    assert normalized["affected_version"] == "4.0.4.0.260715(40400034)"


def test_resolve_minor_by_build_name(db_session):
    """openedBuild 名称与本地小版本 version_no 匹配 → 解析出大/小版本。"""
    user = User(username="bn_user", password_hash="x", role=UserRole.ADMIN)
    software = SoftwareProduct(name="BN 产品", zentao_product_id=77)
    db_session.add_all([user, software])
    db_session.flush()
    major = Version(version_no="V4.0.4", version_type=VersionType.MAJOR, software_id=software.id)
    db_session.add(major)
    db_session.flush()
    minor = Version(
        version_no="4.0.4.0.260715(40400034)", version_type=VersionType.MINOR,
        parent_id=major.id, software_id=software.id,
    )
    unclassified_stub = Version(
        version_no="__unclassified__", version_type=VersionType.MAJOR, software_id=software.id,
    )
    db_session.add_all([minor, unclassified_stub])
    db_session.commit()

    svc = OverallTestService(db_session)
    normalized = svc._normalize_zentao_bug_summary(
        {"id": 31777, "title": "x", "status": "active",
         "openedBuild": "4.0.4.0.260715(40400034)(64-bit)",
         "openedBy": {"account": "alice"}},
        "http://z/zentao",
    )
    major_id, minor_id = svc._resolve_major_minor_for_bug(normalized, unclassified_stub)
    assert major_id == major.id
    assert minor_id == minor.id
