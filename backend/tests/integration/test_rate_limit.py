import asyncio

import redis.asyncio as aioredis

from app.core.config import Settings
from app.monitoring.metrics import AppMetrics
from app.services.rate_limiter import SlidingWindowRateLimiter
from tests.conftest import TEST_REDIS_URL, build_client, make_settings


def _limiter(**overrides):
    settings = make_settings(**overrides)
    client = aioredis.from_url(TEST_REDIS_URL, decode_responses=True)
    return client, SlidingWindowRateLimiter(client, settings, AppMetrics())


async def test_allows_up_to_limit_then_rejects():
    client, rl = _limiter()
    try:
        results = [await rl.check("t", "ip1", 5, 60) for _ in range(7)]
        assert [r.allowed for r in results] == [True] * 5 + [False] * 2
        assert [r.remaining for r in results[:5]] == [4, 3, 2, 1, 0]
        assert results[5].retry_after >= 1 and results[5].remaining == 0
    finally:
        await client.aclose()


async def test_concurrent_requests_cannot_exceed_the_limit():
    """200 simultaneous checks across 4 independent clients (simulating 4 API nodes): exactly `limit` pass."""
    limit = 25
    pairs = [_limiter() for _ in range(4)]
    try:
        jobs = [pairs[i % 4][1].check("race", "same-ip", limit, 60) for i in range(200)]
        results = await asyncio.gather(*jobs)
        assert sum(r.allowed for r in results) == limit
        assert sum(not r.allowed for r in results) == 200 - limit
    finally:
        for c, _ in pairs:
            await c.aclose()


async def test_window_slides_and_capacity_returns():
    client, rl = _limiter()
    try:
        for _ in range(3):
            assert (await rl.check("slide", "ip", 3, 1)).allowed
        assert not (await rl.check("slide", "ip", 3, 1)).allowed
        await asyncio.sleep(1.15)
        assert (await rl.check("slide", "ip", 3, 1)).allowed
    finally:
        await client.aclose()


async def test_sliding_log_has_no_fixed_window_boundary_burst():
    """Fixed windows allow 2x the limit across a boundary; a sliding log must not."""
    client, rl = _limiter()
    try:
        await asyncio.sleep(0.0)
        first = [await rl.check("burst", "ip", 4, 1) for _ in range(4)]
        assert all(r.allowed for r in first)
        await asyncio.sleep(0.6)  # half a window later: still only 0 slots free
        assert not (await rl.check("burst", "ip", 4, 1)).allowed
    finally:
        await client.aclose()


async def test_keys_are_isolated_by_category_and_identifier_and_expire():
    client, rl = _limiter()
    try:
        assert (await rl.check("a", "ip", 1, 60)).allowed
        assert not (await rl.check("a", "ip", 1, 60)).allowed
        assert (await rl.check("b", "ip", 1, 60)).allowed  # other category
        assert (await rl.check("a", "other-ip", 1, 60)).allowed  # other identity
        assert await client.exists("rl:a:ip") == 1
        ttl_ms = await client.pttl("rl:a:ip")
        assert 0 < ttl_ms <= 60_000  # keys always carry an expiry
        assert await client.type("rl:a:ip") == "zset"
    finally:
        await client.aclose()


async def test_rejected_requests_are_not_recorded():
    client, rl = _limiter()
    try:
        for _ in range(20):
            await rl.check("norecord", "ip", 2, 60)
        assert await client.zcard("rl:norecord:ip") == 2
    finally:
        await client.aclose()


async def test_uses_redis_server_time_not_client_clock(monkeypatch):
    import time

    client, rl = _limiter()
    try:
        monkeypatch.setattr(time, "time", lambda: 0.0)  # a badly skewed client clock changes nothing
        assert (await rl.check("clock", "ip", 1, 60)).allowed
        assert not (await rl.check("clock", "ip", 1, 60)).allowed
        score = (await client.zrange("rl:clock:ip", 0, 0, withscores=True))[0][1]
        server_ms = (await client.time())[0] * 1000
        assert abs(score - server_ms) < 5_000
    finally:
        await client.aclose()


async def test_create_endpoint_returns_429_with_headers():
    async with build_client(rate_limit_create_per_window=3) as c:
        statuses = []
        for i in range(5):
            r = await c.post("/api/v1/urls", json={"original_url": f"https://example.com/{i}"})
            statuses.append(r.status_code)
        assert statuses == [201, 201, 201, 429, 429]
        assert r.headers["x-ratelimit-limit"] == "3" and r.headers["x-ratelimit-remaining"] == "0"
        assert 1 <= int(r.headers["retry-after"]) <= 60
        assert r.json()["error"]["code"] == "rate_limited"


async def test_redirect_endpoint_limit_is_per_ip_and_blocks_before_db():
    async with build_client(rate_limit_redirect_per_window=5) as c:
        code = (await c.post("/api/v1/urls", json={"original_url": "https://example.com/r"})).json()["short_code"]
        codes = [(await c.get(f"/{code}")).status_code for _ in range(8)]
        assert codes == [302] * 5 + [429] * 3
        r = await c.get(f"/{code}")
        assert r.status_code == 429 and "location" not in r.headers and "retry-after" in r.headers
    # Another client IP has its own budget.
    async with build_client(client_ip="203.0.113.99", rate_limit_redirect_per_window=5) as c2:
        assert (await c2.get("/whatever1")).status_code == 404


async def test_forwarded_header_ignored_without_trusted_proxy():
    async with build_client(rate_limit_redirect_per_window=2) as c:
        statuses = [(await c.get("/abcdefg", headers={"X-Forwarded-For": f"9.9.9.{i}"})).status_code for i in range(4)]
        assert statuses == [404, 404, 429, 429]  # rotating a spoofed header does not evade the limit


async def test_forwarded_header_honoured_from_trusted_proxy():
    async with build_client(client_ip="10.1.2.3", trusted_proxies="10.0.0.0/8", rate_limit_redirect_per_window=1) as c:
        a = await c.get("/abcdefg", headers={"X-Forwarded-For": "198.51.100.1"})
        b = await c.get("/abcdefg", headers={"X-Forwarded-For": "198.51.100.2"})
        again = await c.get("/abcdefg", headers={"X-Forwarded-For": "198.51.100.1"})
        assert (a.status_code, b.status_code, again.status_code) == (404, 404, 429)


async def test_authenticated_users_get_a_separate_per_user_budget():
    from tests.conftest import register_and_login

    async with build_client(rate_limit_create_per_window=1, rate_limit_create_user_per_window=4) as c:
        headers = await register_and_login(c)
        statuses = [(await c.post("/api/v1/urls", json={"original_url": f"https://example.com/{i}"}, headers=headers)).status_code for i in range(5)]
        assert statuses == [201, 201, 201, 201, 429]


async def test_rate_limiter_fails_open_when_redis_is_down():
    async with build_client(ratelimit_redis_url="redis://127.0.0.1:6399/0", redis_socket_timeout_ms=100) as c:
        r = await c.post("/api/v1/urls", json={"original_url": "https://example.com/x"})
        assert r.status_code == 201
        assert c.app_ctx.metrics.rate_limit_errors.labels(mode="fail_open")._value.get() >= 1


async def test_rate_limiter_can_fail_closed():
    async with build_client(ratelimit_redis_url="redis://127.0.0.1:6399/0", redis_socket_timeout_ms=100, rate_limit_fail_open=False) as c:
        r = await c.post("/api/v1/urls", json={"original_url": "https://example.com/x"})
        assert r.status_code == 503


async def test_rate_limiting_can_be_disabled():
    async with build_client(rate_limit_enabled=False, rate_limit_create_per_window=1) as c:
        for i in range(5):
            assert (await c.post("/api/v1/urls", json={"original_url": f"https://example.com/{i}"})).status_code == 201


async def test_auth_endpoints_are_rate_limited():
    async with build_client(rate_limit_auth_per_window=3) as c:
        codes = [(await c.post("/api/v1/auth/login", json={"email": "x@example.com", "password": "pw-pw-pw-pw"})).status_code for _ in range(5)]
        assert codes == [401, 401, 401, 429, 429]


def test_settings_defaults_match_spec():
    s = Settings(_env_file=None)
    assert (s.rate_limit_create_per_window, s.rate_limit_redirect_per_window) == (10, 120)
    assert (s.cache_ttl_seconds, s.negative_cache_ttl_seconds) == (86_400, 60)
    assert (s.id_range_size, s.worker_batch_size, s.worker_flush_interval_seconds) == (1000, 500, 5.0)
