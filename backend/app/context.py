"""Application context: the process-wide collection of clients and services."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from starlette.requests import HTTPConnection

from app.core.config import Settings
from app.core.net import Network, parse_networks, resolve_client_ip
from app.database.connection import create_engine, create_sessionmaker
from app.monitoring.metrics import AppMetrics, instrument_engine
from app.services.analytics_service import AnalyticsService
from app.services.auth_service import AuthService
from app.services.cache_service import CacheService
from app.services.click_ingest import ClickIngestor
from app.services.geoip import GeoResolver, build_geo_resolver
from app.services.id_allocator import RangeAllocator
from app.services.rate_limiter import SlidingWindowRateLimiter
from app.services.redirect_service import RedirectService
from app.services.url_service import UrlService

logger = logging.getLogger(__name__)


def create_redis(url: str, settings: Settings) -> Redis:
    timeout = settings.redis_socket_timeout_ms / 1000
    return Redis.from_url(
        url,
        decode_responses=True,
        socket_timeout=timeout,
        socket_connect_timeout=timeout,
        max_connections=settings.redis_max_connections,
        health_check_interval=30,
    )


@dataclass
class AppContext:
    settings: Settings
    metrics: AppMetrics
    engine: AsyncEngine
    sessionmaker: async_sessionmaker[AsyncSession]
    # Three isolated Redis clients (separate pools) so one concern cannot starve another.
    cache_redis: Redis
    ratelimit_redis: Redis
    stream_redis: Redis
    allocator: RangeAllocator
    cache: CacheService
    rate_limiter: SlidingWindowRateLimiter
    ingestor: ClickIngestor
    redirect: RedirectService
    urls: UrlService
    analytics: AnalyticsService
    auth: AuthService
    trusted_networks: list[Network]
    geo: GeoResolver | None

    def client_ip(self, conn: HTTPConnection) -> str:
        peer = conn.client.host if conn.client else None
        xff = conn.headers.get("x-forwarded-for") if self.trusted_networks else None
        return resolve_client_ip(peer, xff, self.trusted_networks)

    async def close(self) -> None:
        for client in (self.cache_redis, self.ratelimit_redis, self.stream_redis):
            try:
                await client.aclose()
            except Exception:  # noqa: BLE001
                logger.exception("error closing redis client")
        if self.geo:
            self.geo.close()
        await self.engine.dispose()


def build_context(settings: Settings, metrics: AppMetrics | None = None) -> AppContext:
    metrics = metrics or AppMetrics()
    engine = create_engine(settings)
    instrument_engine(engine, metrics)
    sessionmaker = create_sessionmaker(engine)

    cache_redis = create_redis(settings.cache_url, settings)
    ratelimit_redis = create_redis(settings.ratelimit_url, settings)
    stream_redis = create_redis(settings.stream_url, settings)

    allocator = RangeAllocator(sessionmaker, settings.id_allocator_name, settings.id_range_size)
    cache = CacheService(cache_redis, settings, metrics)
    geo = build_geo_resolver(settings.geoip_db_path)
    return AppContext(
        settings=settings,
        metrics=metrics,
        engine=engine,
        sessionmaker=sessionmaker,
        cache_redis=cache_redis,
        ratelimit_redis=ratelimit_redis,
        stream_redis=stream_redis,
        allocator=allocator,
        cache=cache,
        rate_limiter=SlidingWindowRateLimiter(ratelimit_redis, settings, metrics),
        ingestor=ClickIngestor(stream_redis, settings, metrics, geo),
        redirect=RedirectService(settings, sessionmaker, cache),
        urls=UrlService(settings, allocator, cache),
        analytics=AnalyticsService(),
        auth=AuthService(settings),
        trusted_networks=parse_networks(settings.trusted_proxies),
        geo=geo,
    )
