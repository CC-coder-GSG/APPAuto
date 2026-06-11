from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.utils.time_utils import utc_now_naive


class TerminalStreamTicket(Base):
    """
    连接画面/操作 WebSocket 的一次性短期票据。

    WS 握手不方便带 Authorization 头（浏览器/原生），故采用「先用 JWT 换一次性票据，
    再用票据连 WS」的模式，避免把长效 token 暴露在 URL 上。票据短期（默认 60s）、用完即焚。
    """

    __tablename__ = "terminal_stream_tickets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    token: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    device_id: Mapped[int] = mapped_column(
        ForeignKey("terminal_devices.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    mode: Mapped[str] = mapped_column(String(20), nullable=False)  # view | control

    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    used_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now_naive, nullable=False)


__all__ = ["TerminalStreamTicket"]
