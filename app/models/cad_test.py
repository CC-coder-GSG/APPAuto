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
    # 条目级共享 CAD 文件（含散装文件与文件夹内文件）：上传一次，该条目在所有版本下通用。
    # 唯一的 delete-orphan 父级，避免与文件夹关系产生双父级 orphan 冲突。
    cad_files = relationship(
        "CadItemFile", back_populates="item", cascade="all, delete-orphan", order_by="CadItemFile.id"
    )
    # 条目级共享 CAD 文件夹：整组图纸（含外部参照）打包归档，跨版本通用。
    cad_folders = relationship(
        "CadItemFolder", back_populates="item", cascade="all, delete-orphan", order_by="CadItemFolder.id"
    )


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


class CadItemFolder(Base):
    """条目级共享 CAD 文件夹：整组图纸（含外部参照）作为一个文件夹归档，跨所有版本通用。"""

    __tablename__ = "cad_item_folders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("cad_items.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)  # 文件夹名（取自上传时的顶层目录名）
    uploaded_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=local_now, nullable=False)

    item = relationship("CadItem", back_populates="cad_folders")
    # 只读视图：文件夹内的文件。其生命周期由 CadItem.cad_files（唯一 delete-orphan 父级）与
    # 服务层显式删除负责，避免双父级 orphan 冲突。
    files = relationship(
        "CadItemFile",
        back_populates="folder",
        viewonly=True,
        order_by="CadItemFile.rel_path, CadItemFile.id",
    )


class CadItemFile(Base):
    """条目级共享 CAD 文件：跨所有版本通用，独立于版本记录的生命周期。
    folder_id 为空表示散装单文件；非空表示隶属于某个上传的文件夹，rel_path 保存其在文件夹内的相对路径。"""

    __tablename__ = "cad_item_files"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("cad_items.id", ondelete="CASCADE"), nullable=False, index=True)
    # 隶属文件夹（可空：NULL=散装单文件，保持原有行为）。
    folder_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("cad_item_folders.id", ondelete="CASCADE"), nullable=True, index=True
    )
    # 在文件夹内的相对路径（含子目录与文件名，如 "外部参照/ref1.dwg"）；散装文件为空。
    rel_path: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    original_name: Mapped[str] = mapped_column(String(255), nullable=False)
    stored_name: Mapped[str] = mapped_column(String(120), nullable=False)
    file_path: Mapped[str] = mapped_column(Text, nullable=False)
    file_type: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    file_ext: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    file_size: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    uploaded_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=local_now, nullable=False)

    item = relationship("CadItem", back_populates="cad_files")
    folder = relationship("CadItemFolder", back_populates="files")


__all__ = [
    "CadBoard",
    "CadVersion",
    "CadCustomColumn",
    "CadItem",
    "CadRecord",
    "CadAttachment",
    "CadItemFile",
    "CadItemFolder",
]
