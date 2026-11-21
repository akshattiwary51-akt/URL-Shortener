import asyncio
import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from tests.conftest import build_client


async def _create(client, headers=None, **body):
    body.setdefault("original_url", "https://example.com/dest")
    r = await client.post("/api/v1/urls", json=body, headers=headers or {})
    assert r.status_code == 201, r.text
    return r.json()["short_code"]


async def _redis_get(redis_client, code):
    return await redis_client.get(f"url:{code}")


async def test_redirect_302_with_no_store(client):
    code = await _create(client)
    r = await client.get(f"/{code}")
    assert r.status_code == 302
    assert r.headers["location"] == "https://example.com/dest"
    assert r.headers["cache-control"] == "no-store"
    assert r.headers["x-ratelimit-limit"]


async def test_cache_hit_does_not_touch_postgres(client, redis_client):
    code = await _create(client)
    assert json.loads(await _redis_get(redis_client, code))["u"] == "https://example.com/dest"  # write-through
    # Remove the row behind the cache's back: a hit must still redirect (proves the DB isn't consulted).
    async with client.app_ctx.sessionmaker() as s:
        await s.execute(text("DELETE FROM urls WHERE short_code = :c"), {"c": code})
        await s.commit()
    r = await client.get(f"/{code}")
    assert r.status_code == 302
    assert client.app_ctx.metrics.cache_hits._value.get() >= 1


async def test_cache_miss_falls_back_to_postgres_and_populates(client, redis_client):
    code = await _create(client)
    await redis_client.delete(f"url:{code}")
    before = client.app_ctx.metrics.cache_misses._value.get()
    assert (await client.get(f"/{code}")).status_code == 302
    assert client.app_ctx.metrics.cache_misses._value.get() == before + 1
    cached = json.loads(await _redis_get(redis_client, code))
    assert cached["u"] == "https://example.com/dest"
    ttl = await redis_client.ttl(f"url:{code}")
    assert 86_000 < ttl <= 86_400  # default 24h


async def test_unknown_code_is_negatively_cached_for_60s(client, redis_client):
    assert (await client.get("/nope1234")).status_code == 404
    assert await _redis_get(redis_client, "nope1234") == "__NOT_FOUND__"
    assert 0 < await redis_client.ttl("url:nope1234") <= 60

    # Second request is served from the negative cache without a DB query.
    queries_before = client.app_ctx.metrics.db_query_latency._sum.get()
    count_before = sum(s.value for m in client.app_ctx.metrics.db_query_latency.collect() for s in m.samples if s.name.endswith("_count"))
    assert (await client.get("/nope1234")).status_code == 404
    count_after = sum(s.value for m in client.app_ctx.metrics.db_query_latency.collect() for s in m.samples if s.name.endswith("_count"))
    assert count_after == count_before
    assert client.app_ctx.metrics.cache_negative_hits._value.get() >= 1
    del queries_before


async def test_invalid_code_shapes_return_404_without_any_io(client, redis_client):
    for bad in ("ab", "a" * 33, "with.dot", "ünï", "a$b"):
        assert (await client.get(f"/{bad}")).status_code == 404
    assert await redis_client.dbsize() == 0


async def test_creating_a_link_overwrites_a_stale_negative_entry(client, redis_client):
    assert (await client.get("/fresh-alias")).status_code == 404
    assert await _redis_get(redis_client, "fresh-alias") == "__NOT_FOUND__"
    await _create(client, custom_alias="fresh-alias")
    assert (await client.get("/fresh-alias")).status_code == 302  # not hidden for 60s


async def test_populate_never_overwrites_a_writers_value(client, redis_client):
    """The stale-reader race: a reader with an old DB snapshot must not clobber fresh data."""
    from app.services.cache_service import CacheRecord

    cache = client.app_ctx.cache
    now = datetime.now(timezone.utc).timestamp()
    await cache.write_record("racecode", CacheRecord("https://new.example/", None), now)
    assert await cache.populate("racecode", CacheRecord("https://stale.example/", None), now) is False
    assert json.loads(await _redis_get(redis_client, "racecode"))["u"] == "https://new.example/"


async def test_expired_link_returns_410_and_never_redirects(client, redis_client):
    code = await _create(client)
    async with client.app_ctx.sessionmaker() as s:
        await s.execute(text("UPDATE urls SET expires_at = now() - interval '1 minute' WHERE short_code = :c"), {"c": code})
        await s.commit()
    await redis_client.delete(f"url:{code}")  # cold cache -> DB path
    r = await client.get(f"/{code}")
    assert r.status_code == 410 and "location" not in r.headers
    assert await _redis_get(redis_client, code) == "__GONE__"
    assert (await client.get(f"/{code}")).status_code == 410  # from tombstone


async def test_cached_record_with_past_expiry_is_rejected_even_if_key_lives_on(client, redis_client):
    code = await _create(client)
    past = int(datetime.now(timezone.utc).timestamp()) - 5
    await redis_client.set(f"url:{code}", json.dumps({"u": "https://example.com/dest", "e": past}), ex=3600)
    r = await client.get(f"/{code}")
    assert r.status_code == 410 and "location" not in r.headers


async def test_cache_ttl_is_capped_by_expiry(client, redis_client):
    exp = (datetime.now(timezone.utc) + timedelta(seconds=90)).isoformat()
    code = await _create(client, expires_at=exp)
    assert 0 < await redis_client.ttl(f"url:{code}") <= 90
    await redis_client.delete(f"url:{code}")
    await client.get(f"/{code}")
    assert 0 < await redis_client.ttl(f"url:{code}") <= 90


async def test_deactivate_invalidates_cache_immediately(client, auth, redis_client):
    code = await _create(client, auth)
    assert (await client.get(f"/{code}")).status_code == 302
    assert (await client.patch(f"/api/v1/urls/{code}", json={"is_active": False}, headers=auth)).status_code == 200
    assert (await client.get(f"/{code}")).status_code == 410
    assert (await client.patch(f"/api/v1/urls/{code}", json={"is_active": True}, headers=auth)).status_code == 200
    assert (await client.get(f"/{code}")).status_code == 302


async def test_changing_expiry_updates_cache(client, auth, redis_client):
    code = await _create(client, auth)
    soon = (datetime.now(timezone.utc) + timedelta(seconds=30)).isoformat()
    await client.patch(f"/api/v1/urls/{code}", json={"expires_at": soon}, headers=auth)
    assert 0 < await redis_client.ttl(f"url:{code}") <= 30


async def test_delete_invalidates_cache_immediately(client, auth):
    code = await _create(client, auth)
    assert (await client.get(f"/{code}")).status_code == 302
    await client.delete(f"/api/v1/urls/{code}", headers=auth)
    assert (await client.get(f"/{code}")).status_code == 404


async def test_corrupt_cache_values_are_discarded_and_served_from_postgres(client, redis_client):
    code = await _create(client)
    for garbage in ("not json", "[]", '{"u": "javascript:alert(1)"}', '{"u": 1}', ""):
        await redis_client.set(f"url:{code}", garbage, ex=300)
        r = await client.get(f"/{code}")
        assert r.status_code == 302 and r.headers["location"] == "https://example.com/dest", garbage
        assert json.loads(await _redis_get(redis_client, code))["u"] == "https://example.com/dest"  # healed
    assert client.app_ctx.metrics.cache_corrupt._value.get() >= 4


async def test_cache_can_be_disabled_for_baseline_runs():
    async with build_client(cache_enabled=False) as c:
        code = await _create(c)
        assert (await c.get(f"/{code}")).status_code == 302
        assert c.app_ctx.metrics.cache_hits._value.get() == 0


async def test_redirect_degrades_to_postgres_when_redis_is_down():
    async with build_client(redis_url="redis://127.0.0.1:6399/0", redis_socket_timeout_ms=100) as c:
        code = await _create(c)  # cache write fails silently
        r = await c.get(f"/{code}")
        assert r.status_code == 302  # served from PostgreSQL; analytics enqueue failed quietly
        assert c.app_ctx.metrics.cache_errors._value.get() >= 1
        assert (await c.get("/health/ready")).json()["status"] == "degraded"
        # Unknown codes still behave.
        assert (await c.get("/unknown-code")).status_code == 404


async def test_redirect_fails_cleanly_when_postgres_is_down_and_cache_cold():
    # Starlette re-raises after sending the 503; a real server just logs it, so do not re-raise here.
    async with build_client(raise_app_exceptions=False, database_url="postgresql+asyncpg://shortener:shortener@127.0.0.1:5999/x", db_pool_timeout_seconds=1) as c:
        r = await c.get("/coldcode")
        assert r.status_code == 503 and r.json()["error"]["code"] == "service_unavailable"
        assert (await c.get("/health/ready")).status_code == 503
        assert (await c.get("/health/live")).status_code == 200


async def test_redirect_headers_do_not_leak_to_unrelated_paths(client):
    r = await client.get("/health/live")
    assert "location" not in r.headers


async def test_concurrent_redirects_all_succeed(client):
    code = await _create(client)
    results = await asyncio.gather(*[client.get(f"/{code}") for _ in range(200)])
    assert all(r.status_code == 302 for r in results)
