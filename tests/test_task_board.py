from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from app.core.exceptions import PermissionDenied, ValidationFailed
from app.models import (
    AuditLog,
    BugSourceType,
    BugTracking,
    Requirement,
    RequirementStatus,
    TaskBoardStatus,
    TaskBoardTask,
    TaskBoardUpdate,
    User,
    UserRole,
    Version,
    VersionType,
)
from app.services.task_board_service import (
    TaskBoardService,
    can_change_status,
    can_manage_board,
)


def _create_user(db, username: str, role: UserRole = UserRole.USER, is_team: bool = True) -> User:
    user = User(
        username=username,
        password_hash=User.hash_password("pw"),
        role=role,
        is_team_member=is_team,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _today() -> date:
    return date.today()


def test_admin_can_manage_board(db_session):
    admin = _create_user(db_session, "tb_admin", role=UserRole.ADMIN)
    assert can_manage_board(admin) is True


def test_plain_user_cannot_manage_board(db_session):
    plain = _create_user(db_session, "tb_plain")
    assert can_manage_board(plain) is False


def test_create_task_requires_management_permission(db_session):
    plain = _create_user(db_session, "tb_no_manage")
    service = TaskBoardService(db_session)
    with pytest.raises(PermissionDenied):
        service.create_task(actor=plain, title="阻塞任务")


def test_create_task_emits_create_and_assign_audit(db_session):
    admin = _create_user(db_session, "tb_creator", role=UserRole.ADMIN)
    assignee = _create_user(db_session, "tb_assignee")
    service = TaskBoardService(db_session)

    detail = service.create_task(
        actor=admin,
        title="验证登录闪退",
        description="复现并定位",
        assignee_id=assignee.id,
    )
    assert detail["title"] == "验证登录闪退"
    assert detail["status"] == TaskBoardStatus.TODO.value
    assert detail["assignee_id"] == assignee.id
    assert detail["assigner_id"] == admin.id

    actions = [r.action for r in db_session.query(AuditLog).order_by(AuditLog.id.asc()).all()]
    assert "task_board.create" in actions
    assert "task_board.assign" in actions


def test_status_transition_sets_started_and_completed(db_session):
    admin = _create_user(db_session, "tb_st_admin", role=UserRole.ADMIN)
    assignee = _create_user(db_session, "tb_st_assignee")
    service = TaskBoardService(db_session)
    created = service.create_task(actor=admin, title="跑回归", assignee_id=assignee.id)

    moved = service.update_status(actor=assignee, task_id=created["id"], status="in_progress", progress="开始执行")
    assert moved["status"] == TaskBoardStatus.IN_PROGRESS.value
    assert moved["started_at"] is not None
    assert moved["completed_at"] is None

    done = service.update_status(actor=assignee, task_id=created["id"], status="done")
    assert done["status"] == TaskBoardStatus.DONE.value
    assert done["completed_at"] is not None

    # Undoing completion clears completed_at.
    reopened = service.update_status(actor=admin, task_id=created["id"], status="in_progress")
    assert reopened["completed_at"] is None
    assert reopened["started_at"] is not None

    progress_rows = db_session.query(TaskBoardUpdate).filter(TaskBoardUpdate.task_id == created["id"]).all()
    assert len(progress_rows) == 1
    assert progress_rows[0].content == "开始执行"
    assert progress_rows[0].status_snapshot == TaskBoardStatus.IN_PROGRESS.value


def test_non_related_user_cannot_change_status(db_session):
    admin = _create_user(db_session, "tb_block_admin", role=UserRole.ADMIN)
    assignee = _create_user(db_session, "tb_block_assignee")
    intruder = _create_user(db_session, "tb_block_intruder")
    service = TaskBoardService(db_session)

    task = service.create_task(actor=admin, title="只允许相关人改", assignee_id=assignee.id)
    with pytest.raises(PermissionDenied):
        service.update_status(actor=intruder, task_id=task["id"], status="in_progress")


def test_can_change_status_for_assigner(db_session):
    admin = _create_user(db_session, "tb_assigner_admin", role=UserRole.ADMIN)
    assignee = _create_user(db_session, "tb_assigner_assignee")
    service = TaskBoardService(db_session)
    task_detail = service.create_task(actor=admin, title="派发任务", assignee_id=assignee.id)
    row = db_session.query(TaskBoardTask).filter(TaskBoardTask.id == task_detail["id"]).first()
    assert can_change_status(admin, row) is True
    assert can_change_status(assignee, row) is True


def test_list_board_groups_by_column_and_filters_mine(db_session):
    admin = _create_user(db_session, "tb_list_admin", role=UserRole.ADMIN)
    me = _create_user(db_session, "tb_list_me")
    other = _create_user(db_session, "tb_list_other")
    service = TaskBoardService(db_session)

    mine_task = service.create_task(actor=admin, title="我自己的", assignee_id=me.id)
    other_task = service.create_task(actor=admin, title="别人的", assignee_id=other.id)
    service.update_status(actor=admin, task_id=mine_task["id"], status="done")

    board_all = service.list_board(board_date=_today())
    assert board_all["summary"]["total"] == 2
    titles_done = [t["title"] for t in board_all["columns"]["done"]]
    titles_todo = [t["title"] for t in board_all["columns"]["todo"]]
    assert "我自己的" in titles_done
    assert "别人的" in titles_todo

    board_mine = service.list_board(board_date=_today(), mine_user_id=me.id)
    assert board_mine["summary"]["total"] == 1
    assert board_mine["columns"]["done"][0]["title"] == "我自己的"


def test_archive_hides_from_default_board(db_session):
    admin = _create_user(db_session, "tb_arch_admin", role=UserRole.ADMIN)
    service = TaskBoardService(db_session)
    task = service.create_task(actor=admin, title="待归档")
    service.archive_task(actor=admin, task_id=task["id"])

    board = service.list_board(board_date=_today())
    assert board["summary"]["total"] == 0

    board_with_archived = service.list_board(board_date=_today(), include_archived=True)
    assert board_with_archived["summary"]["total"] == 1


def test_carry_over_moves_board_date_and_marks_deferred(db_session):
    admin = _create_user(db_session, "tb_co_admin", role=UserRole.ADMIN)
    service = TaskBoardService(db_session)
    task = service.create_task(actor=admin, title="今天没做完")
    tomorrow = _today() + timedelta(days=1)
    moved = service.carry_over(actor=admin, task_id=task["id"], to_date=tomorrow)
    assert moved["board_date"] == tomorrow.isoformat()
    assert moved["deferred_from_date"] == _today().isoformat()
    assert moved["status"] == TaskBoardStatus.DEFERRED.value


def test_target_object_validation_requires_id_and_cached_label(db_session):
    admin = _create_user(db_session, "tb_tgt_admin", role=UserRole.ADMIN)
    service = TaskBoardService(db_session)

    major = Version(version_no="V1.0", version_type=VersionType.MAJOR)
    db_session.add(major)
    db_session.commit()
    db_session.refresh(major)
    req = Requirement(zentao_req_id="r#1001", title="验证目标对象", major_version_id=major.id, status=RequirementStatus.PENDING)
    db_session.add(req)
    db_session.commit()
    db_session.refresh(req)

    detail = service.create_task(
        actor=admin,
        title="排查需求",
        target_type="requirement",
        target_id=req.id,
    )
    assert detail["target_type"] == "requirement"
    assert detail["target_label"] == "r#1001 验证目标对象"

    with pytest.raises(ValidationFailed):
        service.create_task(actor=admin, title="缺 ID", target_type="bug")

    with pytest.raises(ValidationFailed):
        service.create_task(actor=admin, title="不存在的对象", target_type="bug", target_id=99999)


def test_update_assignee_writes_assign_audit_with_delta(db_session):
    admin = _create_user(db_session, "tb_re_admin", role=UserRole.ADMIN)
    first = _create_user(db_session, "tb_re_first")
    second = _create_user(db_session, "tb_re_second")
    service = TaskBoardService(db_session)
    task = service.create_task(actor=admin, title="改派任务", assignee_id=first.id)

    moved = service.update_task(actor=admin, task_id=task["id"], patch={"assignee_id": second.id})
    assert moved["assignee_id"] == second.id
    assert moved["assigner_id"] == admin.id

    assign_logs = (
        db_session.query(AuditLog)
        .filter(AuditLog.action == "task_board.assign", AuditLog.target_id == str(task["id"]))
        .order_by(AuditLog.id.asc())
        .all()
    )
    # initial assign + re-assign delta
    assert len(assign_logs) == 2
    assert assign_logs[1].detail and "->" in assign_logs[1].detail


def test_team_candidates_only_lists_team_members(db_session):
    admin = _create_user(db_session, "tb_team_admin", role=UserRole.ADMIN)
    in_team = _create_user(db_session, "tb_team_in", is_team=True)
    out_of_team = _create_user(db_session, "tb_team_out", is_team=False)
    service = TaskBoardService(db_session)
    candidates = {u["username"] for u in service.list_team_candidates()}
    assert in_team.username in candidates
    assert out_of_team.username not in candidates
    # admin is also a team member by default
    assert admin.username in candidates
