from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class UserZentaoBinding(Base):
    """
    Stores per-user Zentao account binding.

    Passwords are stored encrypted (AES/Fernet) using APP_ZENTAO_BINDING_SECRET.
    Tokens are stored in plain text (short-lived, non-sensitive).
    Each user may only have one binding per base_url.
    """

    __tablename__ = "user_zentao_bindings"
    __table_args__ = (UniqueConstraint("user_id", name="uq_user_zentao_bindings_user_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)

    # Zentao server base URL, e.g. http://192.168.2.148:81/zentao
    base_url: Mapped[str] = mapped_column(String(500), nullable=False)
    zentao_account: Mapped[str] = mapped_column(String(120), nullable=False)

    # AES/Fernet encrypted password — never stored or returned as plain text
    zentao_password_ciphertext: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # IV is embedded inside Fernet token, field kept for forward compatibility
    zentao_password_iv: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    # Short-lived Zentao API token — refreshed automatically on expiry
    token_value: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    token_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    # Refresh tracking
    last_refresh_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_refresh_status: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)  # ok | error
    last_error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    user = relationship("User", back_populates="zentao_binding")


__all__ = ["UserZentaoBinding"]
