from __future__ import annotations

from datetime import date, datetime

from app.utils.work_hours import (
    WORK_HOURS_PER_DAY,
    consumed_hours,
    consumed_hours_by_day,
    estimate_hours,
    is_workday,
    working_days,
)


def test_daily_window_total_is_7_83():
    # 9:00-11:50 (170) + 13:30-18:30 (300) = 470 min = 7.8333h
    assert round(WORK_HOURS_PER_DAY, 2) == 7.83


def test_is_workday_weekend_vs_weekday():
    assert is_workday(date(2026, 6, 29)) is True   # Monday
    assert is_workday(date(2026, 6, 27)) is False  # Saturday
    assert is_workday(date(2026, 6, 28)) is False  # Sunday


def test_is_workday_holiday_overrides():
    # 工作日被标放假
    assert is_workday(date(2026, 6, 29), {date(2026, 6, 29): True}) is False
    # 周末调休补班
    assert is_workday(date(2026, 6, 27), {date(2026, 6, 27): False}) is True


def test_working_days_skips_weekend():
    # Mon 6/29 .. Fri 7/3 = 5 工作日；含周末 7/4,7/5 不算
    assert working_days(date(2026, 6, 29), date(2026, 7, 5)) == 5


def test_working_days_with_holiday():
    hmap = {date(2026, 7, 1): True}  # 7/1 放假
    assert working_days(date(2026, 6, 29), date(2026, 7, 3), hmap) == 4


def test_working_days_reversed_returns_zero():
    assert working_days(date(2026, 7, 3), date(2026, 6, 29)) == 0


def test_estimate_hours_5_days_x_8():
    assert estimate_hours(date(2026, 6, 29), date(2026, 7, 3)) == 40.0


def test_estimate_hours_min_one_day():
    # 起止同一个周六 → 兜底按 1 天
    assert estimate_hours(date(2026, 6, 27), date(2026, 6, 27)) == 8.0


def test_consumed_full_workday():
    s = datetime(2026, 6, 29, 9, 0, 0)
    e = datetime(2026, 6, 29, 18, 30, 0)
    # 全天工作时段 = 7.83h（中间午休 11:50-13:30 自动扣掉）
    assert consumed_hours(s, e) == 7.83


def test_consumed_skips_lunch():
    s = datetime(2026, 6, 29, 11, 0, 0)
    e = datetime(2026, 6, 29, 14, 0, 0)
    # 11:00-11:50 (50min) + 13:30-14:00 (30min) = 80min = 1.33h
    assert consumed_hours(s, e) == 1.33


def test_consumed_before_and_after_window_clamped():
    s = datetime(2026, 6, 29, 7, 0, 0)   # 上班前
    e = datetime(2026, 6, 29, 20, 0, 0)  # 下班后
    assert consumed_hours(s, e) == 7.83


def test_consumed_spans_weekend():
    # Fri 7/3 17:30 → Mon 7/6 9:30
    s = datetime(2026, 7, 3, 17, 30, 0)
    e = datetime(2026, 7, 6, 9, 30, 0)
    # Fri: 17:30-18:30 = 60min(1.0h); Sat/Sun skipped; Mon: 9:00-9:30 = 30min(0.5h)
    assert consumed_hours(s, e) == 1.5


def test_consumed_spans_holiday():
    hmap = {date(2026, 7, 1): True}
    s = datetime(2026, 6, 30, 18, 0, 0)  # Tue 18:00-18:30 = 30min
    e = datetime(2026, 7, 2, 9, 30, 0)   # Thu 9:00-9:30 = 30min; Wed(7/1) holiday skipped
    assert consumed_hours(s, e, hmap) == 1.0


def test_consumed_non_positive_range():
    s = datetime(2026, 6, 29, 10, 0, 0)
    assert consumed_hours(s, s) == 0.0
    assert consumed_hours(s, datetime(2026, 6, 29, 9, 0, 0)) == 0.0


# ─── 按天拆分（分段提交禅道工时记录用）─────────────────────────────────────


def test_consumed_by_day_cross_days():
    # Mon 15:00 → Wed 10:30：Mon 15:00-18:30=3.5h；Tue 全天 7.83h；Wed 9:00-10:30=1.5h
    s = datetime(2026, 6, 29, 15, 0, 0)
    e = datetime(2026, 7, 1, 10, 30, 0)
    rows = consumed_hours_by_day(s, e)
    assert rows == [
        (date(2026, 6, 29), 3.5),
        (date(2026, 6, 30), 7.83),
        (date(2026, 7, 1), 1.5),
    ]


def test_consumed_by_day_skips_weekend_and_zero_days():
    # Fri 17:30 → Mon 9:30：周末两天没有行（不是 0 小时行）
    s = datetime(2026, 7, 3, 17, 30, 0)
    e = datetime(2026, 7, 6, 9, 30, 0)
    rows = consumed_hours_by_day(s, e)
    assert rows == [(date(2026, 7, 3), 1.0), (date(2026, 7, 6), 0.5)]


def test_consumed_by_day_total_close_to_consumed_hours():
    # 两个口径各自按天取整到分钟再除 60，合计与 consumed_hours 只差舍入误差
    s = datetime(2026, 6, 29, 15, 0, 0)
    e = datetime(2026, 7, 2, 11, 7, 0)
    total_by_day = sum(h for _, h in consumed_hours_by_day(s, e))
    assert abs(total_by_day - consumed_hours(s, e)) < 0.05


def test_consumed_by_day_empty_when_non_positive():
    s = datetime(2026, 6, 29, 10, 0, 0)
    assert consumed_hours_by_day(s, s) == []
