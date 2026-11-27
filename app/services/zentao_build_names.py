from __future__ import annotations

import logging
import re
import threading
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy.orm import Session

from app.models import Version, VersionType


def local_build_names(db: Session) -> dict[str, str]:
    """Use the same local minor-version names shown for discovery versions."""
    rows = db.query(Version.zentao_build_id, Version.version_no, Version.zentao_build_name_cache).filter(
        Version.version_type == VersionType.MINOR, Version.zentao_build_id.isnot(None),
    ).all()
    names = {}
    for build_id, version_no, cached_name in rows:
        name = next((str(value).strip() for value in (version_no, cached_name)
                     if value and str(value).strip() and not str(value).strip().isdecimal()), "")
        if name:
            names[str(build_id)] = name
    return names


def readable_build_names(value: str, names: dict[str, str]) -> str:
    parts = [part.strip() for part in value.split(',') if part.strip()]
    return ", ".join(dict.fromkeys(
        names.get(str(int(part)), "版本名称待获取") if part.isdecimal() else part
        for part in parts
    ))


# ── Bug 预览：构建编号 → 构建版本名（本地小版本优先，取不到再查禅道构建页并缓存）──────
# 禅道 Bug 详情的 openedBuild / resolvedBuild 常只给数字构建 ID（如 5432）；解决版本经常是
# 别的执行的构建，本地没有对应小版本，这时读禅道构建页拿名称，去掉 "(64-bit)" 架构后缀后
# 与平台小版本号同格式（如 4.0.5.0.261123_alpha.34(40500035)）。"trunk" 是禅道「主干」。
_logger = logging.getLogger(__name__)
_ARCH_SUFFIX_RE = re.compile(r"\s*\((?:32|64)[- ]?bit\)\s*$", re.IGNORECASE)
_REMOTE_NAME_CACHE: dict[int, str] = {}
_CACHE_LIMIT = 2000
_CACHE_LOCK = threading.Lock()
_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="zt-build-name")


def clean_build_name(name: str) -> str:
    return _ARCH_SUFFIX_RE.sub("", str(name or "").strip())


def _local_names_for(db: Session, build_ids: list[int]) -> dict[int, str]:
    """与 local_build_names 同口径（版本号优先、其次构建名缓存），只查需要的几个构建。"""
    if not build_ids:
        return {}
    rows = (
        db.query(Version.zentao_build_id, Version.version_no, Version.zentao_build_name_cache)
        .filter(Version.version_type == VersionType.MINOR, Version.zentao_build_id.in_(build_ids))
        .order_by(Version.id.asc())
        .all()
    )
    names: dict[int, str] = {}
    for build_id, version_no, cached_name in rows:
        name = next((clean_build_name(value) for value in (version_no, cached_name)
                     if value and str(value).strip() and not str(value).strip().isdecimal()), "")
        if name and int(build_id) not in names:
            names[int(build_id)] = name
    return names


def _remote_name(client, build_id: int) -> str | None:
    try:
        build = client.get_build(build_id) or {}
    except Exception as exc:  # noqa: BLE001 — 单个构建取不到不影响预览
        _logger.info("zentao build name lookup failed build_id=%s err=%s", build_id, exc)
        return None
    name = clean_build_name(build.get("name") or build.get("title") or "")
    return name or None


def resolve_build_labels(db: Session, client, values: list[str]) -> list[str]:
    """把若干个「逗号分隔的构建引用」翻译成构建版本名，返回同顺序结果；取不到名称的保留编号。"""
    tokens = [[part.strip() for part in str(v or "").split(",") if part.strip()] for v in values]
    ids = sorted({int(t) for parts in tokens for t in parts if t.isdecimal()})
    names = _local_names_for(db, ids)
    missing: list[int] = []
    with _CACHE_LOCK:
        for build_id in ids:
            if build_id in names:
                continue
            if build_id in _REMOTE_NAME_CACHE:
                names[build_id] = _REMOTE_NAME_CACHE[build_id]
            else:
                missing.append(build_id)
    if missing and client is not None:
        futures = {build_id: _POOL.submit(_remote_name, client, build_id) for build_id in missing}
        for build_id, future in futures.items():
            try:
                name = future.result(timeout=8)
            except Exception:  # noqa: BLE001
                name = None
            if not name:
                continue
            names[build_id] = name
            with _CACHE_LOCK:
                if len(_REMOTE_NAME_CACHE) >= _CACHE_LIMIT:
                    _REMOTE_NAME_CACHE.clear()
                _REMOTE_NAME_CACHE[build_id] = name

    out = []
    for parts in tokens:
        labels = []
        for part in parts:
            if part.isdecimal():
                labels.append(names.get(int(part), part))
            elif part.lower() == "trunk":
                labels.append("主干")
            else:
                labels.append(clean_build_name(part))
        out.append(", ".join(dict.fromkeys(label for label in labels if label)))
    return out
