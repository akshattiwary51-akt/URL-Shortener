"""Redis cache for short-link resolution.

Value formats (key ``url:{code}``):

* ``{"u": "<destination>", "e": <expiry epoch seconds | null>}`` - active link
* ``__NOT_FOUND__`` - negative cache (unknown code)
* ``__GONE__``      - tombstone for disabled/expired links (HTTP 410)

Consistency model
-----------------
*Population from a DB read* uses ``SET NX`` so it can never overwrite a value
written by a concurrent writer. *Write-through from a writer* (create / update /
delete) uses a plain ``SET`` after the DB commit and therefore always wins.
Together this closes the race where a slow reader repopulates stale data after
a writer has already updated the cache.
"""

from __future__ import annotations

import asyncio
import enum
import json
import logging
from dataclasses import dataclass
from typing import Any

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.core.config import Settings
from app.monitoring.metrics import AppMetrics
from app.services.resilience import CircuitBreaker

logger = logging.getLogger(__name__)

NOT_FOUND = "__NOT_FOUND__"
GONE = "__GONE__"
_REDIS_ERRORS = (RedisError, OSError, asyncio.TimeoutError)


def cache_key(short_code: str) -> str:
    return f"url:{short_code}"


class CacheState(enum.Enum):
    MISS = "miss"
    HIT = "hit"
    NOT_FOUND = "not_found"
    GONE = "gone"


@dataclass(frozen=True, slots=True)
class CacheRecord:
    url: str
    expires_at: int | None  # epoch seconds

    def is_expired(self, now_epoch: float) -> bool:
        return self.expires_at is not None and now_epoch >= self.expires_at

    def encode(self) -> str:
        return json.dumps({"u": self.url, "e": self.expires_at}, separators=(",", ":"))


@dataclass(frozen=True, slots=True)
class CacheResult:
    state: CacheState
    record: CacheRecord | None = None


_MISS = CacheResult(CacheState.MISS)


def decode_record(raw: str) -> CacheRecord | None:
    """Parse a cached value; returns ``None`` for anything malformed."""
    try:
        data: Any = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    url, exp = data.get("u"), data.get("e")
    if not isinstance(url, str) or not url.startswith(("http://", "https://")) or len(url) > 4096:
        return None
    if exp is not None and (not isinstance(exp, int) or isinstance(exp, bool)):
        return None
    return CacheRecord(url, exp)


class CacheService:
    def __init__(self, redis: Redis, settings: Settings, metrics: AppMetrics) -> None:
        self._redis = redis
        self._settings = settings
        self._metrics = metrics
        self._enabled = settings.cache_enabled
        self._breaker = CircuitBreaker(settings.redis_breaker_failures, settings.redis_breaker_cooldown_seconds)

    # -- internals ---------------------------------------------------------
    def _failed(self, op: str, exc: BaseException) -> None:
        self._breaker.record_failure()
        self._metrics.cache_errors.inc()
        logger.warning("cache redis error", extra={"op": op, "error_type": type(exc).__name__})

    def _usable(self) -> bool:
        return self._enabled and not self._breaker.is_open

    def positive_ttl(self, record: CacheRecord, now_epoch: float) -> int:
        """Never cache past expiry: TTL = min(configured TTL, time-to-expiry)."""
        ttl = self._settings.cache_ttl_seconds
        if record.expires_at is not None:
            ttl = min(ttl, int(record.expires_at - now_epoch))
        return ttl

    # -- reads -------------------------------------------------------------
    async def get(self, short_code: str) -> CacheResult:
        if not self._usable():
            return _MISS
        key = cache_key(short_code)
        try:
            raw = await self._redis.get(key)
        except _REDIS_ERRORS as exc:
            self._failed("get", exc)
            return _MISS
        self._breaker.record_success()
        if raw is None:
            self._metrics.cache_misses.inc()
            return _MISS
        if raw == NOT_FOUND:
            self._metrics.cache_negative_hits.inc()
            return CacheResult(CacheState.NOT_FOUND)
        if raw == GONE:
            self._metrics.cache_negative_hits.inc()
            return CacheResult(CacheState.GONE)
        record = decode_record(raw)
        if record is None:
            self._metrics.cache_corrupt.inc()
            self._metrics.cache_misses.inc()
            logger.warning("discarding malformed cache value", extra={"short_code": short_code})
            await self.delete(short_code)
            return _MISS
        self._metrics.cache_hits.inc()
        return CacheResult(CacheState.HIT, record)

    # -- population from DB reads (never overwrites) -----------------------
    async def populate(self, short_code: str, record: CacheRecord, now_epoch: float) -> bool:
        ttl = self.positive_ttl(record, now_epoch)
        if ttl <= 0 or not self._usable():
            return False
        return await self._set(short_code, record.encode(), ttl, nx=True)

    async def populate_negative(self, short_code: str, marker: str = NOT_FOUND) -> bool:
        if not self._settings.negative_cache_enabled or not self._usable():
            return False
        return await self._set(short_code, marker, self._settings.negative_cache_ttl_seconds, nx=True)

    # -- write-through from writers (always overwrites) --------------------
    async def write_record(self, short_code: str, record: CacheRecord, now_epoch: float) -> bool:
        ttl = self.positive_ttl(record, now_epoch)
        if not self._usable():
            return False
        if ttl <= 0:
            return await self.write_tombstone(short_code, GONE)
        return await self._set(short_code, record.encode(), ttl, nx=False)

    async def write_tombstone(self, short_code: str, marker: str = NOT_FOUND) -> bool:
        if not self._usable():
            return False
        return await self._set(short_code, marker, self._settings.negative_cache_ttl_seconds, nx=False)

    async def delete(self, short_code: str) -> bool:
        if not self._enabled:
            return False
        try:
            await self._redis.delete(cache_key(short_code))
            return True
        except _REDIS_ERRORS as exc:
            self._failed("delete", exc)
            return False

    async def delete_many(self, short_codes: list[str]) -> int:
        if not self._enabled or not short_codes:
            return 0
        try:
            return int(await self._redis.delete(*[cache_key(c) for c in short_codes]))
        except _REDIS_ERRORS as exc:
            self._failed("delete_many", exc)
            return 0

    async def _set(self, short_code: str, value: str, ttl: int, *, nx: bool) -> bool:
        try:
            return bool(await self._redis.set(cache_key(short_code), value, ex=ttl, nx=nx))
        except _REDIS_ERRORS as exc:
            self._failed("set", exc)
            return False
