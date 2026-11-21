from __future__ import annotations

from datetime import datetime, timezone

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from app.database.models import Url


class UrlCreate(BaseModel):
    # extra="forbid" => mass-assignment protection (cannot inject owner_id, total_clicks, ...)
    model_config = ConfigDict(extra="forbid")
    original_url: str = Field(min_length=1, max_length=4096)
    custom_alias: str | None = Field(default=None, max_length=64)
    expires_at: AwareDatetime | None = None


class UrlUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    is_active: bool | None = None
    expires_at: AwareDatetime | None = None  # explicit null clears the expiry


class UrlOut(BaseModel):
    short_code: str
    short_url: str
    original_url: str
    created_at: datetime
    expires_at: datetime | None
    is_active: bool
    is_expired: bool
    total_clicks: int
    is_owned: bool = Field(description="True when the URL belongs to a user account.")


class UrlList(BaseModel):
    items: list[UrlOut]
    next_cursor: str | None


def url_out(url: Url, base_url: str) -> UrlOut:
    return UrlOut(
        short_code=url.short_code,
        short_url=f"{base_url}/{url.short_code}",
        original_url=url.original_url,
        created_at=url.created_at,
        expires_at=url.expires_at,
        is_active=url.is_active,
        is_expired=bool(url.expires_at and url.expires_at <= datetime.now(timezone.utc)),
        total_clicks=url.total_clicks,
        is_owned=url.owner_id is not None,
    )
