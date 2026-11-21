"""Producer side of the click pipeline: build a compact event and ``XADD`` it.

The redirect path may await this, but only with a strict timeout, and a failure
here must never fail an otherwise valid redirect.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.core.config import Settings
from app.core.security import hash_ip
from app.monitoring.metrics import AppMetrics
from app.services.click_events import ClickEvent
from app.services.geoip import GeoResolver
from app.services.resilience import CircuitBreaker

logger = logging.getLogger(__name__)
_LOG_INTERVAL_SECONDS = 5.0


class ClickIngestor:
    def __init__(
        self, redis: Redis, settings: Settings, metrics: AppMetrics, geo: GeoResolver | None = None
    ) -> None:
        self._redis = redis
        self._settings = settings
        self._metrics = metrics
        self._geo = geo
        self._secret = settings.ip_hash_secret.get_secret_value().encode()
        self._timeout = settings.stream_enqueue_timeout_ms / 1000
        self._breaker = CircuitBreaker(settings.redis_breaker_failures, settings.redis_breaker_cooldown_seconds)
        self._last_log = 0.0

    def build_event(self, short_code: str, *, user_agent: str | None, ip: str, referrer: str | None) -> ClickEvent:
        return ClickEvent(
            event_id=uuid.uuid4().hex,
            short_code=short_code,
            ts_ms=int(time.time() * 1000),
            user_agent=(user_agent or "")[:512],
            ip_hash=hash_ip(self._secret, ip),  # raw IP stops here
            referrer=(referrer or "")[:512],
            country=self._geo.country(ip) if self._geo else None,
        )

    async def enqueue(self, short_code: str, *, user_agent: str | None, ip: str, referrer: str | None) -> bool:
        """Returns True if the event reached the stream. Never raises."""
        if not self._settings.analytics_enabled:
            return False
        if self._breaker.is_open:
            self._metrics.stream_enqueue_failures.labels(reason="circuit_open").inc()
            return False
        try:
            event = self.build_event(short_code, user_agent=user_agent, ip=ip, referrer=referrer)
            await asyncio.wait_for(
                self._redis.xadd(
                    self._settings.click_stream_name,
                    event.to_fields(),
                    maxlen=self._settings.click_stream_maxlen,
                    approximate=True,
                ),
                timeout=self._timeout,
            )
        except asyncio.TimeoutError:
            # A timeout can be local event-loop delay on a saturated API process, not a Redis fault. Drop this
            # one event (cost per request is bounded by the timeout) but do not trip the breaker: tripping it
            # turned a handful of timeouts into seconds of dropped clicks under load (see docs/BENCHMARKS.md).
            return self._failed("timeout", None, trip_breaker=False)
        except (RedisError, OSError) as exc:
            return self._failed("redis_error", exc)
        except Exception as exc:  # noqa: BLE001 - analytics must never break a redirect
            return self._failed("unexpected", exc)
        self._breaker.record_success()
        self._metrics.stream_enqueued.inc()
        return True

    def _failed(self, reason: str, exc: BaseException | None, *, trip_breaker: bool = True) -> bool:
        if trip_breaker:
            self._breaker.record_failure()
        self._metrics.stream_enqueue_failures.labels(reason=reason).inc()
        now = time.monotonic()
        if now - self._last_log >= _LOG_INTERVAL_SECONDS:  # avoid log floods during an outage
            self._last_log = now
            logger.warning(
                "click enqueue failed",
                extra={"reason": reason, "error_type": type(exc).__name__ if exc else None},
            )
        return False
