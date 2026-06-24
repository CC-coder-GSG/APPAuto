from __future__ import annotations

import pytest

from app.core.exceptions import ValidationFailed
from app.models import FeatureTreeMark, FeatureTreeNode, SoftwareProduct, User, UserRole
from app.services.feature_tree_service import FeatureTreeService, user_color


def _user(db, username: str, display: str | None = None) -> User:
    u = User(
        username=username,
        password_hash=User.hash_password("pw"),
        role=UserRole.USER,
        display_name=display,
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def _software(db, name: str = "测量软件A") -> SoftwareProduct:
    s = SoftwareProduct(name=name)
    db.add(s)
    db.commit()
    db.refresh(s)
    return s


def test_get_tree_lazily_creates_root_named_after_software(db_session):
    sw = _software(db_session, "CAD大师")
    svc = FeatureTreeService(db_session)
    data = svc.get_tree(sw.id)
    assert data["software_name"] == "CAD大师"
    root = data["tree"]
    assert root["is_root"] is True
    assert root["name"] == "CAD大师"
    assert root["children"] == []
    # 再次调用不应重复建根
    again = svc.get_tree(sw.id)
    assert again["tree"]["id"] == root["id"]
    assert db_session.query(FeatureTreeNode).filter_by(software_id=sw.id, is_root=True).count() == 1


def test_create_nested_branches(db_session):
    sw = _software(db_session)
    user = _user(db_session, "tester1")
    svc = FeatureTreeService(db_session)
    root_id = svc.get_tree(sw.id)["tree"]["id"]

    b1 = svc.create_node(sw.id, root_id, "绘图", user)
    sub = svc.create_node(sw.id, b1["id"], "直线", user)

    tree = svc.get_tree(sw.id)["tree"]
    assert tree["children"][0]["name"] == "绘图"
    assert tree["children"][0]["children"][0]["name"] == "直线"
    assert tree["children"][0]["children"][0]["id"] == sub["id"]


def test_blank_name_rejected(db_session):
    sw = _software(db_session)
    user = _user(db_session, "u")
    svc = FeatureTreeService(db_session)
    root_id = svc.get_tree(sw.id)["tree"]["id"]
    with pytest.raises(ValidationFailed):
        svc.create_node(sw.id, root_id, "   ", user)


def test_note_update_sets_metadata_and_has_note_flag(db_session):
    sw = _software(db_session)
    user = _user(db_session, "u")
    svc = FeatureTreeService(db_session)
    root_id = svc.get_tree(sw.id)["tree"]["id"]
    node = svc.create_node(sw.id, root_id, "功能X", user)

    svc.update_node(node["id"], user, note_html="<b>用途说明</b>")
    fetched = svc.get_tree(sw.id)["tree"]["children"][0]
    assert fetched["has_note"] is True
    assert "用途说明" in fetched["note_html"]
    assert fetched["note_updated_at"] is not None


def test_mark_upsert_and_color_by_user(db_session):
    sw = _software(db_session)
    author = _user(db_session, "author")
    t1 = _user(db_session, "alice", "Alice")
    t2 = _user(db_session, "bob", "Bob")
    svc = FeatureTreeService(db_session)
    root_id = svc.get_tree(sw.id)["tree"]["id"]
    node = svc.create_node(sw.id, root_id, "功能X", author)
    version_id = 9001

    svc.set_mark(node["id"], version_id, "<p>已覆盖主流程</p>", t1)
    svc.set_mark(node["id"], version_id, "看着没问题", t2)
    # 同一用户重复标记应 upsert，不新增行
    svc.set_mark(node["id"], version_id, "更新说明", t1)

    assert db_session.query(FeatureTreeMark).filter_by(node_id=node["id"], version_id=version_id).count() == 2

    marks = svc.get_tree(sw.id, version_id=version_id)["tree"]["children"][0]["marks"]
    by_user = {m["user_id"]: m for m in marks}
    assert by_user[t1.id]["color"] == user_color(t1.id)
    assert by_user[t1.id]["display_name"] == "Alice"
    assert "更新说明" in by_user[t1.id]["comment_html"]
    assert by_user[t2.id]["color"] == user_color(t2.id)
    # 不同用户配色不同（前两个调色板色值不同）
    assert by_user[t1.id]["color"] != by_user[t2.id]["color"]


def test_marks_scoped_by_version(db_session):
    sw = _software(db_session)
    user = _user(db_session, "u")
    svc = FeatureTreeService(db_session)
    root_id = svc.get_tree(sw.id)["tree"]["id"]
    node = svc.create_node(sw.id, root_id, "功能X", user)
    svc.set_mark(node["id"], 100, "v100", user)

    # 查询另一版本不应看到 v100 的标记
    other = svc.get_tree(sw.id, version_id=200)["tree"]["children"][0]
    assert other["marks"] == []
    # 不传 version_id 时也不附带标记
    none_ver = svc.get_tree(sw.id)["tree"]["children"][0]
    assert none_ver["marks"] == []


def test_delete_mark(db_session):
    sw = _software(db_session)
    user = _user(db_session, "u")
    svc = FeatureTreeService(db_session)
    root_id = svc.get_tree(sw.id)["tree"]["id"]
    node = svc.create_node(sw.id, root_id, "功能X", user)
    svc.set_mark(node["id"], 1, "x", user)
    svc.delete_mark(node["id"], 1, user)
    assert db_session.query(FeatureTreeMark).filter_by(node_id=node["id"]).count() == 0


def test_delete_node_cascades_subtree_and_marks(db_session):
    sw = _software(db_session)
    user = _user(db_session, "u")
    svc = FeatureTreeService(db_session)
    root_id = svc.get_tree(sw.id)["tree"]["id"]
    b1 = svc.create_node(sw.id, root_id, "父", user)
    child = svc.create_node(sw.id, b1["id"], "子", user)
    svc.set_mark(child["id"], 1, "x", user)

    svc.delete_node(b1["id"])

    assert db_session.query(FeatureTreeNode).filter_by(id=b1["id"]).first() is None
    assert db_session.query(FeatureTreeNode).filter_by(id=child["id"]).first() is None
    assert db_session.query(FeatureTreeMark).filter_by(node_id=child["id"]).count() == 0


def test_root_cannot_be_deleted(db_session):
    sw = _software(db_session)
    svc = FeatureTreeService(db_session)
    root_id = svc.get_tree(sw.id)["tree"]["id"]
    with pytest.raises(ValidationFailed):
        svc.delete_node(root_id)
