from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import Ctx, CurrentUser, Session
from app.schemas.analytics import DailyPoint, DashboardSummary
from app.schemas.urls import url_out

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("/summary", response_model=DashboardSummary)
async def summary(user: CurrentUser, ctx: Ctx, session: Session) -> DashboardSummary:
    data = await ctx.analytics.owner_summary(session, user.id)
    base = ctx.settings.base_url
    total_urls = data["total_urls"]
    return DashboardSummary(
        total_urls=total_urls,
        active_urls=data["active_urls"],
        total_clicks=data["total_clicks"],
        average_clicks_per_url=round(data["total_clicks"] / total_urls, 2) if total_urls else 0.0,
        clicks_last_14_days=[DailyPoint(date=d, clicks=c) for d, c in data["series"]],
        top_links=[url_out(u, base) for u in data["top"]],
        recent_urls=[url_out(u, base) for u in data["recent"]],
    )
