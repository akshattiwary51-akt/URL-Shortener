"""Read-side analytics queries. Everything is SQL aggregation; nothing is loaded wholesale."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import (
    BrowserAnalytics,
    CountryAnalytics,
    DailyAnalytics,
    DeviceAnalytics,
    OsAnalytics,
    ReferrerAnalytics,
    Url,
)

_DAILY_SQL = text(
    """
    SELECT d::date AS day, COALESCE(a.total_clicks, 0) AS clicks
    FROM generate_series(CAST(:start AS date), CAST(:end AS date), interval '1 day') AS d
    LEFT JOIN daily_analytics a ON a.short_code = :code AND a.date = d::date
    ORDER BY day
    """
)

_OWNER_DAILY_SQL = text(
    """
    SELECT d::date AS day, COALESCE(x.clicks, 0) AS clicks
    FROM generate_series(CAST(:start AS date), CAST(:end AS date), interval '1 day') AS d
    LEFT JOIN (
        SELECT a.date AS day, SUM(a.total_clicks) AS clicks
        FROM daily_analytics a JOIN urls u ON u.short_code = a.short_code
        WHERE u.owner_id = :owner AND a.date BETWEEN CAST(:start AS date) AND CAST(:end AS date)
        GROUP BY a.date
    ) x ON x.day = d::date
    ORDER BY day
    """
)

_BREAKDOWNS = {
    "browsers": (BrowserAnalytics, BrowserAnalytics.browser_name),
    "os": (OsAnalytics, OsAnalytics.os_name),
    "devices": (DeviceAnalytics, DeviceAnalytics.device_type),
    "countries": (CountryAnalytics, CountryAnalytics.country_code),
    "referrers": (ReferrerAnalytics, ReferrerAnalytics.referrer),
}


def utc_today() -> date:
    return datetime.now(timezone.utc).date()


@dataclass(frozen=True)
class Overview:
    total_clicks: int
    clicks_today: int
    clicks_last_7_days: int
    clicks_last_30_days: int


class AnalyticsService:
    async def overview(self, session: AsyncSession, url: Url) -> Overview:
        today = utc_today()
        row = (
            await session.execute(
                select(
                    func.coalesce(func.sum(DailyAnalytics.total_clicks).filter(DailyAnalytics.date == today), 0),
                    func.coalesce(
                        func.sum(DailyAnalytics.total_clicks).filter(DailyAnalytics.date > today - timedelta(days=7)), 0
                    ),
                    func.coalesce(func.sum(DailyAnalytics.total_clicks), 0),
                ).where(DailyAnalytics.short_code == url.short_code, DailyAnalytics.date > today - timedelta(days=30))
            )
        ).one()
        return Overview(url.total_clicks, int(row[0]), int(row[1]), int(row[2]))

    async def daily(self, session: AsyncSession, short_code: str, start: date, end: date) -> list[tuple[date, int]]:
        rows = await session.execute(_DAILY_SQL, {"code": short_code, "start": start, "end": end})
        return [(r.day, int(r.clicks)) for r in rows]

    async def breakdown(
        self, session: AsyncSession, kind: str, short_code: str, limit: int
    ) -> tuple[int, list[tuple[str, int]]]:
        """Top ``limit`` values plus the grand total across *all* values (for percentages)."""
        model, column = _BREAKDOWNS[kind]
        rows = (
            await session.execute(
                select(column, model.total_clicks, func.sum(model.total_clicks).over())
                .where(model.short_code == short_code)
                .order_by(model.total_clicks.desc(), column)
                .limit(limit)
            )
        ).all()
        total = int(rows[0][2]) if rows else 0
        return total, [(str(r[0]), int(r[1])) for r in rows]

    async def owner_summary(self, session: AsyncSession, owner_id: int) -> dict:
        now = datetime.now(timezone.utc)
        totals = (
            await session.execute(
                select(
                    func.count(Url.id),
                    func.count(Url.id).filter(Url.is_active, (Url.expires_at.is_(None)) | (Url.expires_at > now)),
                    func.coalesce(func.sum(Url.total_clicks), 0),
                ).where(Url.owner_id == owner_id)
            )
        ).one()
        top = list(
            (
                await session.execute(
                    select(Url).where(Url.owner_id == owner_id).order_by(Url.total_clicks.desc(), Url.id.desc()).limit(5)
                )
            ).scalars()
        )
        recent = list(
            (await session.execute(select(Url).where(Url.owner_id == owner_id).order_by(Url.id.desc()).limit(5))).scalars()
        )
        today = utc_today()
        series_rows = await session.execute(
            _OWNER_DAILY_SQL, {"owner": owner_id, "start": today - timedelta(days=13), "end": today}
        )
        series = [(r.day, int(r.clicks)) for r in series_rows]
        return {
            "total_urls": int(totals[0]),
            "active_urls": int(totals[1]),
            "total_clicks": int(totals[2]),
            "top": top,
            "recent": recent,
            "series": series,
        }
