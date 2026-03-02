from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from passlib.context import CryptContext
from sqlalchemy import (
    Boolean,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


class UserRole(str, Enum):
    ADMIN = "admin"
    USER = "user"


class VersionType(str, Enum):
    MAJOR = "major"
    MINOR = "minor"


class RequirementStatus(str, Enum):
    PENDING = "pending"
    ASSIGNED = "assigned"
    CASE_DONE = "case_done"
    TESTING = "testing"
    TEST_DONE = "test_done"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    username: Mapped[str] = mapped_column(String(50), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[UserRole] = mapped_column(SAEnum(UserRole), default=UserRole.USER, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    assigned_requirements: Mapped[list[Requirement]] = relationship(
        "Requirement",
        back_populates="owner",
        foreign_keys="Requirement.owner_id",
    )

    @staticmethod
    def hash_password(raw_password: str) -> str:
        return pwd_context.hash(raw_password)

    def verify_password(self, raw_password: str) -> bool:
        return pwd_context.verify(raw_password, self.password_hash)


class Version(Base):
    __tablename__ = "versions"
    __table_args__ = (
        UniqueConstraint("version_no", "version_type", name="uq_version_no_type"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    version_no: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    version_type: Mapped[VersionType] = mapped_column(SAEnum(VersionType), nullable=False)
    parent_id: Mapped[Optional[int]] = mapped_column(ForeignKey("versions.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    parent: Mapped[Optional[Version]] = relationship("Version", remote_side=[id], back_populates="children")
    children: Mapped[list[Version]] = relationship("Version", back_populates="parent")
    requirements: Mapped[list[Requirement]] = relationship("Requirement", back_populates="major_version")
    executions: Mapped[list[TestExecution]] = relationship("TestExecution", back_populates="minor_version")


class Requirement(Base):
    __tablename__ = "requirements"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    internal_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    zentao_req_id: Mapped[str] = mapped_column(String(20), nullable=False, index=True)  # r#xxxx
    title: Mapped[str] = mapped_column(String(255), nullable=False)

    major_version_id: Mapped[int] = mapped_column(ForeignKey("versions.id"), nullable=False)
    owner_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)

    case_completed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    test_completed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    status: Mapped[RequirementStatus] = mapped_column(
        SAEnum(RequirementStatus),
        default=RequirementStatus.PENDING,
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )

    major_version: Mapped[Version] = relationship("Version", back_populates="requirements")
    owner: Mapped[Optional[User]] = relationship("User", back_populates="assigned_requirements")
    test_cases: Mapped[list[TestCase]] = relationship("TestCase", back_populates="requirement", cascade="all, delete-orphan")
    test_executions: Mapped[list[TestExecution]] = relationship(
        "TestExecution",
        back_populates="requirement",
        cascade="all, delete-orphan",
    )


class TestCase(Base):
    __tablename__ = "test_cases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    requirement_id: Mapped[int] = mapped_column(ForeignKey("requirements.id"), nullable=False)
    zentao_case_id: Mapped[str] = mapped_column(String(30), nullable=False, index=True)  # u#123
    creator_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    requirement: Mapped[Requirement] = relationship("Requirement", back_populates="test_cases")


class TestExecution(Base):
    __tablename__ = "test_executions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    requirement_id: Mapped[int] = mapped_column(ForeignKey("requirements.id"), nullable=False)
    minor_version_id: Mapped[int] = mapped_column(ForeignKey("versions.id"), nullable=False)

    bug_id: Mapped[Optional[str]] = mapped_column(String(30), nullable=True, index=True)  # b#9901
    source_case_id: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)  # u#123 or none
    result_status: Mapped[str] = mapped_column(String(30), default="untested", nullable=False)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    executed_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    executed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    requirement: Mapped[Requirement] = relationship("Requirement", back_populates="test_executions")
    minor_version: Mapped[Version] = relationship("Version", back_populates="executions")
