"""Range allocator: uniqueness across processes/instances, crash safety, capacity."""

import asyncio

import pytest
from sqlalchemy import text

from app.core.exceptions import IdSpaceExhausted
from app.database.connection import create_engine, create_sessionmaker
from app.services import base62
from app.services.id_allocator import RangeAllocator
from app.services.url_service import UrlService
from tests.conftest import make_settings


async def _allocator(name="t", range_size=50, capacity=base62.CAPACITY):
    engine = create_engine(make_settings())  # each allocator = its own engine/pool, like a separate process
    return engine, RangeAllocator(create_sessionmaker(engine), name, range_size, capacity)


async def test_concurrent_allocators_never_issue_duplicate_ids():
    pairs = [await _allocator() for _ in range(6)]
    try:
        async def draw(allocator, n):
            return [await allocator.next_id() for _ in range(n)]

        # 3 concurrent tasks per allocator also exercises the per-process asyncio lock.
        jobs = [draw(a, 300) for _, a in pairs for _ in range(3)]
        results = await asyncio.gather(*jobs)
        ids = [i for r in results for i in r]
        assert len(ids) == 6 * 3 * 300
        assert len(set(ids)) == len(ids), "duplicate IDs were issued"
        assert min(ids) == 0
        # Each task's own sequence is strictly increasing.
        assert all(r == sorted(r) for r in results)
    finally:
        for engine, _ in pairs:
            await engine.dispose()


async def test_only_one_reservation_per_range():
    engine, alloc = await _allocator(range_size=1000)
    try:
        for _ in range(2500):
            await alloc.next_id()
        assert alloc.reservations == 3  # ceil(2500 / 1000)
        async with engine.connect() as conn:
            next_id = (await conn.execute(text("SELECT next_id FROM id_allocator WHERE allocator_name='t'"))).scalar_one()
        assert next_id == 3000
    finally:
        await engine.dispose()


async def test_crash_never_reuses_ids():
    e1, a1 = await _allocator(range_size=100)
    first = [await a1.next_id() for _ in range(10)]  # uses 10 of 100, then "crashes"
    await e1.dispose()
    e2, a2 = await _allocator(range_size=100)
    try:
        resumed = await a2.next_id()
        assert resumed >= 100, "restarted process must start a fresh block, never reuse the lost remainder"
        assert resumed not in first
    finally:
        await e2.dispose()


async def test_capacity_is_enforced_and_never_exceeded():
    engine, alloc = await _allocator(range_size=1000, capacity=2500)
    try:
        ids = [await alloc.next_id() for _ in range(2500)]
        assert ids == list(range(2500))
        with pytest.raises(IdSpaceExhausted):
            await alloc.next_id()
        with pytest.raises(IdSpaceExhausted):
            await alloc.next_id()
    finally:
        await engine.dispose()


async def test_independent_allocator_names_do_not_interfere():
    e1, a = await _allocator(name="one")
    e2, b = await _allocator(name="two")
    try:
        assert await a.next_id() == 0
        assert await b.next_id() == 0
    finally:
        await e1.dispose()
        await e2.dispose()


async def test_generated_codes_are_unique_fixed_length_and_in_range():
    settings = make_settings()
    svc = UrlService(settings, None, None)  # type: ignore[arg-type]
    codes = [svc.code_for_id(n) for n in range(30_000)]
    assert len(set(codes)) == len(codes)
    assert all(len(c) == 7 and c.isalnum() for c in codes)
    # Highest valid ID still encodes in 7 chars.
    assert len(svc.code_for_id(base62.CAPACITY - 1)) == 7
