from __future__ import annotations

from datetime import date, datetime

from app.utils.work_hours import (
    consumed_hours,
    consumed_hours_by_day,
    estimate_hours,
    is_workday,
    working_days,
)


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


def test_consumed_full_day_no_window():
    # 2026-07-17 起取消工作时段窗口：工作日内按自然时间计
    s = datetime(2026, 6, 29, 9, 0, 0)
    e = datetime(2026, 6, 29, 18, 30, 0)
    assert consumed_hours(s, e) == 9.5


def test_consumed_counts_lunch_and_overtime():
    # 午休照计
    assert consumed_hours(datetime(2026, 6, 29, 11, 0), datetime(2026, 6, 29, 14, 0)) == 3.0
    # 加班（下班后 19:00-21:30）照计——取消窗口的核心诉求
    assert consumed_hours(datetime(2026, 6, 29, 19, 0), datetime(2026, 6, 29, 21, 30)) == 2.5


def test_consumed_spans_weekend():
    # Fri 7/3 17:30 → Mon 7/6 9:30
    s = datetime(2026, 7, 3, 17, 30, 0)
    e = datetime(2026, 7, 6, 9, 30, 0)
    # Fri: 17:30-24:00 = 6.5h; Sat/Sun skipped; Mon: 0:00-9:30 = 9.5h
    assert consumed_hours(s, e) == 16.0


def test_consumed_spans_holiday():
    hmap = {date(2026, 7, 1): True}
    s = datetime(2026, 6, 30, 18, 0, 0)  # Tue 18:00-24:00 = 6h
    e = datetime(2026, 7, 2, 9, 30, 0)   # Thu 0:00-9:30 = 9.5h; Wed(7/1) holiday skipped
    assert consumed_hours(s, e, hmap) == 15.5


def test_consumed_non_positive_range():
    s = datetime(2026, 6, 29, 10, 0, 0)
    assert consumed_hours(s, s) == 0.0
    assert consumed_hours(s, datetime(2026, 6, 29, 9, 0, 0)) == 0.0


# ─── 按天拆分（分段提交禅道工时记录用）─────────────────────────────────────


def test_consumed_by_day_cross_days():
    # Mon 15:00 → Wed 10:30：Mon 15:00-24:00=9h；Tue 全天 24h；Wed 0:00-10:30=10.5h
    s = datetime(2026, 6, 29, 15, 0, 0)
    e = datetime(2026, 7, 1, 10, 30, 0)
    rows = consumed_hours_by_day(s, e)
    assert rows == [
        (date(2026, 6, 29), 9.0),
        (date(2026, 6, 30), 24.0),
        (date(2026, 7, 1), 10.5),
    ]


def test_consumed_by_day_skips_weekend_and_zero_days():
    # Fri 17:30 → Mon 9:30：周末两天没有行（不是 0 小时行）
    s = datetime(2026, 7, 3, 17, 30, 0)
    e = datetime(2026, 7, 6, 9, 30, 0)
    rows = consumed_hours_by_day(s, e)
    assert rows == [(date(2026, 7, 3), 6.5), (date(2026, 7, 6), 9.5)]


def test_consumed_by_day_total_close_to_consumed_hours():
    # 两个口径各自按天取整到分钟再除 60，合计与 consumed_hours 只差舍入误差
    s = datetime(2026, 6, 29, 15, 0, 0)
    e = datetime(2026, 7, 2, 11, 7, 0)
    total_by_day = sum(h for _, h in consumed_hours_by_day(s, e))
    assert abs(total_by_day - consumed_hours(s, e)) < 0.05


def test_consumed_by_day_empty_when_non_positive():
    s = datetime(2026, 6, 29, 10, 0, 0)
    assert consumed_hours_by_day(s, s) == []
