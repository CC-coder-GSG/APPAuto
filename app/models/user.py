from __future__ import annotations

from datetime import datetime
from typing import Optional

from passlib.context import CryptContext
from sqlalchemy import Boolean, DateTime, Enum as SAEnum, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.enums import UserRole
from app.utils.time_utils import local_now

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    username: Mapped[str] = mapped_column(String(50), unique=True, nullable=False, index=True)
    display_name: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[UserRole] = mapped_column(SAEnum(UserRole), default=UserRole.USER, nullable=False)
    is_team_member: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    tab_permissions: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    session_token: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=local_now, nullable=False)

    assigned_requirements = relationship("Requirement", back_populates="owner", foreign_keys="Requirement.owner_id")
    retested_requirements = relationship("Requirement", back_populates="retester", foreign_keys="Requirement.retested_by_id")
    zentao_binding = relationship("UserZentaoBinding", back_populates="user", uselist=False, cascade="all, delete-orphan")
    jenkins_binding = relationship("UserJenkinsBinding", back_populates="user", uselist=False, cascade="all, delete-orphan")

    @staticmethod
    def hash_password(raw_password: str) -> str:
        return pwd_context.hash(raw_password)

    def verify_password(self, raw_password: str) -> bool:
        return pwd_context.verify(raw_password, self.password_hash)

    @property
    def shown_name(self) -> str:
        name = (self.display_name or "").strip()
        return name or self.username


__all__ = ["User"]
