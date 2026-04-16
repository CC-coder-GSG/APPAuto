from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class SoftwareProduct(Base):
    __tablename__ = "software_products"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False, unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    # Zentao anchor fields
    zentao_product_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    zentao_product_name_cache: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)


__all__ = ["SoftwareProduct"]

