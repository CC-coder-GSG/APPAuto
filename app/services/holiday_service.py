"""中国法定节假日服务（禅道任务联动，2026-06-29 需求）。

数据来源：公开节假日 JSON（holiday-cn，NateScarlet 维护，raw.githubusercontent）。
拉取结果落 ``holidays`` 表缓存；后续工时/工作日计算从缓存读，**不每次联网**。
API 不可达时回退已有缓存；缓存也没有则由 work_hours 按"仅跳周末"默认规则处理。

holidays.is_off：True=放假，False=调休补班。
"""
from __future__ import annotations

import logging
import time
from datetime import date, datetime
from typing import Iterable

import httpx
from sqlalchemy.orm import Session

from app.models.holiday import Holiday

logger = logging.getLogger(__name__)

# 主源：holiday-cn（无鉴权、无限流、按年一个 JSON）
_SOURCE_URL = "https://raw.githubusercontent.com/NateScarlet/holiday-cn/master/{year}.json"
# 备源：timor.tech
_FALLBACK_URL = "https://timor.tech/api/holiday/year/{year}"
_TIMEOUT = 6

# 负缓存：拉取失败（如内网无外网）后，在冷却期内不再重复联网，避免每次分配/完成任务
# 都阻塞两次 HTTP 超时。{year: 上次失败的单调时刻}
_FAILED_FETCH_AT: dict[int, float] = {}
_FAIL_COOLDOWN_SEC = 3600.0  # 1 小时内不重试同一年


def _parse_natescarlet(payload: dict) -> list[tuple[date, bool, str]]:
    out: list[tuple[date, bool, str]] = []
    for d in payload.get("days", []) or []:
        raw = d.get("date")
        if not raw:
            continue
        try:
            day = datetime.strptime(raw, "%Y-%m-%d").date()
        except ValueError:
            continue
        out.append((day, bool(d.get("isOffDay", True)), str(d.get("name") or "")))
    return out


def _parse_timor(payload: dict) -> list[tuple[date, bool, str]]:
    out: list[tuple[date, bool, str]] = []
    holiday = payload.get("holiday") or {}
    for _key, v in holiday.items():
        raw = v.get("date")
        if not raw:
            continue
        try:
            day = datetime.strptime(raw, "%Y-%m-%d").date()
        except ValueError:
            continue
        # timor: holiday=True 放假；False 补班
        out.append((day, bool(v.get("holiday", True)), str(v.get("name") or "")))
    return out


def _fetch_year(year: int) -> list[tuple[date, bool, str]] | None:
    """联网拉一年的节假日；失败返回 None。"""
    for url, parser in ((_SOURCE_URL, _parse_natescarlet), (_FALLBACK_URL, _parse_timor)):
        try:
            resp = httpx.get(url.format(year=year), timeout=_TIMEOUT, follow_redirects=True)
            if resp.status_code != 200:
                continue
            data = resp.json()
            rows = parser(data)
            if rows:
                return rows
        except Exception as exc:
            logger.warning("holiday fetch %s failed: %s", url.format(year=year), exc)
            continue
    return None


def refresh_year(db: Session, year: int, *, respect_cooldown: bool = False) -> int:
    """拉取并 upsert 某年节假日，返回写入条数；失败返回 0（保留旧缓存）。

    respect_cooldown=True 时，若该年最近联网失败且仍在冷却期内，直接跳过联网
    （供 ensure_years_cached 用，避免内网环境反复超时）。
    """
    if respect_cooldown:
        last_fail = _FAILED_FETCH_AT.get(year)
        if last_fail is not None and (time.monotonic() - last_fail) < _FAIL_COOLDOWN_SEC:
            return 0
    rows = _fetch_year(year)
    if not rows:
        logger.warning("holiday refresh_year(%s): no data, keeping cache", year)
        _FAILED_FETCH_AT[year] = time.monotonic()
        return 0
    _FAILED_FETCH_AT.pop(year, None)
    n = 0
    for day, is_off, name in rows:
        existing = db.query(Holiday).filter(Holiday.day == day).first()
        if existing:
            existing.is_off = is_off
            existing.name = name
            existing.year = year
        else:
            db.add(Holiday(day=day, is_off=is_off, name=name, year=year))
        n += 1
    db.commit()
    logger.info("holiday refresh_year(%s): upserted %s rows", year, n)
    return n


def ensure_years_cached(db: Session, years: Iterable[int]) -> None:
    """确保给定年份已有缓存；缺的年份联网补一次（失败静默回退）。"""
    for year in set(years):
        has = db.query(Holiday.day).filter(Holiday.year == year).first()
        if not has:
            refresh_year(db, year, respect_cooldown=True)


def get_holiday_map(db: Session, start_day: date, end_day: date) -> dict[date, bool]:
    """读取 [start_day, end_day] 覆盖年份的节假日映射 {date: is_off}。

    自动确保涉及的年份已缓存（缺则尝试联网补）。供 work_hours 使用。
    """
    if end_day < start_day:
        start_day, end_day = end_day, start_day
    years = range(start_day.year, end_day.year + 1)
    ensure_years_cached(db, years)
    rows = (
        db.query(Holiday)
        .filter(Holiday.day >= start_day, Holiday.day <= end_day)
        .all()
    )
    return {r.day: bool(r.is_off) for r in rows}


__all__ = ["refresh_year", "ensure_years_cached", "get_holiday_map"]
