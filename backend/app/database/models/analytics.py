from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.models.base import Base

def _fk() -> ForeignKey:
    return ForeignKey("urls.short_code", ondelete="CASCADE")


class ClickEvent(Base):
    """Short-lived dedupe record. ``event_id`` is the idempotency key for the worker."""

    __tablename__ = "click_events"

    event_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    short_code: Mapped[str] = mapped_column(String(32), nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    user_agent: Mapped[str | None] = mapped_column(String(512), nullable=True)
    ip_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    referrer: Mapped[str | None] = mapped_column(String(512), nullable=True)

    __table_args__ = (Index("ix_click_events_timestamp", "timestamp"),)


class DailyAnalytics(Base):
    __tablename__ = "daily_analytics"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    short_code: Mapped[str] = mapped_column(String(32), _fk(), nullable=False)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    total_clicks: Mapped[int] = mapped_column(BigInteger, server_default=text("0"), nullable=False)

    __table_args__ = (
        UniqueConstraint("short_code", "date", name="uq_daily_analytics_short_code_date"),
        CheckConstraint("total_clicks >= 0", name="total_clicks_non_negative"),
        Index("ix_daily_analytics_date", "date"),
    )


class BrowserAnalytics(Base):
    __tablename__ = "browser_analytics"

    short_code: Mapped[str] = mapped_column(String(32), _fk(), primary_key=True)
    browser_name: Mapped[str] = mapped_column(String(64), primary_key=True)
    total_clicks: Mapped[int] = mapped_column(BigInteger, server_default=text("0"), nullable=False)


class OsAnalytics(Base):
    __tablename__ = "os_analytics"

    short_code: Mapped[str] = mapped_column(String(32), _fk(), primary_key=True)
    os_name: Mapped[str] = mapped_column(String(64), primary_key=True)
    total_clicks: Mapped[int] = mapped_column(BigInteger, server_default=text("0"), nullable=False)


class DeviceAnalytics(Base):
    __tablename__ = "device_analytics"

    short_code: Mapped[str] = mapped_column(String(32), _fk(), primary_key=True)
    device_type: Mapped[str] = mapped_column(String(32), primary_key=True)
    total_clicks: Mapped[int] = mapped_column(BigInteger, server_default=text("0"), nullable=False)


class CountryAnalytics(Base):
    __tablename__ = "country_analytics"

    short_code: Mapped[str] = mapped_column(String(32), _fk(), primary_key=True)
    country_code: Mapped[str] = mapped_column(String(2), primary_key=True)
    total_clicks: Mapped[int] = mapped_column(BigInteger, server_default=text("0"), nullable=False)


class ReferrerAnalytics(Base):
    __tablename__ = "referrer_analytics"

    short_code: Mapped[str] = mapped_column(String(32), _fk(), primary_key=True)
    referrer: Mapped[str] = mapped_column(String(255), primary_key=True)
    total_clicks: Mapped[int] = mapped_column(BigInteger, server_default=text("0"), nullable=False)
