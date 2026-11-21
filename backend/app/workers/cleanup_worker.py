"""Maintenance worker. Everything runs in bounded batches so no transaction is ever large.

* evicts cache entries of recently expired URLs (belt and braces: TTLs are already capped at expiry)
* prunes ``click_events`` dedupe rows older than ``CLICK_EVENT_RETENTION_DAYS``
* optionally hard-deletes URLs expired for more than ``EXPIRED_URL_PURGE_DAYS`` (0 = never; analytics
  are preserved by default)

Run: ``python -m app.workers.cleanup_worker``
"""

from __future__ import annotations

import asyncio
import logging
import signal
from datetime import datetime, timedelta, timezone

from prometheus_client import start_http_server
from redis.asyncio import Redis
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.context import create_redis
from app.core.config import Settings, get_settings
from app.core.logging import configure_logging
from app.database.connection import create_engine, create_sessionmaker
from app.database.models import Url
from app.monitoring.metrics import AppMetrics, WorkerMetrics
from app.services.cache_service import GONE, CacheService

logger = logging.getLogger(__name__)

_PRUNE_EVENTS_SQL = text(
    """
    DELETE FROM click_events WHERE event_id IN (
        SELECT event_id FROM click_events WHERE timestamp < :cutoff LIMIT :n
    )
    """
)


class CleanupWorker:
    def __init__(
        self,
        settings: Settings,
        sessionmaker: async_sessionmaker[AsyncSession],
        cache: CacheService,
        metrics: WorkerMetrics | None = None,
    ) -> None:
        self._s = settings
        self._sessionmaker = sessionmaker
        self._cache = cache
        self.metrics = metrics or WorkerMetrics()

    async def evict_expired_from_cache(self, now: datetime | None = None) -> int:
        """Keyset-paginated scan of URLs that expired within the lookback window."""
        now = now or datetime.now(timezone.utc)
        since = now - timedelta(seconds=self._s.cleanup_cache_lookback_seconds)
        last_id, evicted = 0, 0
        while True:
            async with self._sessionmaker() as session:
                rows = (
                    await session.execute(
                        select(Url.id, Url.short_code)
                        .where(Url.expires_at.is_not(None), Url.expires_at > since, Url.expires_at <= now, Url.id > last_id)
                        .order_by(Url.id)
                        .limit(self._s.cleanup_batch_size)
                    )
                ).all()
            if not rows:
                break
            last_id = rows[-1][0]
            codes = [r[1] for r in rows]
            for code in codes:  # replace with a short-lived 410 tombstone rather than a bare delete
                await self._cache.write_tombstone(code, GONE)
            evicted += len(codes)
        self.metrics.cleanup_deleted.labels(kind="cache_entries").inc(evicted)
        return evicted

    async def prune_click_events(self, now: datetime | None = None) -> int:
        cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=self._s.click_event_retention_days)
        total = 0
        while True:
            async with self._sessionmaker.begin() as session:
                result = await session.execute(_PRUNE_EVENTS_SQL, {"cutoff": cutoff, "n": self._s.cleanup_batch_size})
            total += result.rowcount
            if result.rowcount < self._s.cleanup_batch_size:
                break
            await asyncio.sleep(0)  # yield between batches
        self.metrics.cleanup_deleted.labels(kind="click_events").inc(total)
        return total

    async def purge_expired_urls(self, now: datetime | None = None) -> int:
        days = self._s.expired_url_purge_days
        if days <= 0:
            return 0
        cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=days)
        total = 0
        while True:
            async with self._sessionmaker.begin() as session:
                ids = list(
                    (
                        await session.execute(
                            select(Url.id).where(Url.expires_at.is_not(None), Url.expires_at < cutoff).limit(self._s.cleanup_batch_size)
                        )
                    ).scalars()
                )
                if ids:
                    codes = list((await session.execute(select(Url.short_code).where(Url.id.in_(ids)))).scalars())
                    await session.execute(delete(Url).where(Url.id.in_(ids)))  # analytics rows cascade
            if not ids:
                break
            for code in codes:
                await self._cache.delete(code)
            total += len(ids)
        self.metrics.cleanup_deleted.labels(kind="urls").inc(total)
        return total

    async def run_once(self) -> dict[str, int]:
        return {
            "cache_evicted": await self.evict_expired_from_cache(),
            "events_pruned": await self.prune_click_events(),
            "urls_purged": await self.purge_expired_urls(),
        }

    async def run(self, stop: asyncio.Event) -> None:
        logger.info("cleanup worker started")
        while not stop.is_set():
            try:
                result = await self.run_once()
                if any(result.values()):
                    logger.info("cleanup pass finished", extra=result)
            except Exception:  # noqa: BLE001 - keep the loop alive; the next pass retries
                logger.exception("cleanup pass failed")
            try:
                await asyncio.wait_for(stop.wait(), timeout=self._s.cleanup_interval_seconds)
            except asyncio.TimeoutError:
                pass
        logger.info("cleanup worker stopped")


async def amain() -> None:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_json)
    engine = create_engine(settings)
    redis = create_redis(settings.cache_url, settings)
    cache = CacheService(redis, settings, AppMetrics())
    metrics = WorkerMetrics()
    worker = CleanupWorker(settings, create_sessionmaker(engine), cache, metrics)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    start_http_server(settings.worker_metrics_port + 1, registry=metrics.registry)
    try:
        await worker.run(stop)
    finally:
        await redis.aclose()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(amain())
