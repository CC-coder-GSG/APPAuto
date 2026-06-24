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


# ── 自动汇总规则 ────────────────────────────────────────────
def _marks_of(node):
    return {m["user_id"]: m for m in node["marks"]}


def test_cannot_manually_mark_node_with_children(db_session):
    sw = _software(db_session)
    user = _user(db_session, "u")
    svc = FeatureTreeService(db_session)
    root_id = svc.get_tree(sw.id)["tree"]["id"]
    svc.create_node(sw.id, root_id, "子", user)  # root 现在有子节点
    with pytest.raises(ValidationFailed):
        svc.set_mark(root_id, 1, "", user)


def test_marking_all_children_auto_marks_parent(db_session):
    sw = _software(db_session)
    user = _user(db_session, "u")
    svc = FeatureTreeService(db_session)
    root_id = svc.get_tree(sw.id)["tree"]["id"]
    a = svc.create_node(sw.id, root_id, "A", user)
    b = svc.create_node(sw.id, root_id, "B", user)
    v = 500

    svc.set_mark(a["id"], v, "", user)
    # 只标了 A，root 不应自动标记
    assert _marks_of(svc.get_tree(sw.id, version_id=v)["tree"]) == {}

    svc.set_mark(b["id"], v, "", user)
    # A、B 全标 → root 自动标记，且 is_auto=True
    root_marks = _marks_of(svc.get_tree(sw.id, version_id=v)["tree"])
    assert user.id in root_marks
    assert root_marks[user.id]["is_auto"] is True


def test_auto_propagates_to_grandparent(db_session):
    sw = _software(db_session)
    user = _user(db_session, "u")
    svc = FeatureTreeService(db_session)
    root_id = svc.get_tree(sw.id)["tree"]["id"]
    p = svc.create_node(sw.id, root_id, "P", user)
    c1 = svc.create_node(sw.id, p["id"], "c1", user)
    c2 = svc.create_node(sw.id, p["id"], "c2", user)
    v = 7

    svc.set_mark(c1["id"], v, "", user)
    svc.set_mark(c2["id"], v, "", user)

    tree = svc.get_tree(sw.id, version_id=v)["tree"]
    p_node = tree["children"][0]
    assert _marks_of(p_node)[user.id]["is_auto"] is True       # 父自动
    assert _marks_of(tree)[user.id]["is_auto"] is True          # 祖父（root）也自动


def test_unmarking_child_removes_parent_auto(db_session):
    sw = _software(db_session)
    user = _user(db_session, "u")
    svc = FeatureTreeService(db_session)
    root_id = svc.get_tree(sw.id)["tree"]["id"]
    a = svc.create_node(sw.id, root_id, "A", user)
    v = 3
    svc.set_mark(a["id"], v, "", user)
    assert user.id in _marks_of(svc.get_tree(sw.id, version_id=v)["tree"])  # root 自动

    svc.delete_mark(a["id"], v, user)
    assert _marks_of(svc.get_tree(sw.id, version_id=v)["tree"]) == {}        # 自动撤销


def test_cannot_delete_auto_mark(db_session):
    sw = _software(db_session)
    user = _user(db_session, "u")
    svc = FeatureTreeService(db_session)
    root_id = svc.get_tree(sw.id)["tree"]["id"]
    a = svc.create_node(sw.id, root_id, "A", user)
    v = 9
    svc.set_mark(a["id"], v, "", user)  # root 得到自动标记
    with pytest.raises(ValidationFailed):
        svc.delete_mark(root_id, v, user)


def test_adding_child_invalidates_parent_auto(db_session):
    sw = _software(db_session)
    user = _user(db_session, "u")
    svc = FeatureTreeService(db_session)
    root_id = svc.get_tree(sw.id)["tree"]["id"]
    a = svc.create_node(sw.id, root_id, "A", user)
    v = 11
    svc.set_mark(a["id"], v, "", user)
    assert user.id in _marks_of(svc.get_tree(sw.id, version_id=v)["tree"])  # root 自动

    svc.create_node(sw.id, root_id, "B", user)  # 新增未标记子分支
    assert _marks_of(svc.get_tree(sw.id, version_id=v)["tree"]) == {}        # root 自动失效


def test_deleting_unmarked_child_triggers_parent_auto(db_session):
    sw = _software(db_session)
    user = _user(db_session, "u")
    svc = FeatureTreeService(db_session)
    root_id = svc.get_tree(sw.id)["tree"]["id"]
    a = svc.create_node(sw.id, root_id, "A", user)
    b = svc.create_node(sw.id, root_id, "B", user)
    v = 13
    svc.set_mark(a["id"], v, "", user)  # 只标 A，B 未标 → root 未自动
    assert _marks_of(svc.get_tree(sw.id, version_id=v)["tree"]) == {}

    svc.delete_node(b["id"])  # 删掉未标记的 B → 剩余子(A)全标 → root 自动
    assert _marks_of(svc.get_tree(sw.id, version_id=v)["tree"])[user.id]["is_auto"] is True
