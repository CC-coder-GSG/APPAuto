from app.models import Version, VersionType
from app.services import zentao_build_names
from app.services.zentao_build_names import resolve_build_labels


class _FakeClient:
    def __init__(self, builds):
        self.builds = builds
        self.calls = []

    def get_build(self, build_id):
        self.calls.append(build_id)
        if build_id not in self.builds:
            raise RuntimeError("not found")
        return {"id": build_id, "name": self.builds[build_id]}


def _minor(db_session, version_no, build_id):
    major = Version(version_no="V4.0.5.0", version_type=VersionType.MAJOR)
    db_session.add(major)
    db_session.commit()
    row = Version(version_no=version_no, version_type=VersionType.MINOR, parent_id=major.id, zentao_build_id=build_id)
    db_session.add(row)
    db_session.commit()
    return row


def test_build_ids_resolve_to_version_names(db_session):
    zentao_build_names._REMOTE_NAME_CACHE.clear()
    _minor(db_session, "4.0.4.16.261124_fm(40416008)", 5329)
    client = _FakeClient({5432: "4.0.5.0.261123_alpha.34(40500035)(64-bit)"})

    opened, resolved = resolve_build_labels(db_session, client, ["5329", "5432"])

    # 本地小版本直接命中，不回源禅道；远端名称去掉架构后缀
    assert opened == "4.0.4.16.261124_fm(40416008)"
    assert resolved == "4.0.5.0.261123_alpha.34(40500035)"
    assert client.calls == [5432]

    # 第二次走进程内缓存
    resolve_build_labels(db_session, client, ["", "5432"])
    assert client.calls == [5432]


def test_build_labels_keep_names_trunk_and_unknown_ids(db_session):
    zentao_build_names._REMOTE_NAME_CACHE.clear()
    client = _FakeClient({})

    labels = resolve_build_labels(db_session, client, [
        "trunk",
        "4.0.4.0.260715(40400034)(64-bit), 9999",
        "",
    ])

    assert labels == ["主干", "4.0.4.0.260715(40400034), 9999", ""]


def test_bug_preview_shows_build_version_names(db_session, monkeypatch):
    from app.api.routes import zentao_bug_actions
    from app.models import User, UserRole

    zentao_build_names._REMOTE_NAME_CACHE.clear()
    _minor(db_session, "4.0.4.16.261124_fm(40416008)", 5329)
    user = User(username="preview_user", password_hash=User.hash_password("pass123"), role=UserRole.USER)
    db_session.add(user)
    db_session.commit()

    class Client(_FakeClient):
        def get_bug_with_fallback(self, zt_id):
            return {"id": zt_id, "title": "林业大师分享功能无法使用", "status": "resolved",
                    "openedBuild": "5329", "resolvedBuild": "5432", "actions": []}

    client = Client({5432: "4.0.5.0.261123_alpha.34(40500035)(64-bit)"})
    monkeypatch.setattr(zentao_bug_actions, "_get_client", lambda user_id, db: client)

    preview = zentao_bug_actions.get_bug_preview(34186, current_user=user, db=db_session)

    assert preview["opened_build"] == "4.0.4.16.261124_fm(40416008)"
    assert preview["resolved_build"] == "4.0.5.0.261123_alpha.34(40500035)"
    assert (preview["opened_build_ref"], preview["resolved_build_ref"]) == ("5329", "5432")
