from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field

from app.schemas.urls import UrlOut


class AnalyticsOverview(BaseModel):
    short_code: str
    total_clicks: int
    clicks_today: int
    clicks_last_7_days: int
    clicks_last_30_days: int


class DailyPoint(BaseModel):
    date: date
    clicks: int


class DailySeries(BaseModel):
    short_code: str
    start: date = Field(serialization_alias="from")
    end: date = Field(serialization_alias="to")
    total: int
    points: list[DailyPoint]

    model_config = {"populate_by_name": True}


class BreakdownItem(BaseModel):
    name: str
    clicks: int
    percentage: float


class Breakdown(BaseModel):
    short_code: str
    total: int
    items: list[BreakdownItem]


class DashboardSummary(BaseModel):
    total_urls: int
    active_urls: int
    total_clicks: int
    average_clicks_per_url: float
    clicks_last_14_days: list[DailyPoint]
    top_links: list[UrlOut]
    recent_urls: list[UrlOut]
