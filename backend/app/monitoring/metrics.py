"""Prometheus metrics. One registry per app/worker instance (avoids duplicate registration in tests)."""

from __future__ import annotations

import time

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine

_FAST_BUCKETS = (0.0005, 0.001, 0.002, 0.003, 0.005, 0.0075, 0.01, 0.015, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5)


class AppMetrics:
    def __init__(self, registry: CollectorRegistry | None = None) -> None:
        self.registry = registry or CollectorRegistry()
        r = self.registry

        # Redirects
        self.redirect_requests = Counter("redirect_requests_total", "All redirect requests.", registry=r)
        self.redirect_success = Counter("redirect_success_total", "Redirects answered with 302.", registry=r)
        self.redirect_errors = Counter(
            "redirect_errors_total", "Redirect requests not answered with 302.", ["reason"], registry=r
        )
        self.redirect_latency = Histogram(
            "redirect_latency_seconds", "Handler latency of GET /{short_code}.", buckets=_FAST_BUCKETS, registry=r
        )

        # Cache
        self.cache_hits = Counter("cache_hits_total", "Positive cache hits.", registry=r)
        self.cache_misses = Counter("cache_misses_total", "Cache misses.", registry=r)
        self.cache_negative_hits = Counter("cache_negative_hits_total", "Negative-cache hits.", registry=r)
        self.cache_errors = Counter("cache_errors_total", "Redis errors on the cache client.", registry=r)
        self.cache_corrupt = Counter("cache_corrupt_total", "Malformed cache values discarded.", registry=r)
        self.cache_hit_ratio = Gauge(
            "cache_hit_ratio", "(positive + negative hits) / all lookups since process start.", registry=r
        )
        self.cache_hit_ratio.set_function(self._hit_ratio)

        # Rate limiting
        self.rate_limit_rejections = Counter(
            "rate_limit_rejections_total", "Requests rejected with 429.", ["category"], registry=r
        )
        self.rate_limit_errors = Counter(
            "rate_limit_errors_total", "Rate limiter Redis failures.", ["mode"], registry=r
        )

        # Creation
        self.url_creations = Counter("url_creation_requests_total", "URL creation attempts.", ["result"], registry=r)

        # Analytics ingestion (producer side)
        self.stream_enqueued = Counter("click_events_enqueued_total", "Click events written to the stream.", registry=r)
        self.stream_enqueue_failures = Counter(
            "stream_enqueue_failures_total", "Click events that could not be enqueued.", ["reason"], registry=r
        )

        # HTTP / DB
        self.http_latency = Histogram(
            "http_request_duration_seconds",
            "HTTP request duration by route template.",
            ["method", "route", "status"],
            buckets=_FAST_BUCKETS,
            registry=r,
        )
        self.db_query_latency = Histogram(
            "db_query_duration_seconds", "PostgreSQL statement latency.", buckets=_FAST_BUCKETS, registry=r
        )

    def _hit_ratio(self) -> float:
        hits = self.cache_hits._value.get() + self.cache_negative_hits._value.get()
        total = hits + self.cache_misses._value.get()
        return hits / total if total else 0.0


class WorkerMetrics:
    def __init__(self, registry: CollectorRegistry | None = None) -> None:
        self.registry = registry or CollectorRegistry()
        r = self.registry
        self.events_processed = Counter("worker_events_processed_total", "New events aggregated.", registry=r)
        self.events_duplicate = Counter("worker_events_duplicate_total", "Duplicate events skipped.", registry=r)
        self.events_dropped = Counter(
            "worker_events_dropped_total", "Events dropped (unknown URL or malformed).", ["reason"], registry=r
        )
        self.batches = Counter("worker_batches_total", "Batches committed.", registry=r)
        self.batch_failures = Counter("worker_batch_failures_total", "Batches that failed to commit.", registry=r)
        self.batch_duration = Histogram(
            "worker_batch_duration_seconds",
            "DB transaction time per batch.",
            buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10),
            registry=r,
        )
        self.reclaimed = Counter("worker_reclaimed_events_total", "Events recovered via XAUTOCLAIM.", registry=r)
        self.stream_lag = Gauge("worker_stream_lag", "Entries not yet delivered to the group.", registry=r)
        self.stream_pending = Gauge("worker_stream_pending", "Delivered but unacknowledged entries.", registry=r)
        self.stream_length = Gauge("worker_stream_length", "Current stream length.", registry=r)
        self.cleanup_deleted = Counter("cleanup_deleted_total", "Rows removed by cleanup.", ["kind"], registry=r)


def instrument_engine(engine: AsyncEngine, metrics: AppMetrics) -> None:
    """Record per-statement latency for every query issued through ``engine``."""
    sync_engine = engine.sync_engine

    @event.listens_for(sync_engine, "before_cursor_execute")
    def _before(conn, cursor, statement, parameters, context, executemany):  # type: ignore[no-untyped-def]
        conn.info["_q_start"] = time.perf_counter()

    @event.listens_for(sync_engine, "after_cursor_execute")
    def _after(conn, cursor, statement, parameters, context, executemany):  # type: ignore[no-untyped-def]
        start = conn.info.pop("_q_start", None)
        if start is not None:
            metrics.db_query_latency.observe(time.perf_counter() - start)
