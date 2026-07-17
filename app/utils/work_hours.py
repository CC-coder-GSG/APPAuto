"""工作日 / 工时计算（禅道任务联动，2026-06-29 需求）。

两套口径（需求明确接受两者差异）：
  - 预计工时 estimate：工作日数 × 8h（用于分配时估算）。
  - 实际工时 consumed：开始→完成之间，按每日工作时段窗口累加，单日上限 7.833h。

工作时段窗口（公司规定）：
    上午 09:00–11:50（170 分钟）
    下午 13:30–18:30（300 分钟）
    合计 470 分钟 = 7.8333… 小时/天

本模块为纯函数、不依赖 DB：节假日信息由调用方以 ``holiday_map`` 注入
（``{date: is_off}``，is_off=True 放假 / False 调休补班）。未在 map 中的日期
按"周末休、工作日上班"默认规则处理。所有入参均为上海本地 naive datetime/date。
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Mapping, Optional

# (start, end) 工作时段
WORK_WINDOWS: list[tuple[time, time]] = [
    (time(9, 0), time(11, 50)),
    (time(13, 30), time(18, 30)),
]

# 每日工作分钟数（窗口口径）
WORK_MINUTES_PER_DAY = sum(
    (e.hour * 60 + e.minute) - (s.hour * 60 + s.minute) for s, e in WORK_WINDOWS
)  # = 470
WORK_HOURS_PER_DAY = WORK_MINUTES_PER_DAY / 60.0  # ≈ 7.8333

# 预计工时口径：每个工作日按 8 小时算
ESTIMATE_HOURS_PER_DAY = 8.0


def is_workday(day: date, holiday_map: Optional[Mapping[date, bool]] = None) -> bool:
    """某日是否为工作日。

    holiday_map[day] == True  → 放假（即使工作日也休）
    holiday_map[day] == False → 调休补班（即使周末也上班）
    不在 map 中 → 周一~周五为工作日，周六日休息。
    """
    holiday_map = holiday_map or {}
    if day in holiday_map:
        return not holiday_map[day]
    return day.weekday() < 5


def working_days(
    start_day: date,
    end_day: date,
    holiday_map: Optional[Mapping[date, bool]] = None,
) -> int:
    """[start_day, end_day] 闭区间内的工作日数（含两端）。

    end_day < start_day 时返回 0。
    """
    if end_day < start_day:
        return 0
    count = 0
    cur = start_day
    while cur <= end_day:
        if is_workday(cur, holiday_map):
            count += 1
        cur += timedelta(days=1)
    return count


def estimate_hours(
    start_day: date,
    deadline: date,
    holiday_map: Optional[Mapping[date, bool]] = None,
) -> float:
    """预计工时 = 工作日数 × 8h。至少 1 个工作日（避免 0）。"""
    days = working_days(start_day, deadline, holiday_map)
    if days <= 0:
        # 起止落在同一非工作日或区间非法时，兜底按 1 天算
        days = 1
    return round(days * ESTIMATE_HOURS_PER_DAY, 2)


def _overlap_minutes_in_day(day: date, start: datetime, end: datetime) -> int:
    """计算 [start, end] 与某一天工作时段窗口的重叠分钟数。"""
    total = 0
    for ws, we in WORK_WINDOWS:
        win_start = datetime.combine(day, ws)
        win_end = datetime.combine(day, we)
        lo = max(start, win_start)
        hi = min(end, win_end)
        if hi > lo:
            total += int((hi - lo).total_seconds() // 60)
    return total


def consumed_hours(
    started_at: datetime,
    finished_at: datetime,
    holiday_map: Optional[Mapping[date, bool]] = None,
) -> float:
    """实际工时：[started_at, finished_at] 落在工作日工作时段内的分钟累加。

    跨午休、跨天、跨周末/节假日均正确处理。finished<=started 返回 0。
    """
    if finished_at <= started_at:
        return 0.0
    total_minutes = 0
    cur_day = started_at.date()
    last_day = finished_at.date()
    while cur_day <= last_day:
        if is_workday(cur_day, holiday_map):
            total_minutes += _overlap_minutes_in_day(cur_day, started_at, finished_at)
        cur_day += timedelta(days=1)
    return round(total_minutes / 60.0, 2)


def consumed_hours_by_day(
    started_at: datetime,
    finished_at: datetime,
    holiday_map: Optional[Mapping[date, bool]] = None,
) -> list[tuple[date, float]]:
    """实际工时按天拆分：[(日期, 当日小时数)]，只含小时数>0 的工作日。

    口径与 consumed_hours 一致（同一段区间两者合计相等，均按分钟取整后除 60、
    保留 2 位小数）。用于分段提交禅道工时记录时把跨天时段落到实际发生的日期上。
    """
    if finished_at <= started_at:
        return []
    out: list[tuple[date, float]] = []
    cur_day = started_at.date()
    last_day = finished_at.date()
    while cur_day <= last_day:
        if is_workday(cur_day, holiday_map):
            minutes = _overlap_minutes_in_day(cur_day, started_at, finished_at)
            if minutes > 0:
                out.append((cur_day, round(minutes / 60.0, 2)))
        cur_day += timedelta(days=1)
    return out


__all__ = [
    "WORK_WINDOWS",
    "WORK_HOURS_PER_DAY",
    "ESTIMATE_HOURS_PER_DAY",
    "is_workday",
    "working_days",
    "estimate_hours",
    "consumed_hours",
    "consumed_hours_by_day",
]
