"""
终端注册表服务：扫描服务器上的 adb 设备并 upsert 到 TerminalDevice。

scan_adb_devices 调用 `adb devices -l`，解析在线 serial 与型号；
refresh 把扫描结果落库（新设备登记、刷新 last_seen），再交给 DeviceLockService
按在线集合重算状态。adb 不可用 / 无设备时安全降级，不抛 500。
"""
from __future__ import annotations

import logging
import re
import shutil
import subprocess
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import TerminalDevice, TerminalDeviceStatus
from app.services.device_lock_service import DeviceLockService
from app.utils.time_utils import local_now

logger = logging.getLogger(__name__)

_MODEL_RE = re.compile(r"model:(\S+)")


@dataclass
class AdbDeviceRow:
    serial: str
    state: str          # device / offline / unauthorized
    model: str | None = None
    connection: str = "usb"


def scan_adb_devices(adb_path: str | None = None, *, timeout: float = 10.0) -> list[AdbDeviceRow]:
    """运行 `adb devices -l`，返回解析后的设备行。adb 不存在/失败时返回空列表。"""
    adb = adb_path or settings.adb_path
    if not shutil.which(adb) and adb == "adb":
        logger.info("adb 不在 PATH 上，跳过设备扫描")
        return []
    try:
        proc = subprocess.run(
            [adb, "devices", "-l"],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError:
        logger.info("adb 可执行文件未找到：%s", adb)
        return []
    except subprocess.TimeoutExpired:
        logger.warning("adb devices 超时")
        return []
    except Exception as exc:  # pragma: no cover - 环境相关
        logger.warning("adb devices 执行失败: %s", exc)
        return []

    rows: list[AdbDeviceRow] = []
    for line in (proc.stdout or "").splitlines():
        line = line.strip()
        if not line or line.startswith("List of devices"):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        serial, state = parts[0], parts[1]
        model_match = _MODEL_RE.search(line)
        model = model_match.group(1).replace("_", " ") if model_match else None
        connection = "tcp" if ":" in serial else "usb"
        rows.append(AdbDeviceRow(serial=serial, state=state, model=model, connection=connection))
    return rows


def refresh_devices(db: Session, adb_path: str | None = None) -> dict:
    """扫描 adb，upsert 设备，刷新在线状态。返回统计。"""
    rows = scan_adb_devices(adb_path)
    online_serials: set[str] = set()
    created = 0

    for row in rows:
        if row.state != "device":
            # offline / unauthorized 的设备不算在线，但仍登记以便显示
            pass
        device = db.query(TerminalDevice).filter(TerminalDevice.serial == row.serial).first()
        if not device:
            device = TerminalDevice(
                serial=row.serial,
                name=row.model or row.serial,
                status=TerminalDeviceStatus.OFFLINE.value,
                connection=row.connection,
                model=row.model,
            )
            db.add(device)
            created += 1
        else:
            if row.model and not device.model:
                device.model = row.model
            device.connection = row.connection
        if row.state == "device":
            device.last_seen_at = local_now()
            online_serials.add(row.serial)

    db.flush()
    DeviceLockService(db).sync_online(online_serials)

    return {
        "scanned": len(rows),
        "online": len(online_serials),
        "created": created,
    }


__all__ = ["scan_adb_devices", "refresh_devices", "AdbDeviceRow"]
