"""工作日 / 工时计算（禅道任务联动，2026-06-29 需求）。

两套口径（需求明确接受两者差异）：
  - 预计工时 estimate：工作日数 × 8h（用于分配时估算）。
  - 实际工时 consumed：开始→完成之间的自然流逝时间，仅按「工作日」过滤——
    周末与节假日整天跳过，工作日内不再限制时段（2026-07-17 需求：原
    09:00-11:50 / 13:30-18:30 工作时段窗口会漏掉加班时间，已移除）。

⚠️ 移除时段窗口的直接后果：跨天不暂停的段会把夜间也计入（一个工作日最多
计 24h）。暂停期不计工时的口径不变，靠及时点暂停控制。

本模块为纯函数、不依赖 DB：节假日信息由调用方以 ``holiday_map`` 注入
（``{date: is_off}``，is_off=True 放假 / False 调休补班）。未在 map 中的日期
按"周末休、工作日上班"默认规则处理。所有入参均为上海本地 naive datetime/date。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Mapping, Optional

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
    """计算 [start, end] 与某一自然日（00:00 → 次日 00:00）的重叠分钟数。"""
    day_start = datetime.combine(day, datetime.min.time())
    day_end = day_start + timedelta(days=1)
    lo = max(start, day_start)
    hi = min(end, day_end)
    if hi <= lo:
        return 0
    return int((hi - lo).total_seconds() // 60)


def consumed_hours(
    started_at: datetime,
    finished_at: datetime,
    holiday_map: Optional[Mapping[date, bool]] = None,
) -> float:
    """实际工时：[started_at, finished_at] 落在工作日内的自然分钟累加。

    周末/节假日整天跳过；工作日内不限时段（加班照计）。跨天正确处理。
    finished<=started 返回 0。
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
    "ESTIMATE_HOURS_PER_DAY",
    "is_workday",
    "working_days",
    "estimate_hours",
    "consumed_hours",
    "consumed_hours_by_day",
]
