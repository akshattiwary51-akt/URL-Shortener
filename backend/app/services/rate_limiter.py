"""Distributed sliding-window-log rate limiter (Redis ZSET + Lua).

The entire check-and-record sequence runs inside one Lua script, which Redis
executes atomically, so concurrent requests - from any number of API processes -
cannot oversubscribe a limit. Time comes from the Redis server (``TIME``), never
from clients, so clock skew between API nodes is irrelevant.
"""

from __future__ import annotations

import asyncio
import logging
import math
import uuid
from dataclasses import dataclass

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.core.config import Settings
from app.core.exceptions import ServiceUnavailable
from app.monitoring.metrics import AppMetrics
from app.services.resilience import CircuitBreaker

logger = logging.getLogger(__name__)

# KEYS[1] = window key
# ARGV[1] = limit, ARGV[2] = window in ms, ARGV[3] = unique member for this request
# Returns {allowed (0/1), remaining, retry_after_ms}
SLIDING_WINDOW_LUA = """
local key = KEYS[1]
local limit = tonumber(ARGV[1])
local window_ms = tonumber(ARGV[2])
local member = ARGV[3]

local t = redis.call('TIME')
local now_ms = tonumber(t[1]) * 1000 + math.floor(tonumber(t[2]) / 1000)

redis.call('ZREMRANGEBYSCORE', key, '-inf', now_ms - window_ms)
local count = redis.call('ZCARD', key)

if count < limit then
  redis.call('ZADD', key, now_ms, member)
  redis.call('PEXPIRE', key, window_ms)
  return {1, limit - count - 1, 0}
end

local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
local retry_ms = window_ms - (now_ms - tonumber(oldest[2]))
if retry_ms < 1 then retry_ms = 1 end
redis.call('PEXPIRE', key, window_ms)
return {0, 0, retry_ms}
"""


@dataclass(frozen=True, slots=True)
class RateLimitResult:
    allowed: bool
    limit: int
    remaining: int
    retry_after: int = 0  # whole seconds, >= 1 when rejected
    degraded: bool = False  # True when Redis was unavailable and we failed open

    def headers(self) -> dict[str, str]:
        h = {"X-RateLimit-Limit": str(self.limit), "X-RateLimit-Remaining": str(self.remaining)}
        if not self.allowed:
            h["Retry-After"] = str(self.retry_after)
        return h


class SlidingWindowRateLimiter:
    def __init__(self, redis: Redis, settings: Settings, metrics: AppMetrics) -> None:
        self._redis = redis
        self._settings = settings
        self._metrics = metrics
        self._script = redis.register_script(SLIDING_WINDOW_LUA)
        self._breaker = CircuitBreaker(settings.redis_breaker_failures, settings.redis_breaker_cooldown_seconds)

    @staticmethod
    def key(category: str, identifier: str) -> str:
        return f"rl:{category}:{identifier}"

    async def check(
        self, category: str, identifier: str, limit: int, window_seconds: int | None = None
    ) -> RateLimitResult:
        if not self._settings.rate_limit_enabled:
            return RateLimitResult(True, limit, limit)
        window_ms = (window_seconds or self._settings.rate_limit_window_seconds) * 1000
        if self._breaker.is_open:
            return self._on_failure(limit, "circuit_open")
        try:
            allowed, remaining, retry_ms = await self._script(
                keys=[self.key(category, identifier)], args=[limit, window_ms, uuid.uuid4().hex]
            )
        except (RedisError, OSError, asyncio.TimeoutError) as exc:
            self._breaker.record_failure()
            logger.warning("rate limiter redis error", extra={"error_type": type(exc).__name__})
            return self._on_failure(limit, type(exc).__name__)
        self._breaker.record_success()
        if allowed:
            return RateLimitResult(True, limit, int(remaining))
        self._metrics.rate_limit_rejections.labels(category=category).inc()
        return RateLimitResult(False, limit, 0, max(1, math.ceil(int(retry_ms) / 1000)))

    def _on_failure(self, limit: int, reason: str) -> RateLimitResult:
        mode = "fail_open" if self._settings.rate_limit_fail_open else "fail_closed"
        self._metrics.rate_limit_errors.labels(mode=mode).inc()
        if self._settings.rate_limit_fail_open:
            return RateLimitResult(True, limit, limit, degraded=True)
        raise ServiceUnavailable("Rate limiting is temporarily unavailable.")
