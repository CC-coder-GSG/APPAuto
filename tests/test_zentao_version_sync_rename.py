"""Regression tests for the V4.0.3.15 占位-错绑 scenario.

Background
----------
A Zentao build can start life as a placeholder (name contains ``xxxx``) and later
be renamed to a real version name by the push flow. The build's ``id`` is stable
across that rename. Before this fix, the local sync code matched the local row by
``zentao_build_id`` and updated the cached name, but **did not** rename the row's
``version_no`` — so the row was left in a hybrid state with placeholder
``version_no`` plus real ``zentao_build_name_cache``. The next sync would then
let the newly-created Zentao placeholder build steal the binding via the
"by name + parent" fallback.

These tests cover the fix:

1. ``_upsert_minor`` renames ``version_no`` when matched by ``zentao_build_id``
   and Zentao's current name differs.
2. After a full re-sync (placeholder + renamed real build), the local DB ends up
   with one row per Zentao build, each correctly bound.
3. ``compare_local_vs_zentao`` treats a properly-bound local placeholder row as
   *matched* to a Zentao placeholder build (not as ``only_local``).
"""
from __future__ import annotations

from app.models import Version, VersionType
from app.services.zentao_version_diff_service import compare_local_vs_zentao
from app.services.zentao_version_sync_service import (
    VersionSyncResult,
    ZentaoVersionSyncService,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_major(db, *, version_no: str, exec_id: int, software_id: int = 1) -> Version:
    row = Version(
        version_no=version_no,
        version_type=VersionType.MAJOR,
        software_id=software_id,
        zentao_execution_id=exec_id,
        zentao_execution_name_cache=version_no,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _make_minor(
    db,
    *,
    parent_id: int,
    version_no: str,
    zentao_build_id: int | None = None,
    cache: str | None = None,
    software_id: int = 1,
) -> Version:
    row = Version(
        version_no=version_no,
        version_type=VersionType.MINOR,
        parent_id=parent_id,
        software_id=software_id,
        zentao_build_id=zentao_build_id,
        zentao_build_name_cache=cache or version_no,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_upsert_minor_renames_version_no_when_matched_by_build_id(db_session):
    """命中 zentao_build_id 时，应跟随禅道当前 name 重命名本地 version_no。"""
    major = _make_major(db_session, version_no="V4.0.3.15", exec_id=1646)
    # 本地行：占位名，但绑定到禅道已被 push 改名成 alpha.4 的真实 build 4615
    minor = _make_minor(
        db_session,
        parent_id=major.id,
        version_no="4.0.3.15.26xxxx(4031xxxx)",
        zentao_build_id=4615,
        cache="4.0.3.15.260520_alpha.4(40315004)",
    )

    service = ZentaoVersionSyncService(db_session)
    result = VersionSyncResult()
    service._upsert_minor(
        version_no="4.0.3.15.260520_alpha.4(40315004)",
        parent_id=major.id,
        software_id=major.software_id or 1,
        zentao_build_id=4615,
        zentao_build_name_cache="4.0.3.15.260520_alpha.4(40315004)",
        result=result,
    )
    db_session.commit()
    db_session.refresh(minor)

    assert minor.version_no == "4.0.3.15.260520_alpha.4(40315004)"
    assert minor.zentao_build_id == 4615
    assert minor.zentao_build_name_cache == "4.0.3.15.260520_alpha.4(40315004)"


def test_upsert_minor_rename_skipped_when_conflict(db_session):
    """另一行已占用目标 version_no 时，不强行改名（避免唯一约束 500）。"""
    major = _make_major(db_session, version_no="V4.0.3.15", exec_id=1646)
    # 占位行（旧绑定）
    placeholder_row = _make_minor(
        db_session,
        parent_id=major.id,
        version_no="4.0.3.15.26xxxx(4031xxxx)",
        zentao_build_id=4615,
        cache="4.0.3.15.260520_alpha.4(40315004)",
    )
    # 自动归档已经先创了一行真实名（auto_archive 路径，无 build_id）
    real_row = _make_minor(
        db_session,
        parent_id=major.id,
        version_no="4.0.3.15.260520_alpha.4(40315004)",
        zentao_build_id=None,
    )

    service = ZentaoVersionSyncService(db_session)
    result = VersionSyncResult()
    service._upsert_minor(
        version_no="4.0.3.15.260520_alpha.4(40315004)",
        parent_id=major.id,
        software_id=1,
        zentao_build_id=4615,
        zentao_build_name_cache="4.0.3.15.260520_alpha.4(40315004)",
        result=result,
    )
    db_session.commit()
    db_session.refresh(placeholder_row)
    db_session.refresh(real_row)

    # 冲突时保留旧 version_no，不让 commit 撞唯一约束
    assert placeholder_row.version_no == "4.0.3.15.26xxxx(4031xxxx)"
    # 但 build_id / cache 仍然按命中刷新
    assert placeholder_row.zentao_build_id == 4615
    assert any("rename-blocked" in s for s in result.skipped)


def test_compare_local_vs_zentao_matches_local_placeholder_to_remote_placeholder(
    db_session, monkeypatch
):
    """本地占位行正确绑定时，对账页应该归到 matched 而非 only_local。"""
    major = _make_major(db_session, version_no="V4.0.3.15", exec_id=1646)
    # 健康状态：3 行本地小版本，含一个占位
    _make_minor(
        db_session,
        parent_id=major.id,
        version_no="4.0.3.15.260512_alpha.3(40315003)",
        zentao_build_id=4614,
    )
    _make_minor(
        db_session,
        parent_id=major.id,
        version_no="4.0.3.15.260520_alpha.4(40315004)",
        zentao_build_id=4615,
    )
    _make_minor(
        db_session,
        parent_id=major.id,
        version_no="4.0.3.15.26xxxx(4031xxxx)",
        zentao_build_id=4625,
    )

    class _StubClient:
        def list_execution_builds(self, exec_id, limit=500):
            assert exec_id == 1646
            return [
                {"id": 4625, "name": "4.0.3.15.26xxxx(4031xxxx)"},
                {"id": 4615, "name": "4.0.3.15.260520_alpha.4(40315004)"},
                {"id": 4614, "name": "4.0.3.15.260512_alpha.3(40315003)"},
            ]

    monkeypatch.setattr(
        "app.services.zentao_version_diff_service.get_system_zentao_client",
        lambda _db: _StubClient(),
    )

    out = compare_local_vs_zentao(db_session, major.id)
    diff = out["diff"]

    matched_remote_ids = {m["remote"]["id"] for m in diff["matched"]}
    assert matched_remote_ids == {4614, 4615, 4625}
    assert diff["only_local"] == []
    assert diff["only_remote"] == []
    # 占位 build 已匹配，就不应再出现在 remote_placeholders（仅作展示分类）
    assert diff["remote_placeholders"] == []


def test_compare_local_vs_zentao_unmatched_placeholder_still_in_placeholders(
    db_session, monkeypatch
):
    """禅道占位 build 未在本地建过行时，归到 remote_placeholders（不归 only_remote）。"""
    major = _make_major(db_session, version_no="V4.0.3.15", exec_id=1646)
    _make_minor(
        db_session,
        parent_id=major.id,
        version_no="4.0.3.15.260512_alpha.3(40315003)",
        zentao_build_id=4614,
    )

    class _StubClient:
        def list_execution_builds(self, exec_id, limit=500):
            return [
                {"id": 4625, "name": "4.0.3.15.26xxxx(4031xxxx)"},
                {"id": 4614, "name": "4.0.3.15.260512_alpha.3(40315003)"},
            ]

    monkeypatch.setattr(
        "app.services.zentao_version_diff_service.get_system_zentao_client",
        lambda _db: _StubClient(),
    )

    out = compare_local_vs_zentao(db_session, major.id)
    diff = out["diff"]

    matched_remote_ids = {m["remote"]["id"] for m in diff["matched"]}
    assert matched_remote_ids == {4614}
    assert diff["only_remote"] == []
    assert len(diff["remote_placeholders"]) == 1
    assert diff["remote_placeholders"][0]["id"] == 4625
