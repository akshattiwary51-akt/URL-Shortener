from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    String,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.models.base import Base


class Url(Base):
    __tablename__ = "urls"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    short_code: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    original_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    owner_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"), nullable=False)
    total_clicks: Mapped[int] = mapped_column(BigInteger, server_default=text("0"), nullable=False)

    __table_args__ = (
        CheckConstraint("char_length(short_code) BETWEEN 3 AND 32", name="short_code_length"),
        CheckConstraint("total_clicks >= 0", name="total_clicks_non_negative"),
        # Owner listing uses keyset pagination on (owner_id, id).
        Index("ix_urls_owner_id_id", "owner_id", "id"),
        # Cleanup scans only rows that can expire.
        Index("ix_urls_expires_at", "expires_at", postgresql_where=text("expires_at IS NOT NULL")),
    )
