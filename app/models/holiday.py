from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from sqlalchemy import Boolean, Date, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.utils.time_utils import local_now


class Holiday(Base):
    """法定节假日 / 调休工作日缓存表。

    由节假日服务从公开 API 拉取后落库（API 不可达时回退本表/仅跳周末）。
    工时/工作日计算据此判断某日是否为工作日：
      - is_off=True  → 放假（即便是工作日也跳过）
      - is_off=False → 调休补班（即便是周末也算工作日）
    未在表中的日期按"周末休、工作日上班"默认规则处理。
    """

    __tablename__ = "holidays"

    day: Mapped[date] = mapped_column(Date, primary_key=True)
    # True=放假，False=调休补班
    is_off: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    name: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    # 数据来源年份，便于按年刷新
    year: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=local_now, onupdate=local_now, nullable=False)


__all__ = ["Holiday"]
