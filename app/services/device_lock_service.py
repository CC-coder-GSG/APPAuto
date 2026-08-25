"""
终端设备状态机 / 排他锁服务（权威来源）。

负责「能否远程操作」的唯一裁决：
- 自动化（Jenkins/Appium）占用期间，只允许只读观看，禁止人工操作；
- 空闲时可申请人工操作权（manual 锁）；
- 特权用户可抢占自动化（中断测试并接管）。

状态由 active lock + 在线与否推导，写回 TerminalDevice.status，并通过 SSE 广播。
锁的释放：用户主动释放 / 心跳超时 / Jenkins unlock / 兜底 TTL / 抢占 / 强制。
"""
from __future__ import annotations

import logging
from datetime import timedelta

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import (
    TerminalDevice,
    TerminalDeviceLock,
    TerminalDeviceStatus,
    TerminalLockHolderKind,
    TerminalLockReleaseReason,
    TerminalLockType,
    User,
)
from app.services.sse_service import sse_publish
from app.utils.time_utils import local_now

logger = logging.getLogger(__name__)


class DeviceConflict(Exception):
    """设备占用冲突 / 非法状态转移。携带 HTTP status 与结构化 detail。"""

    def __init__(self, status_code: int, detail):
        self.status_code = status_code
        self.detail = detail
        super().__init__(str(detail))


class DeviceLockService:
    def __init__(self, db: Session):
        self.db = db

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------

    def get_device(self, device_id: int) -> TerminalDevice | None:
        return self.db.query(TerminalDevice).filter(TerminalDevice.id == device_id).first()

    def get_device_or_404(self, device_id: int) -> TerminalDevice:
        device = self.get_device(device_id)
        if not device:
            raise DeviceConflict(404, "设备不存在")
        return device

    def active_lock(self, device_id: int) -> TerminalDeviceLock | None:
        return (
            self.db.query(TerminalDeviceLock)
            .filter(
                TerminalDeviceLock.device_id == device_id,
                TerminalDeviceLock.released_at.is_(None),
            )
            .first()
        )

    def list_devices(self) -> list[dict]:
        self.expire_stale()
        devices = (
            self.db.query(TerminalDevice)
            .order_by(TerminalDevice.enabled.desc(), TerminalDevice.name.asc())
            .all()
        )
        # 自愈：纠正 status 与真实锁不一致的脏数据（如 status=manual 但无活动锁）
        changed = False
        for d in devices:
            if self._heal_status(d):
                changed = True
        if changed:
            self.db.commit()
        return [self.serialize_device(d) for d in devices]

    def _heal_status(self, device: TerminalDevice) -> bool:
        """按当前活动锁纠正 device.status。返回是否发生变化。

        offline 状态保留（需 adb 扫描才能确认在线）；其余按锁推导：
        有 automation 锁→automation，有 manual 锁→manual，无锁→idle。
        修复"状态卡在 manual/automation 但底下没锁"导致永远释放不掉的脏数据。
        """
        active = self.active_lock(device.id)
        if active and active.lock_type == TerminalLockType.AUTOMATION.value:
            target = TerminalDeviceStatus.AUTOMATION.value
        elif active and active.lock_type == TerminalLockType.MANUAL.value:
            target = TerminalDeviceStatus.MANUAL.value
        elif device.status == TerminalDeviceStatus.OFFLINE.value:
            target = TerminalDeviceStatus.OFFLINE.value
        else:
            target = TerminalDeviceStatus.IDLE.value
        if device.status != target:
            device.status = target
            self._publish(device, "device.status_changed")
            return True
        return False

    def serialize_device(self, device: TerminalDevice) -> dict:
        lock = self.active_lock(device.id)
        holder_name = None
        if lock and lock.holder_user_id:
            holder = self.db.query(User).filter(User.id == lock.holder_user_id).first()
            if holder:
                holder_name = getattr(holder, "display_name", None) or holder.username
        return {
            "id": device.id,
            "serial": device.serial,
            "name": device.name,
            "platform": device.platform,
            "status": device.status,
            "connection": device.connection,
            "model": device.model,
            "os_version": device.os_version,
            "enabled": device.enabled,
            "last_seen_at": device.last_seen_at.isoformat() if device.last_seen_at else None,
            "can_control": device.status == TerminalDeviceStatus.IDLE.value,
            "lock": (
                {
                    "type": lock.lock_type,
                    "holder_kind": lock.holder_kind,
                    "holder_user_id": lock.holder_user_id,
                    "holder_name": holder_name,
                    "jenkins_job": lock.jenkins_job,
                    "jenkins_build": lock.jenkins_build,
                    "acquired_at": lock.acquired_at.isoformat() if lock.acquired_at else None,
                }
                if lock
                else None
            ),
        }

    # ------------------------------------------------------------------
    # 状态推导
    # ------------------------------------------------------------------

    def _apply_status(self, device: TerminalDevice, active_lock: TerminalDeviceLock | None, *, online: bool | None = None) -> None:
        if online is None:
            online = device.status != TerminalDeviceStatus.OFFLINE.value or active_lock is not None
        if not online:
            new_status = TerminalDeviceStatus.OFFLINE.value
        elif active_lock and active_lock.lock_type == TerminalLockType.AUTOMATION.value:
            new_status = TerminalDeviceStatus.AUTOMATION.value
        elif active_lock and active_lock.lock_type == TerminalLockType.MANUAL.value:
            new_status = TerminalDeviceStatus.MANUAL.value
        else:
            new_status = TerminalDeviceStatus.IDLE.value

        if device.status != new_status:
            device.status = new_status
            self._publish(device, "device.status_changed")

    def _publish(self, device: TerminalDevice, event: str) -> None:
        try:
            sse_publish(
                event,
                {"device_id": device.id, "serial": device.serial, "status": device.status},
                channels=["global"],
            )
        except Exception:  # pragma: no cover - SSE 不应阻塞业务
            logger.debug("device SSE publish failed", exc_info=True)

    def sync_online(self, online_serials: set[str]) -> None:
        """供注册表扫描后调用：按在线集合重算每台设备状态。"""
        for device in self.db.query(TerminalDevice).all():
            online = device.serial in online_serials
            self._apply_status(device, self.active_lock(device.id), online=online)
        self.db.commit()

    # ------------------------------------------------------------------
    # 人工操作锁
    # ------------------------------------------------------------------

    def acquire_manual(self, device_id: int, user: User) -> dict:
        device = self.get_device_or_404(device_id)
        active = self.active_lock(device_id)
        if active:
            if active.lock_type == TerminalLockType.AUTOMATION.value:
                raise DeviceConflict(409, {
                    "reason": "automation",
                    "message": "该终端正在跑自动化测试，仅可观看",
                    "preemptable": True,
                    "jenkins_build": active.jenkins_build,
                })
            # manual lock
            if active.holder_user_id == user.id:
                active.heartbeat_at = local_now()
                self.db.commit()
                return self.serialize_device(device)
            holder = self.db.query(User).filter(User.id == active.holder_user_id).first()
            raise DeviceConflict(409, {
                "reason": "busy",
                "message": "该终端正被他人操作",
                "holder": (getattr(holder, "display_name", None) or holder.username) if holder else None,
            })
        if device.status == TerminalDeviceStatus.OFFLINE.value:
            raise DeviceConflict(409, {"reason": "offline", "message": "终端离线，无法操作"})

        lock = TerminalDeviceLock(
            device_id=device.id,
            lock_type=TerminalLockType.MANUAL.value,
            holder_kind=TerminalLockHolderKind.USER.value,
            holder_user_id=user.id,
        )
        self.db.add(lock)
        self.db.flush()
        self._apply_status(device, lock, online=True)
        self._publish(device, "device.lock_changed")
        self.db.commit()
        return self.serialize_device(device)

    def heartbeat_manual(self, device_id: int, user: User) -> dict:
        device = self.get_device_or_404(device_id)
        active = self.active_lock(device_id)
        if not active or active.lock_type != TerminalLockType.MANUAL.value or active.holder_user_id != user.id:
            raise DeviceConflict(409, {"reason": "not_holder", "message": "你未持有该终端的操作权"})
        active.heartbeat_at = local_now()
        self.db.commit()
        return {"ok": True}

    def release_manual(self, device_id: int, user: User, *, force: bool = False) -> dict:
        device = self.get_device_or_404(device_id)
        active = self.active_lock(device_id)
        if active and active.lock_type == TerminalLockType.MANUAL.value:
            if not force and active.holder_user_id != user.id:
                raise DeviceConflict(403, {"reason": "not_holder", "message": "只能释放自己持有的操作权"})
            self._release_lock(active, TerminalLockReleaseReason.FORCED if force else TerminalLockReleaseReason.NORMAL)
        # flush 让刚释放的 released_at 落库，确保 _heal_status 重查 active_lock 时拿到 None
        # （autoflush=False 时不 flush 会查到旧值）。
        self.db.flush()
        # 即便没有活动锁（status 与锁不一致的脏数据），也按真实锁状态重算 status，
        # 让 force-release 能修复"卡在 manual 但无锁"的设备。
        self._heal_status(device)
        self._publish(device, "device.lock_changed")
        self.db.commit()
        return self.serialize_device(device)

    def preempt(self, device_id: int, user: User) -> dict:
        """抢占自动化锁：中断自动化占用并接管为人工操作（调用方需校验特权）。"""
        device = self.get_device_or_404(device_id)
        active = self.active_lock(device_id)
        if not active:
            # 无锁直接当作 acquire
            return self.acquire_manual(device_id, user)
        if active.lock_type != TerminalLockType.AUTOMATION.value:
            raise DeviceConflict(409, {"reason": "not_automation", "message": "当前不是自动化占用，无需抢占"})
        self._release_lock(active, TerminalLockReleaseReason.PREEMPTED)
        lock = TerminalDeviceLock(
            device_id=device.id,
            lock_type=TerminalLockType.MANUAL.value,
            holder_kind=TerminalLockHolderKind.USER.value,
            holder_user_id=user.id,
        )
        self.db.add(lock)
        self.db.flush()
        self._apply_status(device, lock, online=True)
        self._publish(device, "device.lock_changed")
        self.db.commit()
        logger.warning("终端 %s 的自动化占用被用户 %s 抢占（build=%s）", device.serial, user.id, active.jenkins_build)
        return self.serialize_device(device)

    # ------------------------------------------------------------------
    # 自动化锁（Jenkins / 自动化侧回调）
    # ------------------------------------------------------------------

    def automation_lock(self, serial: str, *, jenkins_job: str | None, jenkins_build: str | None) -> dict:
        device = self._get_or_create_device(serial)
        active = self.active_lock(device.id)
        if active:
            if active.lock_type == TerminalLockType.AUTOMATION.value:
                # 同一 build 幂等；不同 build 也续用现有自动化锁（更新 build/job）
                active.jenkins_job = jenkins_job or active.jenkins_job
                active.jenkins_build = jenkins_build or active.jenkins_build
                active.heartbeat_at = local_now()
                self.db.commit()
                return {"ok": True, "status": device.status, "note": "already_locked"}
            # 被人工占用 → 让流水线自行决定等待/跳过
            raise DeviceConflict(409, {"reason": "manual", "message": "终端正被人工操作，自动化无法占用"})

        lock = TerminalDeviceLock(
            device_id=device.id,
            lock_type=TerminalLockType.AUTOMATION.value,
            holder_kind=TerminalLockHolderKind.JENKINS.value,
            jenkins_job=jenkins_job,
            jenkins_build=jenkins_build,
        )
        self.db.add(lock)
        self.db.flush()
        self._apply_status(device, lock, online=True)
        self._publish(device, "device.lock_changed")
        self.db.commit()
        return {"ok": True, "status": device.status}

    def automation_unlock(self, serial: str, *, jenkins_build: str | None = None) -> dict:
        device = self.db.query(TerminalDevice).filter(TerminalDevice.serial == serial).first()
        if not device:
            return {"ok": True, "note": "device_unknown"}
        active = self.active_lock(device.id)
        if not active or active.lock_type != TerminalLockType.AUTOMATION.value:
            return {"ok": True, "note": "not_locked"}
        if jenkins_build and active.jenkins_build and str(jenkins_build) != str(active.jenkins_build):
            # build 不匹配：可能是上一轮残留，仍按请求释放当前自动化锁
            logger.info("automation_unlock build 不匹配 serial=%s req=%s cur=%s", serial, jenkins_build, active.jenkins_build)
        self._release_lock(active, TerminalLockReleaseReason.NORMAL)
        self._apply_status(device, None, online=True)
        self._publish(device, "device.lock_changed")
        self.db.commit()
        return {"ok": True, "status": device.status}

    def automation_heartbeat(self, serial: str) -> dict:
        device = self.db.query(TerminalDevice).filter(TerminalDevice.serial == serial).first()
        if not device:
            return {"ok": True, "note": "device_unknown"}
        active = self.active_lock(device.id)
        if active and active.lock_type == TerminalLockType.AUTOMATION.value:
            active.heartbeat_at = local_now()
            self.db.commit()
        return {"ok": True}

    # ------------------------------------------------------------------
    # 超时清理
    # ------------------------------------------------------------------

    def expire_stale(self) -> int:
        now = local_now()
        manual_deadline = now - timedelta(seconds=settings.terminal_manual_lock_ttl_seconds)
        auto_deadline = now - timedelta(seconds=settings.terminal_automation_lock_ttl_seconds)
        released = 0
        actives = (
            self.db.query(TerminalDeviceLock)
            .filter(TerminalDeviceLock.released_at.is_(None))
            .all()
        )
        touched: set[int] = set()
        for lock in actives:
            stale = (
                lock.lock_type == TerminalLockType.MANUAL.value and lock.heartbeat_at < manual_deadline
            ) or (
                lock.lock_type == TerminalLockType.AUTOMATION.value and lock.acquired_at < auto_deadline
            )
            if stale:
                self._release_lock(lock, TerminalLockReleaseReason.TIMEOUT)
                touched.add(lock.device_id)
                released += 1
        if released:
            for device in self.db.query(TerminalDevice).filter(TerminalDevice.id.in_(touched)).all():
                self._apply_status(device, self.active_lock(device.id), online=True)
            self.db.commit()
        return released

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def _release_lock(self, lock: TerminalDeviceLock, reason: TerminalLockReleaseReason) -> None:
        lock.released_at = local_now()
        lock.release_reason = reason.value

    def _get_or_create_device(self, serial: str) -> TerminalDevice:
        serial = (serial or "").strip()
        if not serial:
            raise DeviceConflict(400, "serial 不能为空")
        device = self.db.query(TerminalDevice).filter(TerminalDevice.serial == serial).first()
        if device:
            return device
        device = TerminalDevice(
            serial=serial,
            name=serial,
            status=TerminalDeviceStatus.IDLE.value,
            last_seen_at=local_now(),
        )
        self.db.add(device)
        self.db.flush()
        return device


__all__ = ["DeviceLockService", "DeviceConflict"]
