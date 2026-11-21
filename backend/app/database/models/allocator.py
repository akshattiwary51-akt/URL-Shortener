from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, String, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.database.models.base import Base


class IdAllocator(Base):
    __tablename__ = "id_allocator"

    allocator_name: Mapped[str] = mapped_column(String(64), primary_key=True)
    next_id: Mapped[int] = mapped_column(BigInteger, server_default=text("0"), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
