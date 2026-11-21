"""Test harness.

Tests run against *real* PostgreSQL and Redis (no mocks for distributed behaviour).
Point them at any instances via TEST_DATABASE_URL / TEST_REDIS_URL; the defaults
match ``docker compose up -d postgres redis`` with the .env.example credentials
on localhost. The schema is built from scratch with Alembic once per session, which
also verifies that migrations work on a fresh database.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

import asyncpg
import httpx
import pytest
import pytest_asyncio
import redis.asyncio as aioredis
from asgi_lifespan import LifespanManager

from app.core.config import Settings
from app.main import create_app

BACKEND_DIR = Path(__file__).resolve().parents[1]
TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+asyncpg://shortener:shortener@localhost:5432/shortener_test"
)
TEST_REDIS_URL = os.environ.get("TEST_REDIS_URL", "redis://localhost:6379/15")

TABLES = (
    "browser_analytics, os_analytics, device_analytics, country_analytics, referrer_analytics, "
    "daily_analytics, click_events, urls, users, id_allocator"
)


def make_settings(**overrides) -> Settings:  # type: ignore[no-untyped-def]
    values = dict(
        environment="test",
        database_url=TEST_DATABASE_URL,
        redis_url=TEST_REDIS_URL,
        log_level="WARNING",
        access_log_enabled=False,
        base_url="http://short.test",
        secret_key="test-secret-key-test-secret-key-0123456789",
        ip_hash_secret="test-ip-secret-test-ip-secret-0123456789",
        # Cheap Argon2 so auth tests are fast; production defaults live in Settings.
        argon2_time_cost=1,
        argon2_memory_cost_kib=1024,
        argon2_parallelism=1,
        # High limits by default; rate-limit tests lower them explicitly.
        rate_limit_create_per_window=10_000,
        rate_limit_create_user_per_window=10_000,
        rate_limit_redirect_per_window=10_000,
        rate_limit_auth_per_window=10_000,
        worker_claim_idle_ms=50,
        worker_flush_interval_seconds=0.2,
    )
    values.update(overrides)
    return Settings(_env_file=None, **values)


def _plain_dsn(url: str) -> str:
    return url.replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture(scope="session", autouse=True)
def _migrated_database() -> None:
    """Drop everything and rebuild the schema with Alembic (migrations-on-fresh-DB check)."""

    async def reset() -> None:
        conn = await asyncpg.connect(_plain_dsn(TEST_DATABASE_URL))
        try:
            await conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        finally:
            await conn.close()

    asyncio.run(reset())
    env = {**os.environ, "DATABASE_URL": TEST_DATABASE_URL}
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=BACKEND_DIR, env=env, check=True)


@pytest_asyncio.fixture(autouse=True)
async def _clean_state(request) -> AsyncIterator[None]:  # type: ignore[no-untyped-def]
    if "unit" in request.node.nodeid.split("/")[:2] or request.node.nodeid.startswith("tests/unit"):
        yield
        return
    conn = await asyncpg.connect(_plain_dsn(TEST_DATABASE_URL))
    try:
        await conn.execute(f"TRUNCATE {TABLES} RESTART IDENTITY CASCADE")
    finally:
        await conn.close()
    r = aioredis.from_url(TEST_REDIS_URL)
    await r.flushdb()
    await r.aclose()
    yield


@pytest_asyncio.fixture
async def redis_client() -> AsyncIterator[aioredis.Redis]:
    r = aioredis.from_url(TEST_REDIS_URL, decode_responses=True)
    yield r
    await r.aclose()


@asynccontextmanager
async def build_client(client_ip: str = "198.51.100.7", raise_app_exceptions: bool = True, **overrides) -> AsyncIterator[httpx.AsyncClient]:  # type: ignore[no-untyped-def]
    app = create_app(make_settings(**overrides))
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app, client=(client_ip, 4242), raise_app_exceptions=raise_app_exceptions)
        async with httpx.AsyncClient(transport=transport, base_url="http://short.test") as client:
            client.app_ctx = app.state.ctx  # type: ignore[attr-defined]
            yield client


@pytest_asyncio.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    async with build_client() as c:
        yield c


async def register_and_login(client: httpx.AsyncClient, email: str = "alice@example.com", password: str = "correct-horse-battery") -> dict[str, str]:
    r = await client.post("/api/v1/auth/register", json={"email": email, "password": password})
    assert r.status_code == 201, r.text
    r = await client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest_asyncio.fixture
async def auth(client: httpx.AsyncClient) -> dict[str, str]:
    return await register_and_login(client)
