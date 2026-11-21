import asyncio
import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest
import redis.asyncio as aioredis
from sqlalchemy import func, select, text

from app.database.connection import create_engine, create_sessionmaker
from app.database.models import (
    BrowserAnalytics,
    ClickEvent as ClickEventRow,
    CountryAnalytics,
    DailyAnalytics,
    DeviceAnalytics,
    ReferrerAnalytics,
    Url,
)
from app.monitoring.metrics import WorkerMetrics
from app.workers.click_worker import ClickWorker
from app.workers.cleanup_worker import CleanupWorker
from tests.conftest import TEST_REDIS_URL, build_client, make_settings, register_and_login

CHROME = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
IPHONE = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
STREAM = "click_events"


def fields(code, *, event_id=None, ua=CHROME, ref="", ts=None, country=None):
    f = {
        "event_id": event_id or uuid.uuid4().hex,
        "short_code": code,
        "ts": str(ts or int(time.time() * 1000)),
        "ua": ua,
        "ip_hash": "h" * 32,
        "ref": ref,
    }
    if country:
        f["country"] = country
    return f


class Env:
    def __init__(self, **overrides):
        self.settings = make_settings(**overrides)
        self.engines = []
        self.redis_clients = []

    def worker(self, name="w1") -> ClickWorker:
        engine = create_engine(self.settings)
        redis = aioredis.from_url(TEST_REDIS_URL, decode_responses=True)
        self.engines.append(engine)
        self.redis_clients.append(redis)
        return ClickWorker(self.settings, create_sessionmaker(engine), redis, WorkerMetrics(), consumer_name=name)

    async def redis(self):
        r = aioredis.from_url(TEST_REDIS_URL, decode_responses=True)
        self.redis_clients.append(r)
        return r

    async def sql(self, query, **params):
        async with self.engines[0].connect() as c:
            return (await c.execute(text(query), params)).all()

    async def close(self):
        for r in self.redis_clients:
            await r.aclose()
        for e in self.engines:
            await e.dispose()


@pytest.fixture
async def env():
    e = Env()
    yield e
    await e.close()


async def make_url(env: Env, code="abc1234", **cols):
    engine = env.engines[0] if env.engines else create_engine(env.settings)
    if not env.engines:
        env.engines.append(engine)
    async with create_sessionmaker(engine).begin() as s:
        s.add(Url(short_code=code, original_url="https://example.com/x", **cols))


async def publish(r, entries):
    return [await r.xadd(STREAM, f) for f in entries]


async def pending(r):
    info = await r.xpending(STREAM, "analytics")
    return info["pending"]


async def test_batch_is_aggregated_into_every_table(env):
    w = env.worker()
    await w.ensure_group()
    await make_url(env)
    r = await env.redis()
    now = int(time.time() * 1000)
    await publish(r, [
        fields("abc1234", ua=CHROME, ref="https://www.google.com/search?q=x", country="IN", ts=now),
        fields("abc1234", ua=CHROME, ref="https://google.com/", country="IN", ts=now),
        fields("abc1234", ua=IPHONE, ref="", country="US", ts=now),
        fields("abc1234", ua=IPHONE, ref="https://t.co/a", ts=now - 2 * 86_400_000),
    ])
    entries = await w.read_new(500, 100)
    stats = await w.process_entries(entries)
    assert (stats.new, stats.duplicates, stats.unknown_url) == (4, 0, 0)

    async with create_sessionmaker(env.engines[0])() as s:
        assert (await s.execute(select(Url.total_clicks))).scalar_one() == 4
        browsers = dict((await s.execute(select(BrowserAnalytics.browser_name, BrowserAnalytics.total_clicks))).all())
        devices = dict((await s.execute(select(DeviceAnalytics.device_type, DeviceAnalytics.total_clicks))).all())
        referrers = dict((await s.execute(select(ReferrerAnalytics.referrer, ReferrerAnalytics.total_clicks))).all())
        countries = dict((await s.execute(select(CountryAnalytics.country_code, CountryAnalytics.total_clicks))).all())
        daily = dict((await s.execute(select(DailyAnalytics.date, DailyAnalytics.total_clicks))).all())
    assert browsers == {"Chrome": 2, "Safari": 2}
    assert devices == {"desktop": 2, "mobile": 2}
    assert referrers == {"google.com": 2, "Direct": 1, "t.co": 1}
    assert countries == {"IN": 2, "US": 1}  # only events that carry a country
    assert sum(daily.values()) == 4 and len(daily) == 2
    assert await pending(r) == 0  # acknowledged after commit
    assert await r.xlen(STREAM) == 0  # and trimmed


async def test_duplicate_delivery_never_double_counts(env):
    w = env.worker()
    await w.ensure_group()
    await make_url(env)
    r = await env.redis()
    event = fields("abc1234", event_id="dupe-event-1")
    await publish(r, [event, fields("abc1234")])
    entries = await w.read_new(500, 100)
    await w.process_entries(entries)

    # Redelivery of the very same logical events (worker retry / producer duplicate / replay).
    again = await publish(r, [event, event])
    stats = await w.process_entries([(mid, event) for mid in again])
    assert stats.new == 0 and stats.duplicates == 2
    assert (await env.sql("SELECT total_clicks FROM urls"))[0][0] == 2
    assert (await env.sql("SELECT sum(total_clicks) FROM daily_analytics"))[0][0] == 2
    assert (await env.sql("SELECT sum(total_clicks) FROM browser_analytics"))[0][0] == 2


async def test_duplicates_inside_one_batch_are_counted_once(env):
    w = env.worker()
    await w.ensure_group()
    await make_url(env)
    ev = fields("abc1234", event_id="same")
    stats = await w.process_entries([("1-0", ev), ("2-0", ev), ("3-0", ev)])
    assert stats.new == 1 and stats.duplicates == 2
    assert (await env.sql("SELECT total_clicks FROM urls"))[0][0] == 1


async def test_crash_after_commit_before_ack_is_harmless(env):
    """Worker A commits then dies before XACK. Worker B reclaims the entries: nothing is double counted."""
    a, b = env.worker("A"), env.worker("B")
    await a.ensure_group()
    await make_url(env)
    r = await env.redis()
    await publish(r, [fields("abc1234") for _ in range(10)])
    entries = await a.read_new(500, 100)
    await a._write_batch([__import__("app.services.click_events", fromlist=["ClickEvent"]).ClickEvent.from_fields(f) for _, f in entries])  # committed...
    assert await pending(r) == 10  # ...but never acknowledged (crash)
    await asyncio.sleep(0.1)  # exceed worker_claim_idle_ms (50 ms in tests)
    assert await b.reclaim_pending() == 10
    assert await pending(r) == 0
    assert (await env.sql("SELECT total_clicks FROM urls"))[0][0] == 10  # still 10, not 20


async def test_crash_before_commit_is_recovered_exactly_once(env):
    a, b = env.worker("A"), env.worker("B")
    await a.ensure_group()
    await make_url(env)
    r = await env.redis()
    await publish(r, [fields("abc1234") for _ in range(7)])
    await a.read_new(500, 100)  # delivered to A, then A "crashes" without writing anything
    assert (await env.sql("SELECT total_clicks FROM urls"))[0][0] == 0
    await asyncio.sleep(0.1)
    assert await b.reclaim_pending() == 7
    assert (await env.sql("SELECT total_clicks FROM urls"))[0][0] == 7
    assert await pending(r) == 0


async def test_entries_are_not_reclaimed_while_still_fresh():
    e = Env(worker_claim_idle_ms=60_000)
    try:
        a, b = e.worker("A"), e.worker("B")
        await a.ensure_group()
        await make_url(e)
        r = await e.redis()
        await publish(r, [fields("abc1234")])
        await a.read_new(10, 100)
        assert await b.reclaim_pending() == 0  # A may still be working on it
    finally:
        await e.close()


async def test_concurrent_workers_process_every_event_exactly_once(env):
    workers = [env.worker(f"w{i}") for i in range(4)]
    await workers[0].ensure_group()
    for code in ("aaa1111", "bbb2222", "ccc3333"):
        await make_url(env, code)
    r = await env.redis()
    total = 1200
    codes = ["aaa1111", "bbb2222", "ccc3333"]
    await publish(r, [fields(codes[i % 3], ua=CHROME if i % 2 else IPHONE) for i in range(total)])

    async def drain(w):
        n = 0
        while True:
            entries = await w.read_new(100, 50)
            if not entries:
                return n
            n += (await w.process_entries(entries)).new

    counts = await asyncio.gather(*[drain(w) for w in workers])
    assert sum(counts) == total
    rows = dict(await env.sql("SELECT short_code, total_clicks FROM urls"))
    assert rows == {"aaa1111": 400, "bbb2222": 400, "ccc3333": 400}
    assert (await env.sql("SELECT sum(total_clicks) FROM daily_analytics"))[0][0] == total
    assert (await env.sql("SELECT count(*) FROM click_events"))[0][0] == total
    assert await pending(r) == 0


async def test_malformed_events_go_to_dead_letter_and_do_not_block(env):
    w = env.worker()
    await w.ensure_group()
    await make_url(env)
    r = await env.redis()
    await r.xadd(STREAM, {"garbage": "1"})
    await r.xadd(STREAM, {"event_id": "x", "short_code": "!!", "ts": "1"})
    await publish(r, [fields("abc1234")])
    stats = await w.process_entries(await w.read_new(500, 100))
    assert stats.new == 1
    assert await r.xlen("click_events_dead") == 2
    dead = await r.xrange("click_events_dead")
    assert all("malformed" in f["reason"] for _, f in dead)
    assert await pending(r) == 0


async def test_events_for_deleted_or_unknown_urls_are_dropped_and_acked(env):
    w = env.worker()
    await w.ensure_group()
    await make_url(env)
    r = await env.redis()
    await publish(r, [fields("ghost99"), fields("abc1234")])
    stats = await w.process_entries(await w.read_new(500, 100))
    assert (stats.new, stats.unknown_url) == (1, 1)
    assert await pending(r) == 0


async def test_database_failure_leaves_events_pending_and_corrupts_nothing(env):
    w = env.worker()
    await w.ensure_group()
    await make_url(env)
    r = await env.redis()
    await publish(r, [fields("abc1234") for _ in range(5)])
    entries = await w.read_new(500, 100)

    original = w._write_batch

    async def boom(events):
        raise ConnectionRefusedError("db down")

    w._write_batch = boom  # type: ignore[assignment]
    with pytest.raises(ConnectionRefusedError):
        await w.process_entries(entries)
    assert await pending(r) == 5  # NOT acknowledged
    assert (await env.sql("SELECT total_clicks FROM urls"))[0][0] == 0

    w._write_batch = original  # type: ignore[assignment]
    await asyncio.sleep(0.1)
    await w.reclaim_pending()
    assert (await env.sql("SELECT total_clicks FROM urls"))[0][0] == 5
    assert await pending(r) == 0


async def test_failure_mid_transaction_rolls_back_everything(env):
    """If an aggregate upsert fails after the dedupe insert, the insert must roll back too."""
    w = env.worker()
    await w.ensure_group()
    await make_url(env)
    from app.services.click_events import ClickEvent

    events = [ClickEvent.from_fields(fields("abc1234")) for _ in range(3)]

    async def broken(session, evs):
        await session.execute(text("UPDATE urls SET total_clicks = total_clicks + 3"))
        raise RuntimeError("boom after partial work")

    w._aggregate = broken  # type: ignore[assignment]
    with pytest.raises(RuntimeError):
        await w._write_batch(events)
    assert (await env.sql("SELECT count(*) FROM click_events"))[0][0] == 0
    assert (await env.sql("SELECT total_clicks FROM urls"))[0][0] == 0


async def test_poison_event_is_isolated_and_the_rest_of_the_batch_survives(env):
    w = env.worker()
    await w.ensure_group()
    await make_url(env)
    r = await env.redis()
    await publish(r, [fields("abc1234") for _ in range(4)])
    entries = await w.read_new(500, 100)

    real = w._write_batch
    calls = {"n": 0}

    async def flaky(events):
        calls["n"] += 1
        if len(events) > 1 or events[0].event_id == poison:
            from sqlalchemy.exc import DataError

            raise DataError("stmt", {}, Exception("bad row"))
        return await real(events)

    poison = entries[2][1]["event_id"]
    w._write_batch = flaky  # type: ignore[assignment]
    stats = await w.process_entries(entries)
    assert stats.new == 3
    assert await r.xlen("click_events_dead") == 1
    assert await pending(r) == 0
    assert (await env.sql("SELECT total_clicks FROM urls"))[0][0] == 3


async def test_event_details_are_not_persisted_by_default_but_can_be_enabled(env):
    w = env.worker()
    await w.ensure_group()
    await make_url(env)
    await w.process_entries([("1-0", fields("abc1234", ref="https://r.example/p?q=1"))])
    row = (await env.sql("SELECT user_agent, ip_hash, referrer FROM click_events"))[0]
    assert row == (None, None, None)

    e2 = Env(store_event_details=True)
    try:
        w2 = e2.worker()
        await w2.process_entries([("2-0", fields("abc1234", ref="https://r.example/p?q=1"))])
        row = (await e2.sql("SELECT user_agent, ip_hash, referrer FROM click_events WHERE user_agent IS NOT NULL"))[0]
        assert row[0] == CHROME and row[1] == "h" * 32 and row[2] == "https://r.example/p?q=1"
    finally:
        await e2.close()


async def test_batches_are_capped_at_the_configured_size():
    e = Env(worker_batch_size=500)
    try:
        w = e.worker()
        await w.ensure_group()
        r = await e.redis()
        await publish(r, [fields("abc1234") for _ in range(1200)])
        sizes = []
        while True:
            batch = await w.read_new(w._s.worker_batch_size, 50)
            if not batch:
                break
            sizes.append(len(batch))
        assert sizes == [500, 500, 200]
    finally:
        await e.close()


async def test_run_loop_flushes_on_interval_and_shuts_down_cleanly():
    e = Env(worker_batch_size=500, worker_flush_interval_seconds=0.3, worker_claim_interval_seconds=0.2)
    try:
        w = e.worker()
        await make_url(e)
        r = await e.redis()
        stop = asyncio.Event()
        task = asyncio.create_task(w.run(stop))
        await asyncio.sleep(0.2)
        await publish(r, [fields("abc1234") for _ in range(25)])  # far below batch size: only the timer can flush
        for _ in range(40):
            await asyncio.sleep(0.1)
            if (await e.sql("SELECT total_clicks FROM urls"))[0][0] == 25:
                break
        assert (await e.sql("SELECT total_clicks FROM urls"))[0][0] == 25
        await publish(r, [fields("abc1234") for _ in range(3)])
        await asyncio.sleep(0.05)
        stop.set()  # buffered events must be flushed on shutdown
        await asyncio.wait_for(task, 5)
        assert (await e.sql("SELECT total_clicks FROM urls"))[0][0] == 28
    finally:
        await e.close()


async def test_run_loop_recovers_after_the_database_comes_back():
    e = Env(worker_flush_interval_seconds=0.2, worker_claim_interval_seconds=0.2)
    try:
        w = e.worker()
        await make_url(e)
        r = await e.redis()
        real = w._write_batch
        state = {"down": True}

        async def flaky(events):
            if state["down"]:
                raise ConnectionRefusedError("db down")
            return await real(events)

        w._write_batch = flaky  # type: ignore[assignment]
        stop = asyncio.Event()
        task = asyncio.create_task(w.run(stop))
        await publish(r, [fields("abc1234") for _ in range(6)])
        await asyncio.sleep(1.5)
        assert (await e.sql("SELECT total_clicks FROM urls"))[0][0] == 0
        state["down"] = False
        for _ in range(40):
            await asyncio.sleep(0.1)
            if (await e.sql("SELECT total_clicks FROM urls"))[0][0] == 6:
                break
        stop.set()
        await asyncio.wait_for(task, 5)
        assert (await e.sql("SELECT total_clicks FROM urls"))[0][0] == 6
        assert await pending(r) == 0
    finally:
        await e.close()


async def test_end_to_end_redirect_to_dashboard():
    """Redirect enqueues; the worker aggregates; the owner sees it in the analytics API."""
    async with build_client() as c:
        headers = await register_and_login(c)
        code = (await c.post("/api/v1/urls", json={"original_url": "https://example.com/e2e"}, headers=headers)).json()["short_code"]
        for i in range(6):
            r = await c.get(f"/{code}", headers={"User-Agent": IPHONE if i < 4 else CHROME, "Referer": "https://www.news.example/story?id=9"})
            assert r.status_code == 302
        assert c.app_ctx.metrics.stream_enqueued._value.get() == 6

        # Privacy: the raw client IP must never be in the stream.
        raw = await c.app_ctx.stream_redis.xrange(STREAM)
        assert len(raw) == 6
        assert all("198.51.100.7" not in str(f) for _, f in raw)
        assert all(len(f["ip_hash"]) == 32 for _, f in raw)
        # The redirect path did not write analytics to PostgreSQL.
        async with c.app_ctx.sessionmaker() as s:
            assert (await s.execute(select(func.count()).select_from(ClickEventRow))).scalar_one() == 0
        assert (await c.get(f"/api/v1/analytics/{code}", headers=headers)).json()["total_clicks"] == 0

        w = ClickWorker(c.app_ctx.settings, c.app_ctx.sessionmaker, c.app_ctx.stream_redis, WorkerMetrics(), "e2e")
        await w.ensure_group()
        assert (await w.process_entries(await w.read_new(500, 100))).new == 6

        overview = (await c.get(f"/api/v1/analytics/{code}", headers=headers)).json()
        assert overview["total_clicks"] == 6 and overview["clicks_today"] == 6 and overview["clicks_last_7_days"] == 6
        daily = (await c.get(f"/api/v1/analytics/{code}/daily", headers=headers)).json()
        assert daily["total"] == 6 and len(daily["points"]) == 30 and daily["points"][-1]["clicks"] == 6
        assert daily["from"] and daily["to"]
        browsers = (await c.get(f"/api/v1/analytics/{code}/browsers", headers=headers)).json()
        assert {i["name"]: i["clicks"] for i in browsers["items"]} == {"Safari": 4, "Chrome": 2}
        assert browsers["items"][0]["percentage"] == pytest.approx(66.67)
        devices = (await c.get(f"/api/v1/analytics/{code}/devices", headers=headers)).json()
        assert {i["name"]: i["clicks"] for i in devices["items"]} == {"mobile": 4, "desktop": 2}
        refs = (await c.get(f"/api/v1/analytics/{code}/referrers", headers=headers)).json()
        assert [(i["name"], i["clicks"]) for i in refs["items"]] == [("news.example", 6)]
        assert (await c.get(f"/api/v1/analytics/{code}/countries", headers=headers)).json()["items"] == []  # no GeoIP DB

        summary = (await c.get("/api/v1/dashboard/summary", headers=headers)).json()
        assert summary["total_urls"] == 1 and summary["total_clicks"] == 6 and summary["average_clicks_per_url"] == 6.0
        assert summary["top_links"][0]["short_code"] == code and summary["clicks_last_14_days"][-1]["clicks"] == 6


async def test_analytics_endpoint_validation(client, auth):
    code = (await client.post("/api/v1/urls", json={"original_url": "https://example.com/v"}, headers=auth)).json()["short_code"]
    base = f"/api/v1/analytics/{code}/daily"
    assert (await client.get(base, params={"from": "2026-02-01", "to": "2026-01-01"}, headers=auth)).status_code == 422
    assert (await client.get(base, params={"from": "2020-01-01", "to": "2026-01-01"}, headers=auth)).status_code == 422
    assert (await client.get(base, params={"from": "not-a-date"}, headers=auth)).status_code == 422
    assert (await client.get(f"/api/v1/analytics/{code}/browsers", params={"limit": 0}, headers=auth)).status_code == 422
    ok = await client.get(base, params={"from": "2026-01-01", "to": "2026-01-03"}, headers=auth)
    assert ok.status_code == 200 and [p["clicks"] for p in ok.json()["points"]] == [0, 0, 0]
    assert (await client.get("/api/v1/analytics/doesnotexist", headers=auth)).status_code == 404


async def test_redirect_survives_stream_outage_and_counts_the_failure():
    async with build_client(stream_redis_url="redis://127.0.0.1:6399/0", redis_socket_timeout_ms=100) as c:
        code = (await c.post("/api/v1/urls", json={"original_url": "https://example.com/s"})).json()["short_code"]
        for _ in range(3):
            assert (await c.get(f"/{code}")).status_code == 302
        failures = sum(
            s.value for m in c.app_ctx.metrics.stream_enqueue_failures.collect() for s in m.samples if s.name.endswith("_total")
        )
        assert failures == 3


async def test_enqueue_is_bounded_by_a_strict_timeout():
    class Hang:
        async def xadd(self, *a, **k):
            await asyncio.sleep(5)

    async with build_client(stream_enqueue_timeout_ms=30) as c:
        code = (await c.post("/api/v1/urls", json={"original_url": "https://example.com/t"})).json()["short_code"]
        c.app_ctx.ingestor._redis = Hang()  # type: ignore[assignment]
        started = time.perf_counter()
        r = await c.get(f"/{code}")
        assert r.status_code == 302 and time.perf_counter() - started < 1.0
        assert c.app_ctx.metrics.stream_enqueue_failures.labels(reason="timeout")._value.get() == 1


async def test_analytics_can_be_disabled():
    async with build_client(analytics_enabled=False) as c:
        code = (await c.post("/api/v1/urls", json={"original_url": "https://example.com/d"})).json()["short_code"]
        assert (await c.get(f"/{code}")).status_code == 302
        assert await c.app_ctx.stream_redis.xlen(STREAM) == 0


async def test_geoip_country_is_resolved_at_enqueue_without_leaking_the_ip():
    class FakeGeo:
        def country(self, ip):
            return "DE"

        def close(self):
            pass

    async with build_client() as c:
        c.app_ctx.ingestor._geo = FakeGeo()  # type: ignore[assignment]
        code = (await c.post("/api/v1/urls", json={"original_url": "https://example.com/g"})).json()["short_code"]
        await c.get(f"/{code}")
        (_, f), = await c.app_ctx.stream_redis.xrange(STREAM)
        assert f["country"] == "DE" and "198.51.100.7" not in str(f)


# ------------------------------------------------------------------ cleanup


async def test_cleanup_prunes_old_events_in_batches_and_keeps_aggregates():
    e = Env(click_event_retention_days=7, cleanup_batch_size=100)
    try:
        w = e.worker()
        await make_url(e)
        old = int((time.time() - 30 * 86_400) * 1000)
        await w.process_entries([(f"{i}-0", fields("abc1234", ts=old)) for i in range(250)])
        await w.process_entries([(f"x{i}-0", fields("abc1234")) for i in range(10)])
        assert (await e.sql("SELECT count(*) FROM click_events"))[0][0] == 260

        cache = (await build_cache(e))
        cw = CleanupWorker(e.settings, create_sessionmaker(e.engines[0]), cache)
        assert await cw.prune_click_events() == 250
        assert (await e.sql("SELECT count(*) FROM click_events"))[0][0] == 10
        assert (await e.sql("SELECT total_clicks FROM urls"))[0][0] == 260  # history preserved
    finally:
        await e.close()


async def build_cache(e: Env):
    from app.monitoring.metrics import AppMetrics
    from app.services.cache_service import CacheService

    return CacheService(await e.redis(), e.settings, AppMetrics())


async def test_cleanup_tombstones_recently_expired_urls_and_purge_is_opt_in():
    e = Env(expired_url_purge_days=0)
    try:
        w = e.worker()
        await make_url(e, "exp1234", expires_at=datetime.now(timezone.utc) - timedelta(minutes=2))
        await make_url(e, "live123")
        r = await e.redis()
        await r.set("url:exp1234", '{"u":"https://example.com/x","e":null}', ex=300)  # stale positive entry
        cache = await build_cache(e)
        cw = CleanupWorker(e.settings, create_sessionmaker(e.engines[0]), cache)
        assert await cw.evict_expired_from_cache() == 1
        assert await r.get("url:exp1234") == "__GONE__"
        assert await r.get("url:live123") is None
        assert await cw.purge_expired_urls() == 0  # disabled by default
        assert (await e.sql("SELECT count(*) FROM urls"))[0][0] == 2
        del w
    finally:
        await e.close()


async def test_cleanup_purge_deletes_long_expired_urls_in_batches():
    e = Env(expired_url_purge_days=30, cleanup_batch_size=2)
    try:
        for i in range(5):
            await make_url(e, f"old{i}abc", expires_at=datetime.now(timezone.utc) - timedelta(days=60))
        await make_url(e, "recent1", expires_at=datetime.now(timezone.utc) - timedelta(days=2))
        await make_url(e, "never11")
        cache = await build_cache(e)
        cw = CleanupWorker(e.settings, create_sessionmaker(e.engines[0]), cache)
        assert await cw.purge_expired_urls() == 5
        assert sorted(r[0] for r in await e.sql("SELECT short_code FROM urls")) == ["never11", "recent1"]
    finally:
        await e.close()
