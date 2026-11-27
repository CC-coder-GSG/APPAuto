import time

from app.api.routes import zentao_bug_actions as routes
from app.models import BugSourceType, BugTracking, User, UserRole, Version, VersionType


class _Client:
    def __init__(self, delay=0.0):
        self.delay = delay
        self.page_calls = 0
        self.build_calls = []

    def get_bug_action_meta(self, zt_id, action):
        self.page_calls += 1
        time.sleep(self.delay)
        return {
            "users": {"": "", "chenwenbo": "C:陈文博", "wangjie": "W:王杰", "zhangsan": "Z:张三"},
            "builds": {
                "5301": "4.0.4.16.261020_fm_alpha.1(40416001)(64-bit)",
                "5329": "4.0.4.16.261124_fm(40416008)(64-bit)",
                "5432": "4.0.5.0.261127(40500040)(64-bit)",
                "trunk": "主干",
            },
            "bug": {"id": zt_id, "title": "林业大师分享功能无法使用", "execution": 2033,
                    "assignedTo": "chenwenbo", "resolvedBy": "wangjie", "openedBuild": "5329"},
        }

    def get_execution_build_ids(self, execution_id):
        self.build_calls.append(execution_id)
        time.sleep(self.delay)
        return ["5301", "5329", "trunk"]


def _setup(db_session, monkeypatch, client):
    routes._META_CACHE.clear()
    routes._EXEC_BUILDS_CACHE.clear()
    user = User(username="meta_user", password_hash=User.hash_password("pass123"), role=UserRole.USER)
    major = Version(version_no="V4.0.4.16", version_type=VersionType.MAJOR)
    db_session.add_all([user, major])
    db_session.commit()
    db_session.add(BugTracking(major_version_id=major.id, source_type=BugSourceType.MANUAL, bug_id="b#34186",
                               created_by_id=user.id, zentao_bug_id="34186", zentao_execution_id="2033"))
    db_session.commit()
    monkeypatch.setattr(routes, "_get_client", lambda user_id, db: client)
    return user


def test_action_meta_lists_newest_builds_first_with_clean_names(db_session, monkeypatch):
    client = _Client()
    user = _setup(db_session, monkeypatch, client)

    meta = routes.get_bug_action_meta(34186, action="activate", fresh=False, current_user=user, db=db_session)

    # 只保留本执行的构建（5432 属于别的执行），新构建在前，主干放最后
    assert [b["id"] for b in meta["build_list"]] == ["5329", "5301", "trunk"]
    assert meta["build_list"][0]["name"] == "4.0.4.16.261124_fm(40416008)"
    assert meta["build_list"][-1]["name"] == "主干"
    assert meta["users"] == {"chenwenbo": "陈文博", "wangjie": "王杰", "zhangsan": "张三"}
    assert (meta["current_assigned"], meta["resolved_by"]) == ("chenwenbo", "wangjie")
    assert meta["opened_builds"] == ["5329"] and meta["title"] == "林业大师分享功能无法使用"


def test_action_meta_fetches_page_and_builds_in_parallel_and_caches(db_session, monkeypatch):
    client = _Client(delay=0.3)
    user = _setup(db_session, monkeypatch, client)

    started = time.perf_counter()
    first = routes.get_bug_action_meta(34186, action="activate", fresh=False, current_user=user, db=db_session)
    elapsed = time.perf_counter() - started
    # 页面 0.3s + 构建 0.3s 并行，总耗时明显小于两者之和
    assert elapsed < 0.55, elapsed
    assert client.build_calls == [2033]

    started = time.perf_counter()
    again = routes.get_bug_action_meta(34186, action="activate", fresh=False, current_user=user, db=db_session)
    assert time.perf_counter() - started < 0.05
    assert again["cached"] is True and again["build_list"] == first["build_list"]
    assert client.page_calls == 1

    # 写后校验要绕过缓存
    routes.get_bug_action_meta(34186, action="activate", fresh=True, current_user=user, db=db_session)
    assert client.page_calls == 2
    # 执行构建列表单独缓存，不再重复请求
    assert client.build_calls == [2033]


def test_write_operations_drop_cached_meta(db_session, monkeypatch):
    client = _Client()
    user = _setup(db_session, monkeypatch, client)
    routes.get_bug_action_meta(34186, action="activate", fresh=False, current_user=user, db=db_session)
    assert any(k[1] == 34186 for k in routes._META_CACHE)

    routes._forget_bug_meta(34186)

    assert not any(k[1] == 34186 for k in routes._META_CACHE)
    routes.get_bug_action_meta(34186, action="activate", fresh=False, current_user=user, db=db_session)
    assert client.page_calls == 2
