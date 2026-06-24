from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.utils.time_utils import local_now


class FeatureTreeNode(Base):
    """软件功能树节点（自引用邻接表）。

    根节点（is_root）每个软件一个，名取软件名；其余为可自由命名、无限层级的
    功能分支。note_html 为富文本功能备注（用途 / 测试注意点），与"测试标记"
    （FeatureTreeMark）相互独立——备注是图谱本身的说明，标记是某次最终测试的留痕。
    """

    __tablename__ = "feature_tree_nodes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    # 与 Version.software_id 一致：裸 Integer + 索引，不设外键约束。
    software_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    parent_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("feature_tree_nodes.id", ondelete="CASCADE"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    is_root: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    note_html: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    note_updated_by: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    note_updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_by: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=local_now, nullable=False)

    parent = relationship("FeatureTreeNode", remote_side=[id], back_populates="children")
    children = relationship(
        "FeatureTreeNode",
        back_populates="parent",
        cascade="all, delete-orphan",
        order_by="FeatureTreeNode.sort_order, FeatureTreeNode.id",
    )
    marks = relationship("FeatureTreeMark", back_populates="node", cascade="all, delete-orphan")


class FeatureTreeMark(Base):
    """某个最终测试大版本下，某测试人对某功能节点打的"已测"标记 + 富文本说明。

    一个 (node, version, user) 只有一条；每个测试人前端用按 user_id 确定性配色的
    彩色小圆点区分。
    """

    __tablename__ = "feature_tree_marks"
    __table_args__ = (
        UniqueConstraint("node_id", "version_id", "user_id", name="uq_feature_mark_node_ver_user"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    node_id: Mapped[int] = mapped_column(
        ForeignKey("feature_tree_nodes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    comment_html: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # True 表示该标记是"子节点全部标记后自动汇总"产生的（非测试人手动所打）。
    is_auto: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=local_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=local_now, onupdate=local_now, nullable=False)

    node = relationship("FeatureTreeNode", back_populates="marks")


__all__ = ["FeatureTreeNode", "FeatureTreeMark"]
