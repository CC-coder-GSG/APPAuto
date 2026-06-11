"""终端设备状态机 / 排他锁 / 流票据测试。"""
from __future__ import annotations

import pytest

from app.models import (
    TerminalDevice,
    TerminalDeviceStatus,
    TerminalLockType,
    User,
    UserRole,
)
from app.services import device_stream_service
from app.services.device_lock_service import DeviceConflict, DeviceLockService


def _make_user(db, username: str, role: UserRole = UserRole.USER) -> User:
    user = User(username=username, password_hash="x", role=role)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _make_device(db, serial: str = "ABC123", status: str = TerminalDeviceStatus.IDLE.value) -> TerminalDevice:
    device = TerminalDevice(serial=serial, name=serial, status=status)
    db.add(device)
    db.commit()
    db.refresh(device)
    return device


def test_acquire_manual_on_idle(db_session):
    user = _make_user(db_session, "alice")
    device = _make_device(db_session)
    svc = DeviceLockService(db_session)

    result = svc.acquire_manual(device.id, user)

    assert result["status"] == TerminalDeviceStatus.MANUAL.value
    assert result["lock"]["holder_user_id"] == user.id
    assert result["can_control"] is False  # manual != idle


def test_acquire_blocked_when_other_holds(db_session):
    alice = _make_user(db_session, "alice")
    bob = _make_user(db_session, "bob")
    device = _make_device(db_session)
    svc = DeviceLockService(db_session)
    svc.acquire_manual(device.id, alice)

    with pytest.raises(DeviceConflict) as ei:
        svc.acquire_manual(device.id, bob)
    assert ei.value.status_code == 409
    assert ei.value.detail["reason"] == "busy"


def test_acquire_idempotent_for_same_holder(db_session):
    alice = _make_user(db_session, "alice")
    device = _make_device(db_session)
    svc = DeviceLockService(db_session)
    first = svc.acquire_manual(device.id, alice)
    second = svc.acquire_manual(device.id, alice)
    assert first["lock"]["holder_user_id"] == second["lock"]["holder_user_id"]
    # 仍只有一把活动锁
    assert svc.active_lock(device.id) is not None


def test_acquire_blocked_when_offline(db_session):
    user = _make_user(db_session, "alice")
    device = _make_device(db_session, status=TerminalDeviceStatus.OFFLINE.value)
    svc = DeviceLockService(db_session)
    with pytest.raises(DeviceConflict) as ei:
        svc.acquire_manual(device.id, user)
    assert ei.value.detail["reason"] == "offline"


def test_release_returns_to_idle(db_session):
    user = _make_user(db_session, "alice")
    device = _make_device(db_session)
    svc = DeviceLockService(db_session)
    svc.acquire_manual(device.id, user)
    result = svc.release_manual(device.id, user)
    assert result["status"] == TerminalDeviceStatus.IDLE.value
    assert svc.active_lock(device.id) is None


def test_automation_lock_blocks_manual(db_session):
    user = _make_user(db_session, "alice")
    device = _make_device(db_session)
    svc = DeviceLockService(db_session)
    svc.automation_lock(device.serial, jenkins_job="s40311", jenkins_build="6")

    db_session.refresh(device)
    assert device.status == TerminalDeviceStatus.AUTOMATION.value

    with pytest.raises(DeviceConflict) as ei:
        svc.acquire_manual(device.id, user)
    assert ei.value.detail["reason"] == "automation"
    assert ei.value.detail["preemptable"] is True


def test_automation_unlock_releases(db_session):
    device = _make_device(db_session)
    svc = DeviceLockService(db_session)
    svc.automation_lock(device.serial, jenkins_job="s40311", jenkins_build="6")
    svc.automation_unlock(device.serial, jenkins_build="6")
    db_session.refresh(device)
    assert device.status == TerminalDeviceStatus.IDLE.value
    assert svc.active_lock(device.id) is None


def test_automation_lock_auto_registers_unknown_device(db_session):
    svc = DeviceLockService(db_session)
    res = svc.automation_lock("NEWSERIAL", jenkins_job="job", jenkins_build="1")
    assert res["ok"] is True
    device = db_session.query(TerminalDevice).filter(TerminalDevice.serial == "NEWSERIAL").first()
    assert device is not None
    assert device.status == TerminalDeviceStatus.AUTOMATION.value


def test_manual_lock_blocks_automation(db_session):
    user = _make_user(db_session, "alice")
    device = _make_device(db_session)
    svc = DeviceLockService(db_session)
    svc.acquire_manual(device.id, user)
    with pytest.raises(DeviceConflict) as ei:
        svc.automation_lock(device.serial, jenkins_job="job", jenkins_build="1")
    assert ei.value.detail["reason"] == "manual"


def test_preempt_automation(db_session):
    admin = _make_user(db_session, "admin1", role=UserRole.ADMIN)
    device = _make_device(db_session)
    svc = DeviceLockService(db_session)
    svc.automation_lock(device.serial, jenkins_job="job", jenkins_build="1")
    result = svc.preempt(device.id, admin)
    assert result["status"] == TerminalDeviceStatus.MANUAL.value
    assert result["lock"]["holder_user_id"] == admin.id


def test_control_ticket_requires_lock(db_session):
    alice = _make_user(db_session, "alice")
    bob = _make_user(db_session, "bob")
    device = _make_device(db_session)
    svc = DeviceLockService(db_session)
    svc.acquire_manual(device.id, alice)

    # 持有者可领 control 票据
    ticket = device_stream_service.issue_ticket(db_session, device_id=device.id, user=alice, mode="control")
    assert ticket["mode"] == "control"
    assert ticket["ws_url"].startswith(f"/api/terminals/{device.id}/ws?ticket=")

    # 非持有者领 control 票据被拒
    with pytest.raises(DeviceConflict) as ei:
        device_stream_service.issue_ticket(db_session, device_id=device.id, user=bob, mode="control")
    assert ei.value.status_code == 403


def test_view_ticket_open_to_anyone(db_session):
    alice = _make_user(db_session, "alice")
    bob = _make_user(db_session, "bob")
    device = _make_device(db_session)
    DeviceLockService(db_session).acquire_manual(device.id, alice)
    # 即使 alice 持锁，bob 也能领 view 票据观看
    ticket = device_stream_service.issue_ticket(db_session, device_id=device.id, user=bob, mode="view")
    assert ticket["mode"] == "view"


def test_ticket_single_use(db_session):
    alice = _make_user(db_session, "alice")
    device = _make_device(db_session)
    ticket = device_stream_service.issue_ticket(db_session, device_id=device.id, user=alice, mode="view")
    validated = device_stream_service.validate_ticket(db_session, token=ticket["token"], device_id=device.id)
    assert validated.mode == "view"
    # 二次使用应失败
    with pytest.raises(DeviceConflict):
        device_stream_service.validate_ticket(db_session, token=ticket["token"], device_id=device.id)


def test_heal_orphan_manual_status(db_session):
    """status=manual 但无活动锁的脏数据，应被自愈回 idle（list_devices / force-release 均可）。"""
    admin = _make_user(db_session, "admin1", role=UserRole.ADMIN)
    device = _make_device(db_session, status=TerminalDeviceStatus.MANUAL.value)  # 故意造脏：无锁却 manual
    svc = DeviceLockService(db_session)
    assert svc.active_lock(device.id) is None

    # list_devices 自愈
    rows = svc.list_devices()
    assert rows[0]["status"] == TerminalDeviceStatus.IDLE.value
    db_session.refresh(device)
    assert device.status == TerminalDeviceStatus.IDLE.value


def test_force_release_heals_orphan_status(db_session):
    admin = _make_user(db_session, "admin1", role=UserRole.ADMIN)
    device = _make_device(db_session, status=TerminalDeviceStatus.MANUAL.value)
    svc = DeviceLockService(db_session)
    result = svc.release_manual(device.id, admin, force=True)
    assert result["status"] == TerminalDeviceStatus.IDLE.value


def test_sync_online_marks_offline(db_session):
    device = _make_device(db_session, status=TerminalDeviceStatus.IDLE.value)
    svc = DeviceLockService(db_session)
    svc.sync_online(set())  # 没有任何设备在线
    db_session.refresh(device)
    assert device.status == TerminalDeviceStatus.OFFLINE.value

    svc.sync_online({device.serial})
    db_session.refresh(device)
    assert device.status == TerminalDeviceStatus.IDLE.value
