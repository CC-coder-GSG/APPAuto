from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from sqlalchemy import Date, DateTime, Float, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.utils.time_utils import local_now


class ZentaoTaskMirror(Base):
    """禅道任务只读镜像缓存（任务看板接入禅道用）。

    后台同步 job 周期性遍历已绑定执行、拉 `executions/{id}/tasks`，把任务 upsert 到本表；
    任务看板按需读取本表，避免每次开看板都实时全量拉禅道。

    本表是「缓存」，禅道为权威；不参与本地 `task_board_tasks` 体系。
    """

    __tablename__ = "zentao_task_mirror"
    __table_args__ = (
        Index("ix_zentao_task_mirror_exec", "execution_id"),
        Index("ix_zentao_task_mirror_assignee_user", "assignee_user_id"),
        Index("ix_zentao_task_mirror_dates", "est_started", "deadline"),
    )

    # 用禅道 task id 做主键，天然幂等
    task_id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    execution_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    execution_name_cache: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    project_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    parent: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_parent: Mapped[bool] = mapped_column(Integer, default=0, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    type: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    status: Mapped[Optional[str]] = mapped_column(String(40), nullable=True, index=True)
    pri: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    story: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # 禅道账号 + 映射回的本地用户（映射不到则为空）
    assigned_to: Mapped[Optional[str]] = mapped_column(String(120), nullable=True, index=True)
    assigned_to_realname: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    assignee_user_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)

    # 完成者（禅道 finishedBy）：任务完成后 assignedTo 常被流转给下一环节的人，
    # 周报等场景要展示真正做完任务的人，必须单独存
    finished_by: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    finished_by_realname: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)

    estimate: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    consumed: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    left: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    est_started: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    deadline: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    # 时刻字段统一转成上海本地 naive 存储
    real_started: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    finished_date: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    desc: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # 平台侧工时结算（独立任务开始/暂停/完成用；镜像同步不覆盖这几个字段）：
    # local_started_at = 本段计时起点（暂停时清空）；
    # consumed_accum = 尚未提交禅道的已结算工时（暂停时分段提交成功则清零，
    #   提交失败/无本人网页凭据时退回旧口径在此累计、完成时一次性提交）；
    # efforts_submitted = 平台已按段提交为禅道工时记录的小时合计（>0 说明该任务
    #   走过分段提交，完成兜底时不得再拿 left/estimate 起算，否则重复计入）。
    local_started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    consumed_accum: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    efforts_submitted: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)

    synced_at: Mapped[datetime] = mapped_column(DateTime, default=local_now, onupdate=local_now, nullable=False)


__all__ = ["ZentaoTaskMirror"]
