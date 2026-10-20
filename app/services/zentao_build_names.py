from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import Version, VersionType


def local_build_names(db: Session) -> dict[str, str]:
    """Use the same local minor-version names shown for discovery versions."""
    rows = db.query(Version.zentao_build_id, Version.version_no, Version.zentao_build_name_cache).filter(
        Version.version_type == VersionType.MINOR, Version.zentao_build_id.isnot(None),
    ).all()
    names = {}
    for build_id, version_no, cached_name in rows:
        name = str(version_no or cached_name or "").strip()
        if name and not name.isdecimal():
            names[str(build_id)] = name
    return names


def readable_build_names(value: str, names: dict[str, str]) -> str:
    parts = [part.strip() for part in value.split(',') if part.strip()]
    return ", ".join(dict.fromkeys(
        names.get(str(int(part)), "版本名称待获取") if part.isdecimal() else part
        for part in parts
    ))
