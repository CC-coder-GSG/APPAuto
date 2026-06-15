from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, Enum as SAEnum, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.enums import VersionType
from app.utils.time_utils import local_now


class Version(Base):
    __tablename__ = "versions"
    __table_args__ = (UniqueConstraint("version_no", "version_type", name="uq_version_no_type"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    version_no: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    version_type: Mapped[VersionType] = mapped_column(SAEnum(VersionType), nullable=False)
    parent_id: Mapped[Optional[int]] = mapped_column(ForeignKey("versions.id", ondelete="CASCADE"), nullable=True)
    software_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=local_now, nullable=False)

    # 最终测试阶段（仅对 MAJOR 大版本有意义）：开启后全员复测该版本所有需求，
    # 勾选状态按用户独立存到 final_test_records，与常规分配/勾选互不影响。
    final_test_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    final_test_started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    # Zentao anchor fields — applicable to both MAJOR and MINOR versions
    # MAJOR: zentao_project_id / zentao_execution_id are primary anchors
    # MINOR: zentao_build_id / zentao_testtask_id / zentao_release_id are additionally populated
    zentao_project_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    zentao_project_name_cache: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    zentao_execution_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    zentao_execution_name_cache: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    zentao_build_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    zentao_build_name_cache: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    zentao_testtask_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    zentao_testtask_name_cache: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    zentao_release_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    zentao_release_name_cache: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    parent = relationship("Version", remote_side=[id], back_populates="children")
    children = relationship("Version", back_populates="parent", cascade="all, delete-orphan")
    requirements = relationship("Requirement", back_populates="major_version", cascade="all, delete-orphan", foreign_keys="Requirement.major_version_id")
    executions = relationship("TestExecution", back_populates="minor_version", cascade="all, delete-orphan")
    bugs = relationship("BugTracking", back_populates="major_version", cascade="all, delete-orphan", foreign_keys="BugTracking.major_version_id")


__all__ = ["Version"]
