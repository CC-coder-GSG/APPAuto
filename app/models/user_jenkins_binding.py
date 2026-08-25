from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.utils.time_utils import local_now


class UserJenkinsBinding(Base):
    """
    Stores per-user Jenkins account binding.

    每个测试管理系统用户用自己的 Jenkins 账号 + API Token 登录，由本系统代为触发
    Jenkins 上已配置好的自动化测试 Job。

    Jenkins 用 HTTP Basic Auth（username + API Token），Token 不过期，因此不需要
    像禅道那样维护短期 token 的刷新；只需把 API Token 加密存储（AES/Fernet）。
    每个用户仅有一条绑定。
    """

    __tablename__ = "user_jenkins_bindings"
    __table_args__ = (UniqueConstraint("user_id", name="uq_user_jenkins_bindings_user_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)

    # Jenkins server base URL, e.g. http://192.168.2.229:8080
    base_url: Mapped[str] = mapped_column(String(500), nullable=False)
    jenkins_account: Mapped[str] = mapped_column(String(120), nullable=False)

    # AES/Fernet encrypted API token — never stored or returned as plain text
    jenkins_token_ciphertext: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Connection check tracking
    last_check_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_check_status: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)  # ok | error
    last_error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=local_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=local_now, onupdate=local_now, nullable=False)

    user = relationship("User", back_populates="jenkins_binding")


__all__ = ["UserJenkinsBinding"]
