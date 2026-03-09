from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Enum as SAEnum, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.enums import VersionType


class Version(Base):
    __tablename__ = "versions"
    __table_args__ = (UniqueConstraint("version_no", "version_type", name="uq_version_no_type"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    version_no: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    version_type: Mapped[VersionType] = mapped_column(SAEnum(VersionType), nullable=False)
    parent_id: Mapped[Optional[int]] = mapped_column(ForeignKey("versions.id", ondelete="CASCADE"), nullable=True)
    software_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    parent = relationship("Version", remote_side=[id], back_populates="children")
    children = relationship("Version", back_populates="parent", cascade="all, delete-orphan")
    requirements = relationship("Requirement", back_populates="major_version", cascade="all, delete-orphan", foreign_keys="Requirement.major_version_id")
    executions = relationship("TestExecution", back_populates="minor_version", cascade="all, delete-orphan")
    bugs = relationship("BugTracking", back_populates="major_version", cascade="all, delete-orphan", foreign_keys="BugTracking.major_version_id")


__all__ = ["Version"]
