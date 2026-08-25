"""同一需求跨大版本时，工作台归集所有大版本上的关联 Bug（2026-07-15）。

历史行为：build_requirement_case_view / build_requirement_free_bug_view /
build_retest_evidence 都按「当前查看需求所在的大版本」过滤 BugTracking，
另一个大版本上同一 story 的 Bug 被隐藏。现在不再按大版本过滤，序列化
输出带 major_version_id/major_version_no，前端据此加「来自 X」徽章。
"""
from __future__ import annotations

from app.models import BugSourceType, BugTracking, Requirement, User, UserRole, Version, VersionType
from app.services.workbench_link_service import WorkbenchLinkService


def _seed_two_majors(db):
    user = User(username="xm_user", password_hash="x", role=UserRole.USER)
    major_a = Version(version_no="V-XM-A", version_type=VersionType.MAJOR)
    major_b = Version(version_no="V-XM-B", version_type=VersionType.MAJOR)
    db.add_all([user, major_a, major_b])
    db.flush()
    # 同一禅道需求（story 7001）分别挂在两个大版本下
    req_a = Requirement(
        zentao_req_id="r#7001", title="跨版本需求", major_version_id=major_a.id,
        owner_id=user.id, zentao_story_id=7001,
    )
    db.add(req_a)
    db.commit()
    db.refresh(req_a)
    return user, major_a, major_b, req_a


def test_free_bug_view_includes_other_major_with_source_tag(db_session):
    user, major_a, major_b, req_a = _seed_two_majors(db_session)
    bug_same = BugTracking(
        major_version_id=major_a.id, source_type=BugSourceType.MANUAL,
        bug_id="b#71001", zentao_bug_id="71001", zentao_story_id=7001,
        created_by_id=user.id, zentao_deleted=False,
    )
    bug_other = BugTracking(
        major_version_id=major_b.id, source_type=BugSourceType.MANUAL,
        bug_id="b#71002", zentao_bug_id="71002", zentao_story_id=7001,
        created_by_id=user.id, zentao_deleted=False,
    )
    db_session.add_all([bug_same, bug_other])
    db_session.commit()

    svc = WorkbenchLinkService(db_session)
    free_map, _ = svc.build_requirement_free_bug_view([req_a], {}, include_retest=False)
    bugs = {b["bug_id"]: b for b in free_map.get(req_a.id, [])}

    # 本大版本 + 其他大版本的同 story Bug 都要出现
    assert set(bugs) == {"b#71001", "b#71002"}
    # 序列化带大版本来源，前端比对后为跨版本 Bug 加「来自 X」徽章
    assert bugs["b#71001"]["major_version_id"] == major_a.id
    assert bugs["b#71002"]["major_version_id"] == major_b.id
    assert bugs["b#71002"]["major_version_no"] == "V-XM-B"


def test_found_version_falls_back_to_affected_string(db_session):
    """发现于：无本地小版本映射时显示禅道侧版本字符串而非「未知」。"""
    user, major_a, _major_b, _req = _seed_two_majors(db_session)
    bug = BugTracking(
        major_version_id=major_a.id, source_type=BugSourceType.MANUAL,
        bug_id="b#71003", zentao_bug_id="71003",
        zentao_affected_version="4.0.4.0.260715(40400034)",
        created_by_id=user.id, zentao_deleted=False,
    )
    db_session.add(bug)
    db_session.commit()

    brief = WorkbenchLinkService(db_session).serialize_bug_brief(bug, {})
    assert brief["found_minor_version_no"] == "4.0.4.0.260715(40400034)"

    # 两者皆无时仍显示未知
    bug.zentao_affected_version = None
    brief2 = WorkbenchLinkService(db_session).serialize_bug_brief(bug, {})
    assert brief2["found_minor_version_no"] == "未知"
