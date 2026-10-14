"""Validate a whole assignment batch before any local or remote side effects."""
from __future__ import annotations

import re
from datetime import date

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models import Requirement, User, Version, VersionType


def validate_task_dates(start: str | None, end: str | None, *, required: bool = True) -> tuple[date | None, date | None]:
    if not required and start is None and end is None:
        return None, None
    if not start or not end:
        raise HTTPException(400, "请设置完整的禅道任务预计开始和预计结束日期")
    try:
        if not all(re.fullmatch(r"\d{4}-\d{2}-\d{2}", v) for v in (start, end)):
            raise ValueError
        first, last = date.fromisoformat(start), date.fromisoformat(end)
    except (ValueError, TypeError):
        raise HTTPException(400, "禅道任务日期非法，请使用有效的 YYYY-MM-DD 日期") from None
    if first > last:
        raise HTTPException(400, "预计开始日期不能晚于预计结束日期")
    return first, last


def validate_selection(db: Session, major_id: int, requirement_ids: list[int]) -> tuple[Version, list[Requirement]]:
    major = db.get(Version, major_id)
    if not major or major.version_type != VersionType.MAJOR:
        raise HTTPException(400, "请选择有效的大版本")
    ids = list(dict.fromkeys(requirement_ids))
    if not ids:
        raise HTTPException(400, "请至少勾选一个需求")
    rows = db.query(Requirement).filter(Requirement.id.in_(ids), Requirement.major_version_id == major_id).all()
    by_id = {r.id: r for r in rows}
    invalid = [rid for rid in ids if rid not in by_id]
    if invalid:
        raise HTTPException(400, f"需求不存在或不属于所选大版本：{', '.join(map(str, invalid))}")
    return major, [by_id[rid] for rid in ids]


def validate_assignments(db: Session, major_id: int, assignments: list[dict]) -> list[dict]:
    validate_selection(db, major_id, [a["requirement_id"] for a in assignments])
    by_id: dict[int, dict] = {}
    for item in assignments:
        rid = item["requirement_id"]
        if rid in by_id and by_id[rid].get("owner_id") != item.get("owner_id"):
            raise HTTPException(400, f"需求 #{rid} 包含冲突的负责人设置")
        by_id[rid] = item
    owner_ids = {a["owner_id"] for a in by_id.values() if a.get("owner_id") is not None}
    known = {u.id for u in db.query(User).filter(User.id.in_(owner_ids)).all()} if owner_ids else set()
    if owner_ids - known:
        raise HTTPException(400, "所选负责人账号不存在，请重新加载后选择")
    return list(by_id.values())
