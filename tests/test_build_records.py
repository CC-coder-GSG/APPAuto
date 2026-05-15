from app.models import BuildRecord
from app.schemas.admin import JenkinsBuildReportPayload
from app.services.build_record_service import BuildRecordService


def test_upsert_build_report_creates_record(db_session):
    service = BuildRecordService(db_session)

    record, action = service.upsert_report(
        job_name="s40311",
        build_number="6",
        build_status="SUCCESS",
        version_name="4.0.3.11.260316_alpha.3(40311003)",
        branch="dev/dubhe",
        build_url="http://jenkins/job/s40311/6/",
        change_log="build report from jenkins",
    )

    assert action == "created"
    assert record.id is not None
    assert record.job_name == "s40311"
    assert record.build_number == "6"
    assert record.version_name == "4.0.3.11.260316_alpha.3(40311003)"


def test_upsert_build_report_updates_existing_record(db_session):
    service = BuildRecordService(db_session)
    first, first_action = service.upsert_report(
        job_name="s40311",
        build_number="6",
        build_status="FAILURE",
        version_name=None,
        branch=None,
        build_url=None,
        change_log="first",
    )

    second, second_action = service.upsert_report(
        job_name="s40311",
        build_number=6,
        build_status="SUCCESS",
        version_name="4.0.3.11.260316_alpha.3(40311003)",
        branch="release/s40311",
        build_url="http://jenkins/job/s40311/6/",
        change_log="second",
    )

    rows = db_session.query(BuildRecord).filter(BuildRecord.job_name == "s40311", BuildRecord.build_number == "6").all()
    assert first_action == "created"
    assert second_action == "updated"
    assert first.id == second.id
    assert len(rows) == 1
    assert second.build_status == "SUCCESS"
    assert second.version_name == "4.0.3.11.260316_alpha.3(40311003)"
    assert second.change_log == "second"


def test_list_build_records_supports_filters(db_session):
    service = BuildRecordService(db_session)
    service.upsert_report(job_name="s40311", build_number="6", build_status="SUCCESS")
    service.upsert_report(job_name="s40311", build_number="7", build_status="FAILURE")
    service.upsert_report(job_name="wosense-build", build_number="8", build_status="SUCCESS")

    result = service.list_records(limit=20, offset=0, job_name="s40311", build_status="SUCCESS")

    assert result["total"] == 1
    assert len(result["items"]) == 1
    assert result["items"][0]["job_name"] == "s40311"
    assert result["items"][0]["build_status"] == "SUCCESS"


def test_build_report_payload_accepts_optional_version_name():
    payload = JenkinsBuildReportPayload(
        job_name=" s40311 ",
        build_number=6,
        build_status="success",
        version_name=" 4.0.3.11.260316_alpha.3(40311003) ",
        branch=" ",
        build_url=" http://jenkins/job/s40311/6/ ",
        change_log=" build report from jenkins ",
    )

    assert payload.job_name == "s40311"
    assert payload.build_number == "6"
    assert payload.build_status == "SUCCESS"
    assert payload.version_name == "4.0.3.11.260316_alpha.3(40311003)"
    assert payload.branch is None
    assert payload.build_url == "http://jenkins/job/s40311/6/"


def test_retry_zentao_push_re_runs_push_and_returns_status(db_session):
    service = BuildRecordService(db_session)
    record, _ = service.upsert_report(
        job_name="s40311",
        build_number="9",
        build_status="SUCCESS",
        version_name="4.0.3.11.260316_alpha.3(40311003)",
    )
    # 没绑定大版本 → 首跑就是 no_execution；重试应当复现同一结果而不抛错
    initial_status = record.zentao_push_status

    item, status = service.retry_zentao_push(record.id)

    assert item["id"] == record.id
    # 状态可能是 no_execution / no_binding / not_success，但必须是确定的字符串
    assert status
    assert status == item["zentao_push_status"]
    # 重试不应该把成功状态翻成 None
    if initial_status:
        assert item["zentao_push_status"] is not None


def test_retry_zentao_push_raises_on_missing_record(db_session):
    import pytest
    service = BuildRecordService(db_session)
    with pytest.raises(ValueError):
        service.retry_zentao_push(99999)


def test_reassign_major_moves_zentao_build_via_put(db_session, monkeypatch):
    """禅道 IPD 4.3 不允许 API DELETE build。切归属时必须走 PUT 把原 build 直接
    搬到目标执行下，而不是"新建 + 删除"。这条 test 锁死这个行为，防止以后回退。"""
    from app.models import Version
    from app.models.enums import VersionType
    from app.services import zentao_version_diff_service as svc
    from app.services.build_record_service import BuildRecordService

    source_major = Version(version_no="V4.0.3.1", version_type=VersionType.MAJOR,
                           software_id=1, zentao_execution_id=1647)
    target_major = Version(version_no="V4.0.3.20", version_type=VersionType.MAJOR,
                           software_id=1, zentao_execution_id=1822)
    db_session.add_all([source_major, target_major])
    db_session.commit()

    minor = Version(version_no="4.0.3.1.260514(40301051)", version_type=VersionType.MINOR,
                    parent_id=source_major.id, software_id=1,
                    zentao_build_id=4591, zentao_build_name_cache="4.0.3.1.260514(40301051)")
    db_session.add(minor)
    db_session.commit()

    record, _ = BuildRecordService(db_session).upsert_report(
        job_name="s4031", build_number="51", build_status="SUCCESS",
        version_name="4.0.3.1.260514(40301051)",
    )
    record.auto_archive_minor_version_id = minor.id
    db_session.commit()

    calls = []

    class _StubClient:
        def get(self, path):
            calls.append(("get", path))
            return {"id": 1822, "name": "s4031定制", "project": 134, "products": [{"id": 15}]}

        def update_build(self, build_id, **kwargs):
            calls.append(("update_build", build_id, kwargs))
            return {"id": build_id, **kwargs}

        def delete_build(self, build_id):
            calls.append(("delete_build", build_id))
            raise AssertionError("reassign must NOT call delete_build — DELETE is forbidden on Zentao IPD 4.3")

        def create_execution_build(self, *args, **kwargs):
            calls.append(("create_execution_build", args, kwargs))
            raise AssertionError("reassign must NOT create a new build when one already exists — use PUT move instead")

    monkeypatch.setattr(svc, "get_system_zentao_client", lambda db: _StubClient())

    out = svc.build_record_reassign_major(db_session, record.id, target_major.id)

    assert out["ok"] is True
    assert out["moved_zentao_build_id"] == 4591
    assert out["new_zentao_build_id"] is None
    # PUT carried execution=1822 (the target)
    assert any(c[0] == "update_build" and c[2].get("execution_id") == 1822 for c in calls)

    db_session.refresh(minor)
    assert minor.parent_id == target_major.id
    assert minor.zentao_build_id == 4591  # same build, just moved

    # 切归属成功后，"禅道写回"卡片要立刻反映新位置：execution 名 + build 名 + build #id
    db_session.refresh(record)
    assert record.zentao_push_status == "ok"
    assert "s4031定制" in (record.zentao_push_message or "")
    assert "#1822" in (record.zentao_push_message or "")
    assert "#4591" in (record.zentao_push_message or "")
    assert "4.0.3.1.260514(40301051)" in (record.zentao_push_message or "")
    assert "切归属" in (record.zentao_push_message or "")
    assert record.zentao_pushed_at is not None


def test_reassign_major_creates_when_no_existing_build(db_session, monkeypatch):
    """如果 minor 从来没有禅道 build，move 没东西可移 → 退回到"在目标执行下新建"。"""
    from app.models import Version
    from app.models.enums import VersionType
    from app.services import zentao_version_diff_service as svc
    from app.services.build_record_service import BuildRecordService

    source_major = Version(version_no="V4.0.3.1", version_type=VersionType.MAJOR,
                           software_id=1, zentao_execution_id=1647)
    target_major = Version(version_no="V4.0.3.20", version_type=VersionType.MAJOR,
                           software_id=1, zentao_execution_id=1822)
    db_session.add_all([source_major, target_major])
    db_session.commit()

    minor = Version(version_no="4.0.3.1.260514(40301051)", version_type=VersionType.MINOR,
                    parent_id=source_major.id, software_id=1,
                    zentao_build_id=None)
    db_session.add(minor)
    db_session.commit()

    record, _ = BuildRecordService(db_session).upsert_report(
        job_name="s4031", build_number="52", build_status="SUCCESS",
        version_name="4.0.3.1.260514(40301051)",
    )
    record.auto_archive_minor_version_id = minor.id
    db_session.commit()

    class _StubClient:
        def get(self, path):
            assert path == "executions/1822"
            # 同时给上 PM —— 反映真实禅道返回，并要求 reassign 把它读为 builder
            return {
                "id": 1822, "name": "s4031定制",
                "project": 134, "products": [{"id": 15}],
                "PM": {"account": "zhangchao", "realname": "张超"},
            }

        def update_build(self, *args, **kwargs):
            raise AssertionError("no existing build → should NOT call update_build")

        def create_execution_build(self, exec_id, name, **kwargs):
            assert exec_id == 1822
            assert kwargs.get("project_id") == 134
            # 关键回归：禅道 POST 不带 builder 会 400 "构建者不能为空"
            assert kwargs.get("builder"), "reassign create must pass a non-empty builder"
            assert kwargs.get("builder") == "zhangchao"
            return {"id": 9001, "name": name}

    monkeypatch.setattr(svc, "get_system_zentao_client", lambda db: _StubClient())

    out = svc.build_record_reassign_major(db_session, record.id, target_major.id)

    assert out["ok"] is True
    assert out["new_zentao_build_id"] == 9001
    assert out["moved_zentao_build_id"] is None

    db_session.refresh(minor)
    assert minor.zentao_build_id == 9001


def test_reassign_message_is_fresh_not_appended(db_session, monkeypatch):
    """切归属后，zentao_push_message 不应该是"旧 push 日志 | 切归属：..."这种 append 形态——
    旧位置信息会主导视觉，用户以为没变。改成"切归属 → 新位置..."的崭新一句话。"""
    from app.models import Version
    from app.models.enums import VersionType
    from app.services import zentao_version_diff_service as svc
    from app.services.build_record_service import BuildRecordService

    source_major = Version(version_no="V4.0.3.1", version_type=VersionType.MAJOR,
                           software_id=1, zentao_execution_id=1647)
    target_major = Version(version_no="V4.0.3.1.custom", version_type=VersionType.MAJOR,
                           software_id=1, zentao_execution_id=1822)
    db_session.add_all([source_major, target_major])
    db_session.commit()

    minor = Version(version_no="v.test", version_type=VersionType.MINOR,
                    parent_id=source_major.id, software_id=1, zentao_build_id=7777)
    db_session.add(minor)
    db_session.commit()

    record, _ = BuildRecordService(db_session).upsert_report(
        job_name="s4031", build_number="99", build_status="SUCCESS", version_name="v.test",
    )
    record.auto_archive_minor_version_id = minor.id
    # 模拟之前推送过禅道留下的旧 message
    record.zentao_push_message = "s4031 #1647: 重命名为 \"v.test\" #7777; 占位 \"v.xxxx\" #7778"
    record.zentao_push_status = "ok"
    db_session.commit()

    class _StubClient:
        def get(self, path):
            return {"id": 1822, "name": "s4031定制", "project": 134, "products": [{"id": 15}], "PM": {"account": "zhangchao"}}

        def update_build(self, build_id, **kwargs):
            return {"id": build_id, **kwargs}

    monkeypatch.setattr(svc, "get_system_zentao_client", lambda db: _StubClient())

    svc.build_record_reassign_major(db_session, record.id, target_major.id)
    db_session.refresh(record)

    msg = record.zentao_push_message or ""
    # 新 message 必须以"切归属 → "开头，且不能包含旧 push log 的痕迹（"重命名为"）
    assert msg.startswith("切归属 → "), f"message must start fresh, got: {msg}"
    assert "重命名为" not in msg, f"must NOT carry old push log forward, got: {msg}"
    assert "s4031定制" in msg
    assert "#1822" in msg
    assert "#7777" in msg


def test_reassign_publishes_sse_for_live_clients(db_session, monkeypatch):
    """切归属后端要主动推 SSE，否则其他打开页面的客户端看不到禅道写回卡片的变更。"""
    from app.models import Version
    from app.models.enums import VersionType
    from app.services import zentao_version_diff_service as svc
    from app.services.build_record_service import BuildRecordService
    import app.services.sse_service as sse

    target_major = Version(version_no="V4.0.3.1.custom", version_type=VersionType.MAJOR,
                           software_id=1, zentao_execution_id=1822)
    source_major = Version(version_no="V4.0.3.1", version_type=VersionType.MAJOR,
                           software_id=1, zentao_execution_id=1647)
    db_session.add_all([source_major, target_major])
    db_session.commit()

    minor = Version(version_no="v.sse", version_type=VersionType.MINOR,
                    parent_id=source_major.id, software_id=1, zentao_build_id=8888)
    db_session.add(minor)
    db_session.commit()

    record, _ = BuildRecordService(db_session).upsert_report(
        job_name="s4031", build_number="100", build_status="SUCCESS", version_name="v.sse",
    )
    record.auto_archive_minor_version_id = minor.id
    db_session.commit()

    sse_calls = []

    class _StubClient:
        def get(self, path):
            return {"id": 1822, "name": "s4031定制", "project": 134, "products": [{"id": 15}], "PM": {"account": "zhangchao"}}

        def update_build(self, build_id, **kwargs):
            return {"id": build_id}

    monkeypatch.setattr(svc, "get_system_zentao_client", lambda db: _StubClient())
    monkeypatch.setattr(sse, "sse_publish", lambda event, payload, channels=None: sse_calls.append((event, payload, channels)))

    svc.build_record_reassign_major(db_session, record.id, target_major.id)

    events = [c[0] for c in sse_calls]
    assert "build_record_updated" in events, f"reassign must publish build_record_updated; got {events}"


def test_reassign_create_falls_back_to_current_user_when_pm_missing(db_session, monkeypatch):
    """执行的 PM/openedBy 都没解析到时（理论上罕见）必须回退到 /v1/user.profile.account，
    否则 create build 会被禅道挡为 400 "构建者不能为空"。"""
    from app.models import Version
    from app.models.enums import VersionType
    from app.services import zentao_version_diff_service as svc
    from app.services.build_record_service import BuildRecordService

    source_major = Version(version_no="V4.0.3.1", version_type=VersionType.MAJOR,
                           software_id=1, zentao_execution_id=1647)
    target_major = Version(version_no="V4.0.3.20", version_type=VersionType.MAJOR,
                           software_id=1, zentao_execution_id=1822)
    db_session.add_all([source_major, target_major])
    db_session.commit()

    minor = Version(version_no="v.x", version_type=VersionType.MINOR,
                    parent_id=source_major.id, software_id=1, zentao_build_id=None)
    db_session.add(minor)
    db_session.commit()

    record, _ = BuildRecordService(db_session).upsert_report(
        job_name="s4031", build_number="53", build_status="SUCCESS", version_name="v.x",
    )
    record.auto_archive_minor_version_id = minor.id
    db_session.commit()

    class _StubClient:
        def get(self, path):
            if path == "executions/1822":
                # PM / openedBy 都缺
                return {"id": 1822, "name": "s4031定制", "project": 134, "products": [{"id": 15}]}
            if path == "user":
                return {"profile": {"account": "chenwenbo", "realname": "陈文博"}}
            raise AssertionError(f"unexpected get path: {path}")

        def create_execution_build(self, exec_id, name, **kwargs):
            assert kwargs.get("builder") == "chenwenbo"
            return {"id": 9100, "name": name}

    monkeypatch.setattr(svc, "get_system_zentao_client", lambda db: _StubClient())

    out = svc.build_record_reassign_major(db_session, record.id, target_major.id)
    assert out["ok"] is True
    assert out["new_zentao_build_id"] == 9100


def test_reassign_major_does_not_early_error_when_local_parent_matches_target(db_session, monkeypatch):
    """旧逻辑：local minor.parent_id == target_major.id → hard fail "已经在该大版本"。
    新逻辑：local 状态可能跟禅道脱节（早期 reassign 失败留下来的），用户重复点击
    "切到 X" 恰恰是想 reconcile —— 不能 early-return，必须再跑一遍禅道侧动作。"""
    from app.models import Version
    from app.models.enums import VersionType
    from app.services import zentao_version_diff_service as svc
    from app.services.build_record_service import BuildRecordService

    target_major = Version(version_no="V4.0.3.1.custom", version_type=VersionType.MAJOR,
                           software_id=1, zentao_execution_id=1822)
    db_session.add(target_major)
    db_session.commit()

    # minor.parent_id 已经指向 target —— 用户重复点 "切到 target"，老逻辑会拒绝
    minor = Version(version_no="4.0.3.1.260513_Gnss7(40301050)", version_type=VersionType.MINOR,
                    parent_id=target_major.id, software_id=1,
                    zentao_build_id=5001)
    db_session.add(minor)
    db_session.commit()

    record, _ = BuildRecordService(db_session).upsert_report(
        job_name="s4031", build_number="50", build_status="SUCCESS",
        version_name="4.0.3.1.260513_Gnss7(40301050)",
    )
    record.auto_archive_minor_version_id = minor.id
    db_session.commit()

    update_calls = []

    class _StubClient:
        def get(self, path):
            return {"id": 1822, "name": "s4031定制", "project": 134, "products": [{"id": 15}]}

        def update_build(self, build_id, **kwargs):
            update_calls.append((build_id, kwargs))
            return {"id": build_id, **kwargs}

    monkeypatch.setattr(svc, "get_system_zentao_client", lambda db: _StubClient())

    out = svc.build_record_reassign_major(db_session, record.id, target_major.id)

    # 不再 "目标大版本就是当前大版本，无需切换"；走完一整轮禅道侧 PUT
    assert out["ok"] is True
    assert out["moved_zentao_build_id"] == 5001
    assert update_calls == [(5001, {"execution_id": 1822})]


def test_zentao_push_message_shows_execution_and_build_names(db_session, monkeypatch):
    """禅道写回卡片不能只显示 'exec=1647 renamed=4585'。
    要把执行名（'s4031'）和 build 名（version_name）都摆出来才便于查看。"""
    from app.models import Version
    from app.models.enums import VersionType
    from app.services.build_record_service import BuildRecordService
    from app.services import zentao_build_push_service as svc

    major = Version(version_no="V4.0.3.1", version_type=VersionType.MAJOR,
                    software_id=1, zentao_execution_id=1647)
    db_session.add(major)
    db_session.commit()

    record, _ = BuildRecordService(db_session).upsert_report(
        job_name="s4031", build_number="50", build_status="SUCCESS",
        version_name="4.0.3.1.260513_Gnss7(40301050)",
    )

    class _StubClient:
        def list_execution_builds(self, exec_id, **kwargs):
            # 一个占位 build 等着被重命名
            return [{"id": 4500, "name": "4.0.3.1.26xxxx(4030xxxx)"}]

        def get(self, path):
            return {"id": 1647, "name": "s4031", "project": 134, "products": [{"id": 15}]}

        def update_build(self, build_id, **kwargs):
            return {"id": build_id, **kwargs}

        def create_execution_build(self, exec_id, name, **kwargs):
            # 占位补建
            return {"id": 4502, "name": name}

        def list_projects(self, **kwargs):
            return []

    monkeypatch.setattr(svc, "get_system_zentao_client", lambda db: _StubClient())

    push = svc.push_build_to_zentao(db_session, record)
    svc.apply_push_result_to_record(record, push)

    msg = record.zentao_push_message or ""
    assert "s4031" in msg                                # execution 名
    assert "#1647" in msg                                # execution id
    assert "4.0.3.1.260513_Gnss7(40301050)" in msg       # real build 名
    assert "#4500" in msg                                # renamed build id
    assert "4.0.3.1.26xxxx(4030xxxx)" in msg             # 占位 build 名
    assert "#4502" in msg                                # 占位 build id
    # 不再用 'exec=' / 'renamed=' / 'placeholder=' 这种代号
    assert "exec=" not in msg
    assert "renamed=" not in msg


def test_get_major_log_aggregates_change_logs_in_created_order(db_session):
    service = BuildRecordService(db_session)
    service.upsert_report(
        job_name="s40311",
        build_number="5",
        build_status="SUCCESS",
        version_name="4.0.3.11.260316_alpha.1(40311001)",
        change_log="修复 A",
    )
    service.upsert_report(
        job_name="s40311",
        build_number="6",
        build_status="SUCCESS",
        version_name="4.0.3.11.260316_alpha.2(40311002)",
        change_log="修复 B",
    )
    service.upsert_report(
        job_name="s40311",
        build_number="7",
        build_status="SUCCESS",
        version_name="4.0.3.11.260316_alpha.3(40311003)",
        change_log="",
    )

    result = service.get_major_log("s40311")

    assert result["job_name"] == "s40311"
    assert result["record_count"] == 3
    assert result["latest_build_number"] == "7"
    assert "## 4.0.3.11.260316_alpha.1(40311001)\n修复 A" in result["major_log"]
    assert "## 4.0.3.11.260316_alpha.2(40311002)\n修复 B" in result["major_log"]
    assert "alpha.3" not in result["major_log"]
