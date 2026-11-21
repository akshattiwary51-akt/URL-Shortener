"""Click analytics worker: Redis Streams consumer group -> batched PostgreSQL aggregation.

Delivery semantics: at-least-once from Redis, made *effectively exactly-once* by
the database. Within one transaction the worker

1. inserts the batch into ``click_events`` with ``ON CONFLICT (event_id) DO NOTHING
   RETURNING event_id`` - only events that were genuinely new come back, and
2. applies aggregate UPSERTs computed from **only those new events**.

A redelivered event therefore inserts nothing and contributes nothing, and a
crash between COMMIT and XACK is harmless. Messages are acknowledged strictly
after the transaction commits.

Run: ``python -m app.workers.click_worker``
"""

from __future__ import annotations

import asyncio
import logging
import signal
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone

from prometheus_client import start_http_server
from redis.asyncio import Redis
from redis.exceptions import RedisError, ResponseError
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings, get_settings
from app.core.exceptions import is_db_unavailable
from app.core.logging import configure_logging
from app.database.connection import create_engine, create_sessionmaker
from app.database.models import (
    BrowserAnalytics,
    ClickEvent as ClickEventRow,
    CountryAnalytics,
    DailyAnalytics,
    DeviceAnalytics,
    OsAnalytics,
    ReferrerAnalytics,
    Url,
)
from app.monitoring.metrics import WorkerMetrics
from app.services.click_events import ClickEvent, MalformedEvent
from app.services.event_parsing import normalize_referrer, parse_user_agent

logger = logging.getLogger(__name__)

Entry = tuple[str, dict[str, str]]
_DEAD_LETTER_MAXLEN = 10_000
_BACKOFF_SECONDS = 1.0

_UPDATE_TOTALS = text(
    """
    UPDATE urls SET total_clicks = urls.total_clicks + v.n
    FROM (SELECT unnest(CAST(:codes AS text[])) AS code, unnest(CAST(:counts AS bigint[])) AS n) AS v
    WHERE urls.short_code = v.code
    """
)


@dataclass(slots=True)
class BatchStats:
    new: int = 0
    duplicates: int = 0
    unknown_url: int = 0


class ClickWorker:
    def __init__(
        self,
        settings: Settings,
        sessionmaker: async_sessionmaker[AsyncSession],
        redis: Redis,
        metrics: WorkerMetrics | None = None,
        consumer_name: str | None = None,
    ) -> None:
        self._s = settings
        self._sessionmaker = sessionmaker
        self._redis = redis
        self.metrics = metrics or WorkerMetrics()
        self.consumer = consumer_name or settings.worker_consumer_name
        self._stream = settings.click_stream_name
        self._group = settings.worker_group

    # ------------------------------------------------------------------ setup
    async def ensure_group(self) -> None:
        """Create the group from the beginning of the stream (``0``) so nothing enqueued before
        the first worker started is lost."""
        try:
            await self._redis.xgroup_create(self._stream, self._group, id="0", mkstream=True)
        except ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    # ------------------------------------------------------------- reading
    async def read_new(self, count: int, block_ms: int) -> list[Entry]:
        resp = await self._redis.xreadgroup(
            self._group, self.consumer, {self._stream: ">"}, count=count, block=max(block_ms, 1)
        )
        return [(mid, fields) for _, msgs in resp for mid, fields in msgs]

    async def reclaim_pending(self) -> int:
        """Take over entries another (crashed) consumer left unacknowledged for too long."""
        total = 0
        start = "0-0"
        while True:
            resp = await self._redis.xautoclaim(
                self._stream,
                self._group,
                self.consumer,
                min_idle_time=self._s.worker_claim_idle_ms,
                start_id=start,
                count=self._s.worker_batch_size,
            )
            next_start, messages = resp[0], resp[1]
            entries = [(mid, fields) for mid, fields in messages if fields is not None]
            # Entries trimmed from the stream while pending come back with no fields: just ack them.
            vanished = [mid for mid, fields in messages if fields is None]
            if vanished:
                await self._redis.xack(self._stream, self._group, *vanished)
            if entries:
                total += len(entries)
                self.metrics.reclaimed.inc(len(entries))
                logger.info("reclaimed pending events", extra={"count": len(entries)})
                await self.process_entries(entries)
            if next_start in ("0-0", b"0-0") or not messages:
                return total
            start = next_start

    # ----------------------------------------------------------- processing
    async def process_entries(self, entries: list[Entry]) -> BatchStats:
        """Persist a batch, then acknowledge. Raises if the DB/Redis is unavailable (entries stay pending)."""
        stats = BatchStats()
        if not entries:
            return stats
        valid: list[tuple[str, ClickEvent]] = []
        for msg_id, fields in entries:
            try:
                valid.append((msg_id, ClickEvent.from_fields(fields)))
            except MalformedEvent as exc:
                await self._dead_letter(msg_id, fields, f"malformed: {exc}")
                self.metrics.events_dropped.labels(reason="malformed").inc()

        if valid:
            started = time.perf_counter()
            try:
                stats = await self._write_batch([ev for _, ev in valid])
            except Exception as exc:  # noqa: BLE001
                if is_db_unavailable(exc):
                    self.metrics.batch_failures.inc()
                    raise
                logger.exception("batch failed; retrying events one by one to isolate poison messages")
                self.metrics.batch_failures.inc()
                stats = await self._process_individually(valid)
            else:
                self.metrics.batch_duration.observe(time.perf_counter() - started)
                self.metrics.batches.inc()
            await self._ack([mid for mid, _ in valid])

        self.metrics.events_processed.inc(stats.new)
        self.metrics.events_duplicate.inc(stats.duplicates)
        if stats.unknown_url:
            self.metrics.events_dropped.labels(reason="unknown_url").inc(stats.unknown_url)
        return stats

    async def _process_individually(self, valid: list[tuple[str, ClickEvent]]) -> BatchStats:
        total = BatchStats()
        survivors: list[tuple[str, ClickEvent]] = []
        for msg_id, ev in valid:
            try:
                s = await self._write_batch([ev])
            except Exception as exc:  # noqa: BLE001
                if is_db_unavailable(exc):
                    raise
                logger.exception("event rejected by database", extra={"event_id": ev.event_id})
                await self._dead_letter(msg_id, ev.to_fields(), "db_error")
                self.metrics.events_dropped.labels(reason="db_error").inc()
                continue
            survivors.append((msg_id, ev))
            total.new += s.new
            total.duplicates += s.duplicates
            total.unknown_url += s.unknown_url
        return total

    async def _write_batch(self, events: list[ClickEvent]) -> BatchStats:
        """One transaction: dedupe-insert + aggregate upserts. Atomic, so a failure leaves nothing behind."""
        unique: dict[str, ClickEvent] = {}
        for ev in events:  # the same event may appear twice within a single batch
            unique.setdefault(ev.event_id, ev)
        in_batch_dupes = len(events) - len(unique)

        async with self._sessionmaker.begin() as session:
            codes = sorted({e.short_code for e in unique.values()})
            # FOR NO KEY UPDATE in sorted order: serialises concurrent workers touching the same
            # URL (no deadlocks), and blocks a concurrent DELETE of the URL until we commit.
            existing = set(
                (
                    await session.execute(
                        select(Url.short_code)
                        .where(Url.short_code.in_(codes))
                        .order_by(Url.short_code)
                        .with_for_update(key_share=True)
                    )
                ).scalars()
            )
            known = [e for e in unique.values() if e.short_code in existing]
            stats = BatchStats(unknown_url=len(unique) - len(known), duplicates=in_batch_dupes)
            if not known:
                return stats

            store = self._s.store_event_details
            rows = [
                {
                    "event_id": e.event_id,
                    "short_code": e.short_code,
                    "timestamp": datetime.fromtimestamp(e.ts_ms / 1000, tz=timezone.utc),
                    "user_agent": e.user_agent or None if store else None,
                    "ip_hash": e.ip_hash or None if store else None,
                    "referrer": e.referrer or None if store else None,
                }
                for e in sorted(known, key=lambda e: e.event_id)
            ]
            inserted = set(
                (
                    await session.execute(
                        pg_insert(ClickEventRow)
                        .values(rows)
                        .on_conflict_do_nothing(index_elements=[ClickEventRow.event_id])
                        .returning(ClickEventRow.event_id)
                    )
                ).scalars()
            )
            fresh = [e for e in known if e.event_id in inserted]  # ONLY these are aggregated
            stats.new = len(fresh)
            stats.duplicates += len(known) - len(fresh)
            if fresh:
                await self._aggregate(session, fresh)
        return stats

    async def _aggregate(self, session: AsyncSession, events: list[ClickEvent]) -> None:
        per_url: Counter[str] = Counter()
        daily: Counter[tuple[str, object]] = Counter()
        browsers: Counter[tuple[str, str]] = Counter()
        systems: Counter[tuple[str, str]] = Counter()
        devices: Counter[tuple[str, str]] = Counter()
        countries: Counter[tuple[str, str]] = Counter()
        referrers: Counter[tuple[str, str]] = Counter()
        for e in events:
            ua = parse_user_agent(e.user_agent)
            day = datetime.fromtimestamp(e.ts_ms / 1000, tz=timezone.utc).date()
            per_url[e.short_code] += 1
            daily[(e.short_code, day)] += 1
            browsers[(e.short_code, ua.browser)] += 1
            systems[(e.short_code, ua.os)] += 1
            devices[(e.short_code, ua.device)] += 1
            referrers[(e.short_code, normalize_referrer(e.referrer))] += 1
            if e.country:
                countries[(e.short_code, e.country)] += 1

        # Keys are sorted everywhere so concurrent workers always lock rows in the same order.
        codes = sorted(per_url)
        await session.execute(_UPDATE_TOTALS, {"codes": codes, "counts": [per_url[c] for c in codes]})
        await _upsert(session, DailyAnalytics, (DailyAnalytics.short_code, DailyAnalytics.date), daily)
        await _upsert(session, BrowserAnalytics, (BrowserAnalytics.short_code, BrowserAnalytics.browser_name), browsers)
        await _upsert(session, OsAnalytics, (OsAnalytics.short_code, OsAnalytics.os_name), systems)
        await _upsert(session, DeviceAnalytics, (DeviceAnalytics.short_code, DeviceAnalytics.device_type), devices)
        await _upsert(session, CountryAnalytics, (CountryAnalytics.short_code, CountryAnalytics.country_code), countries)
        await _upsert(session, ReferrerAnalytics, (ReferrerAnalytics.short_code, ReferrerAnalytics.referrer), referrers)

    # --------------------------------------------------------------- redis
    async def _ack(self, ids: list[str]) -> None:
        if not ids:
            return
        pipe = self._redis.pipeline(transaction=False)
        pipe.xack(self._stream, self._group, *ids)
        pipe.xdel(self._stream, *ids)  # single consumer group: processed entries can be dropped
        await pipe.execute()

    async def _dead_letter(self, msg_id: str, fields: dict[str, str], reason: str) -> None:
        payload = {**{k: str(v)[:512] for k, v in fields.items()}, "reason": reason, "orig_id": msg_id}
        await self._redis.xadd(self._s.click_dead_letter_stream, payload, maxlen=_DEAD_LETTER_MAXLEN, approximate=True)
        await self._ack([msg_id])

    async def update_gauges(self) -> None:
        try:
            length = await self._redis.xlen(self._stream)
            self.metrics.stream_length.set(length)
            for group in await self._redis.xinfo_groups(self._stream):
                if group.get("name") == self._group:
                    self.metrics.stream_pending.set(group.get("pending", 0))
                    lag = group.get("lag")
                    self.metrics.stream_lag.set(lag if lag is not None else 0)
        except (RedisError, OSError):
            logger.debug("could not refresh stream gauges", exc_info=True)

    # ----------------------------------------------------------------- loop
    async def run(self, stop: asyncio.Event) -> None:
        """Main loop: read -> buffer -> flush at 500 events or every ~5 s; reclaim crashed consumers' work."""
        s = self._s
        buffer: list[Entry] = []
        first_buffered_at = 0.0
        last_claim = 0.0
        await self._retry_until_ready(self.ensure_group, stop)
        logger.info("click worker started", extra={"consumer": self.consumer, "stream": self._stream})

        while not stop.is_set() or buffer:
            try:
                now = time.monotonic()
                if now - last_claim >= s.worker_claim_interval_seconds:
                    last_claim = now
                    await self.reclaim_pending()
                    await self.update_gauges()

                if not stop.is_set():
                    remaining = s.worker_flush_interval_seconds - (now - first_buffered_at) if buffer else s.worker_flush_interval_seconds
                    entries = await self.read_new(
                        s.worker_batch_size - len(buffer), int(max(remaining, 0.05) * 1000)
                    )
                    if entries:
                        if not buffer:
                            first_buffered_at = time.monotonic()
                        buffer.extend(entries)

                due = buffer and (
                    len(buffer) >= s.worker_batch_size
                    or stop.is_set()
                    or time.monotonic() - first_buffered_at >= s.worker_flush_interval_seconds
                )
                if due:
                    await self.process_entries(buffer)
                    buffer = []
            except (RedisError, OSError, asyncio.TimeoutError) as exc:
                logger.warning("redis error in worker loop", extra={"error_type": type(exc).__name__})
                if stop.is_set():
                    break
                await self._sleep(stop, _BACKOFF_SECONDS)
            except Exception as exc:  # noqa: BLE001
                if not is_db_unavailable(exc):
                    logger.exception("unexpected worker error")
                else:
                    logger.warning("database unavailable; events remain pending", extra={"error_type": type(exc).__name__})
                if stop.is_set():
                    break
                await self._sleep(stop, _BACKOFF_SECONDS)  # buffer is kept and retried
        logger.info("click worker stopped", extra={"unflushed": len(buffer)})

    @staticmethod
    async def _sleep(stop: asyncio.Event, seconds: float) -> None:
        try:
            await asyncio.wait_for(stop.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            pass

    async def _retry_until_ready(self, fn, stop: asyncio.Event) -> None:  # type: ignore[no-untyped-def]
        while not stop.is_set():
            try:
                await fn()
                return
            except (RedisError, OSError) as exc:
                logger.warning("waiting for redis", extra={"error_type": type(exc).__name__})
                await self._sleep(stop, 2.0)


async def _upsert(session: AsyncSession, model, key_columns, counts: Counter) -> None:  # type: ignore[no-untyped-def]
    """``INSERT … ON CONFLICT DO UPDATE SET total_clicks = total_clicks + excluded.total_clicks``."""
    if not counts:
        return
    names = [c.key for c in key_columns]
    rows = [{**dict(zip(names, key)), "total_clicks": n} for key, n in sorted(counts.items(), key=lambda kv: tuple(map(str, kv[0])))]
    stmt = pg_insert(model).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=list(key_columns), set_={"total_clicks": model.total_clicks + stmt.excluded.total_clicks}
    )
    await session.execute(stmt)


async def amain() -> None:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_json)
    engine = create_engine(settings)
    # Blocking XREADGROUP needs a socket timeout longer than the block time.
    redis = Redis.from_url(settings.stream_url, decode_responses=True, socket_timeout=settings.worker_flush_interval_seconds + 5, health_check_interval=30)
    metrics = WorkerMetrics()
    worker = ClickWorker(settings, create_sessionmaker(engine), redis, metrics)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    if settings.worker_metrics_port:
        start_http_server(settings.worker_metrics_port, registry=metrics.registry)
    try:
        await worker.run(stop)
    finally:
        await redis.aclose()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(amain())
