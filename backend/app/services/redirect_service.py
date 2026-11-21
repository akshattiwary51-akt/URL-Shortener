"""Resolution of a short code to a redirect decision (cache-aside, negative caching)."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.database.models import Url
from app.services.cache_service import GONE, CacheRecord, CacheService, CacheState

SHORT_CODE_RE = re.compile(r"^[A-Za-z0-9_-]{3,32}$")

Outcome = Literal["ok", "not_found", "gone"]


@dataclass(frozen=True, slots=True)
class Resolution:
    outcome: Outcome
    url: str | None = None
    source: Literal["cache", "db", "negative_cache"] = "db"


_NOT_FOUND_DB = Resolution("not_found", None, "db")


class RedirectService:
    def __init__(
        self, settings: Settings, sessionmaker: async_sessionmaker[AsyncSession], cache: CacheService
    ) -> None:
        self._settings = settings
        self._sessionmaker = sessionmaker
        self._cache = cache

    @staticmethod
    def is_valid_code(code: str) -> bool:
        return SHORT_CODE_RE.fullmatch(code) is not None

    async def resolve(self, short_code: str) -> Resolution:
        now = time.time()
        cached = await self._cache.get(short_code)
        if cached.state is CacheState.HIT:
            record = cached.record
            assert record is not None
            if record.is_expired(now):
                return Resolution("gone", None, "cache")
            return Resolution("ok", record.url, "cache")
        if cached.state is CacheState.NOT_FOUND:
            return Resolution("not_found", None, "negative_cache")
        if cached.state is CacheState.GONE:
            return Resolution("gone", None, "negative_cache")

        # Cache miss (or cache unavailable): indexed lookup on urls.short_code.
        async with self._sessionmaker() as session:
            row = (
                await session.execute(
                    select(Url.original_url, Url.expires_at, Url.is_active).where(Url.short_code == short_code)
                )
            ).first()

        if row is None:
            await self._cache.populate_negative(short_code)
            return _NOT_FOUND_DB

        original_url, expires_at, is_active = row
        expires_epoch = int(expires_at.timestamp()) if expires_at is not None else None
        record = CacheRecord(original_url, expires_epoch)
        if not is_active or record.is_expired(now):
            await self._cache.populate_negative(short_code, GONE)
            return Resolution("gone", None, "db")

        await self._cache.populate(short_code, record, now)
        return Resolution("ok", original_url, "db")
