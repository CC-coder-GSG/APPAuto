from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.utils.time_utils import local_now


class CadBoard(Base):
    """CAD 测试统计表/看板。一张表自管版本、条目与自定义列。"""

    __tablename__ = "cad_boards"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=local_now, nullable=False)

    versions = relationship(
        "CadVersion", back_populates="board", cascade="all, delete-orphan", order_by="CadVersion.sort_order, CadVersion.id"
    )
    columns = relationship(
        "CadCustomColumn", back_populates="board", cascade="all, delete-orphan", order_by="CadCustomColumn.sort_order, CadCustomColumn.id"
    )
    items = relationship(
        "CadItem", back_populates="board", cascade="all, delete-orphan", order_by="CadItem.sort_order, CadItem.id"
    )


class CadVersion(Base):
    """统计表下的一个版本（列分组）。"""

    __tablename__ = "cad_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    board_id: Mapped[int] = mapped_column(ForeignKey("cad_boards.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=local_now, nullable=False)

    board = relationship("CadBoard", back_populates="versions")


class CadCustomColumn(Base):
    """统计表下的自定义命名列（如“华测-测地通”），值按 记录(item×version) 存储。"""

    __tablename__ = "cad_custom_columns"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    board_id: Mapped[int] = mapped_column(ForeignKey("cad_boards.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=local_now, nullable=False)

    board = relationship("CadBoard", back_populates="columns")


class CadItem(Base):
    """统计表下的一个测试条目（行）。跨版本追踪同一对象，可关联禅道 Bug。"""

    __tablename__ = "cad_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    board_id: Mapped[int] = mapped_column(ForeignKey("cad_boards.id", ondelete="CASCADE"), nullable=False, index=True)
    seq: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # 序号
    title: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)  # 可选标题/图纸主名
    # 关联测试管理中的禅道 Bug（“ID”列），用于预览/跳转。
    zentao_bug_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=local_now, nullable=False)

    board = relationship("CadBoard", back_populates="items")
    records = relationship("CadRecord", back_populates="item", cascade="all, delete-orphan")


class CadRecord(Base):
    """条目 × 版本 的记录单元：正常/异常数、问题说明、自定义列值、附件（CAD/截图）。"""

    __tablename__ = "cad_records"
    __table_args__ = (UniqueConstraint("item_id", "version_id", name="uq_cad_record_item_version"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("cad_items.id", ondelete="CASCADE"), nullable=False, index=True)
    version_id: Mapped[int] = mapped_column(ForeignKey("cad_versions.id", ondelete="CASCADE"), nullable=False, index=True)
    normal_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    abnormal_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # 异常说明/问题说明
    # 自定义列值，JSON: {"<custom_column_id>": "值"}
    custom_values: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=local_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=local_now, onupdate=local_now, nullable=False)

    item = relationship("CadItem", back_populates="records")
    version = relationship("CadVersion")
    attachments = relationship(
        "CadAttachment", back_populates="record", cascade="all, delete-orphan", order_by="CadAttachment.id"
    )


class CadAttachment(Base):
    """记录的附件：CAD 文件（kind=cad，仅下载）或截图（kind=screenshot，内联预览）。"""

    __tablename__ = "cad_attachments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    record_id: Mapped[int] = mapped_column(ForeignKey("cad_records.id", ondelete="CASCADE"), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(20), default="screenshot", nullable=False)  # cad | screenshot
    original_name: Mapped[str] = mapped_column(String(255), nullable=False)
    stored_name: Mapped[str] = mapped_column(String(120), nullable=False)
    file_path: Mapped[str] = mapped_column(Text, nullable=False)
    file_type: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    file_ext: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    file_size: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_image: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    uploaded_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=local_now, nullable=False)

    record = relationship("CadRecord", back_populates="attachments")


__all__ = [
    "CadBoard",
    "CadVersion",
    "CadCustomColumn",
    "CadItem",
    "CadRecord",
    "CadAttachment",
]
