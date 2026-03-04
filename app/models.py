from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from passlib.context import CryptContext
from sqlalchemy import Boolean, Column, DateTime, Enum as SAEnum, ForeignKey, Integer, String, Text, UniqueConstraint
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


class BugSourceType(str, Enum):
    REQUIREMENT = "requirement"
    CASE = "case"
    LEGACY_BUG = "legacy_bug"
    MANUAL = "manual"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    username: Mapped[str] = mapped_column(String(50), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[UserRole] = mapped_column(SAEnum(UserRole), default=UserRole.USER, nullable=False)
    is_team_member: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    assigned_requirements: Mapped[list[Requirement]] = relationship(
        "Requirement",
        back_populates="owner",
        foreign_keys="Requirement.owner_id",
    )
    retested_requirements: Mapped[list[Requirement]] = relationship(
        "Requirement",
        back_populates="retester",
        foreign_keys="Requirement.retested_by_id",
    )

    @staticmethod
    def hash_password(raw_password: str) -> str:
        return pwd_context.hash(raw_password)

    def verify_password(self, raw_password: str) -> bool:
        return pwd_context.verify(raw_password, self.password_hash)


class Version(Base):
    __tablename__ = "versions"
    __table_args__ = (UniqueConstraint("version_no", "version_type", name="uq_version_no_type"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    version_no: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    version_type: Mapped[VersionType] = mapped_column(SAEnum(VersionType), nullable=False)
    parent_id: Mapped[Optional[int]] = mapped_column(ForeignKey("versions.id", ondelete="CASCADE"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    parent: Mapped[Optional[Version]] = relationship("Version", remote_side=[id], back_populates="children")
    children: Mapped[list[Version]] = relationship("Version", back_populates="parent", cascade="all, delete-orphan")
    requirements: Mapped[list[Requirement]] = relationship("Requirement", back_populates="major_version", cascade="all, delete-orphan")
    executions: Mapped[list[TestExecution]] = relationship("TestExecution", back_populates="minor_version", cascade="all, delete-orphan")
    bugs: Mapped[list[BugTracking]] = relationship(
        "BugTracking",
        back_populates="major_version",
        cascade="all, delete-orphan",
        foreign_keys="BugTracking.major_version_id",
    )


class Requirement(Base):
    __tablename__ = "requirements"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    zentao_req_id: Mapped[str] = mapped_column(String(20), nullable=False, unique=True, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)

    major_version_id: Mapped[int] = mapped_column(ForeignKey("versions.id", ondelete="CASCADE"), nullable=False)
    owner_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)

    case_completed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    test_completed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    retest_completed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    retested_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    retested_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    status: Mapped[RequirementStatus] = mapped_column(SAEnum(RequirementStatus), default=RequirementStatus.PENDING, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    major_version: Mapped[Version] = relationship("Version", back_populates="requirements")
    owner: Mapped[Optional[User]] = relationship("User", back_populates="assigned_requirements", foreign_keys=[owner_id])
    retester: Mapped[Optional[User]] = relationship("User", back_populates="retested_requirements", foreign_keys=[retested_by_id])
    test_cases: Mapped[list[TestCase]] = relationship("TestCase", back_populates="requirement", cascade="all, delete-orphan")
    test_executions: Mapped[list[TestExecution]] = relationship("TestExecution", back_populates="requirement", cascade="all, delete-orphan")
    bug_tracks: Mapped[list[BugTracking]] = relationship("BugTracking", back_populates="requirement", cascade="all, delete-orphan")


class TestCase(Base):
    __tablename__ = "test_cases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    requirement_id: Mapped[int] = mapped_column(ForeignKey("requirements.id", ondelete="CASCADE"), nullable=False)
    zentao_case_id: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    creator_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    requirement: Mapped[Requirement] = relationship("Requirement", back_populates="test_cases")


class TestExecution(Base):
    __tablename__ = "test_executions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    requirement_id: Mapped[int] = mapped_column(ForeignKey("requirements.id", ondelete="CASCADE"), nullable=False)
    minor_version_id: Mapped[int] = mapped_column(ForeignKey("versions.id", ondelete="CASCADE"), nullable=False)

    bug_id: Mapped[Optional[str]] = mapped_column(String(30), nullable=True, index=True)
    source_case_id: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    result_status: Mapped[str] = mapped_column(String(30), default="untested", nullable=False)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    executed_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    executed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    requirement: Mapped[Requirement] = relationship("Requirement", back_populates="test_executions")
    minor_version: Mapped[Version] = relationship("Version", back_populates="executions")


class BugTracking(Base):
    __tablename__ = "bug_tracking"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    major_version_id: Mapped[int] = mapped_column(ForeignKey("versions.id", ondelete="CASCADE"), nullable=False)
    requirement_id: Mapped[Optional[int]] = mapped_column(ForeignKey("requirements.id", ondelete="CASCADE"), nullable=True)
    source_type: Mapped[BugSourceType] = mapped_column(SAEnum(BugSourceType), default=BugSourceType.REQUIREMENT, nullable=False)
    source_ref: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)

    bug_id: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    latest_minor_version_id: Mapped[Optional[int]] = mapped_column(ForeignKey("versions.id"), nullable=True)
    test_done: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    newly_found_bug_id: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    closed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    created_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    closed_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    requirement: Mapped[Optional[Requirement]] = relationship("Requirement", back_populates="bug_tracks")
    major_version: Mapped[Version] = relationship("Version", back_populates="bugs", foreign_keys=[major_version_id])
    stage5_records = relationship("BugStage5Record", backref="bug", cascade="all, delete-orphan")


class BugStage5Record(Base):
    __tablename__ = "bug_stage5_records"
    id = Column(Integer, primary_key=True, index=True)
    bug_tracking_id = Column(Integer, ForeignKey("bug_tracking.id"))
    user_id = Column(Integer, ForeignKey("users.id"))
    minor_version_id = Column(Integer, ForeignKey("versions.id"))
    test_done = Column(Boolean, default=False)
    newly_found_bug_id = Column(String, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = relationship("User")
    minor_version = relationship("Version")
