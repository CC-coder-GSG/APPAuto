from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.utils.time_utils import local_now


class UserAIProviderConfig(Base):
    """Per-user AI provider (DeepSeek etc.) API key configuration.

    api_key is stored encrypted via Fernet (reuses APP_ZENTAO_BINDING_SECRET key
    derivation — see user_ai_config_service._get_fernet). Plain text is never
    stored and never returned by the API.
    """

    __tablename__ = "user_ai_provider_configs"
    __table_args__ = (
        UniqueConstraint("user_id", "provider_name", name="uq_user_ai_provider_user_provider"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )

    provider_name: Mapped[str] = mapped_column(String(40), nullable=False, default="deepseek")

    # Fernet-encrypted API key (never plaintext at rest)
    api_key_encrypted: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Short masked display form, e.g. sk-abcd********wxyz
    api_key_masked: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)

    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=local_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=local_now, onupdate=local_now, nullable=False
    )


__all__ = ["UserAIProviderConfig"]
