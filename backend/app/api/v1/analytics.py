from __future__ import annotations

from datetime import date, timedelta
from typing import Annotated

from fastapi import APIRouter, Path, Query

from app.api.deps import Ctx, CurrentUser, Session
from app.core.exceptions import ValidationFailed
from app.schemas.analytics import AnalyticsOverview, Breakdown, BreakdownItem, DailyPoint, DailySeries
from app.services.analytics_service import utc_today

router = APIRouter(prefix="/analytics", tags=["analytics"])

ShortCode = Annotated[str, Path(min_length=3, max_length=32, pattern=r"^[A-Za-z0-9_-]+$")]
MAX_RANGE_DAYS = 366


@router.get("/{short_code}", response_model=AnalyticsOverview)
async def overview(short_code: ShortCode, user: CurrentUser, ctx: Ctx, session: Session) -> AnalyticsOverview:
    url = await ctx.urls.get_owned(session, user.id, short_code)  # ownership enforced here
    o = await ctx.analytics.overview(session, url)
    return AnalyticsOverview(
        short_code=short_code,
        total_clicks=o.total_clicks,
        clicks_today=o.clicks_today,
        clicks_last_7_days=o.clicks_last_7_days,
        clicks_last_30_days=o.clicks_last_30_days,
    )


@router.get("/{short_code}/daily", response_model=DailySeries, response_model_by_alias=True)
async def daily(
    short_code: ShortCode,
    user: CurrentUser,
    ctx: Ctx,
    session: Session,
    start: Annotated[date | None, Query(alias="from")] = None,
    end: Annotated[date | None, Query(alias="to")] = None,
) -> DailySeries:
    await ctx.urls.get_owned(session, user.id, short_code)
    end = end or utc_today()
    start = start or end - timedelta(days=29)
    if start > end:
        raise ValidationFailed("'from' must not be after 'to'.")
    if (end - start).days + 1 > MAX_RANGE_DAYS:
        raise ValidationFailed(f"Date range must not exceed {MAX_RANGE_DAYS} days.")
    points = [DailyPoint(date=d, clicks=c) for d, c in await ctx.analytics.daily(session, short_code, start, end)]
    return DailySeries(short_code=short_code, start=start, end=end, total=sum(p.clicks for p in points), points=points)


async def _breakdown(kind: str, short_code: str, user, ctx, session, limit: int) -> Breakdown:  # type: ignore[no-untyped-def]
    await ctx.urls.get_owned(session, user.id, short_code)
    total, rows = await ctx.analytics.breakdown(session, kind, short_code, limit)
    return Breakdown(
        short_code=short_code,
        total=total,
        items=[BreakdownItem(name=n, clicks=c, percentage=round(100 * c / total, 2) if total else 0.0) for n, c in rows],
    )


Limit = Annotated[int, Query(ge=1, le=100)]


@router.get("/{short_code}/browsers", response_model=Breakdown)
async def browsers(short_code: ShortCode, user: CurrentUser, ctx: Ctx, session: Session, limit: Limit = 10) -> Breakdown:
    return await _breakdown("browsers", short_code, user, ctx, session, limit)


@router.get("/{short_code}/os", response_model=Breakdown)
async def operating_systems(short_code: ShortCode, user: CurrentUser, ctx: Ctx, session: Session, limit: Limit = 10) -> Breakdown:
    return await _breakdown("os", short_code, user, ctx, session, limit)


@router.get("/{short_code}/devices", response_model=Breakdown)
async def devices(short_code: ShortCode, user: CurrentUser, ctx: Ctx, session: Session, limit: Limit = 10) -> Breakdown:
    return await _breakdown("devices", short_code, user, ctx, session, limit)


@router.get("/{short_code}/referrers", response_model=Breakdown)
async def referrers(short_code: ShortCode, user: CurrentUser, ctx: Ctx, session: Session, limit: Limit = 10) -> Breakdown:
    return await _breakdown("referrers", short_code, user, ctx, session, limit)


@router.get("/{short_code}/countries", response_model=Breakdown)
async def countries(short_code: ShortCode, user: CurrentUser, ctx: Ctx, session: Session, limit: Limit = 10) -> Breakdown:
    return await _breakdown("countries", short_code, user, ctx, session, limit)
