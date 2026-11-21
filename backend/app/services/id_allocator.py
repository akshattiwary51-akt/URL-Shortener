"""Range-based, collision-free ID allocation.

PostgreSQL is the durable source of truth: reserving a block is one atomic
``INSERT … ON CONFLICT DO UPDATE … RETURNING`` on the allocator row (the row lock
serialises concurrent reservations from any number of processes).

Each process hands out IDs from its private in-memory block, guarded by an
``asyncio.Lock``. If a process dies, the unused remainder of its block is simply
lost - blocks are never re-issued, so IDs are never reused.
"""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.exceptions import IdSpaceExhausted
from app.services.base62 import CAPACITY

logger = logging.getLogger(__name__)

_RESERVE_SQL = text(
    """
    INSERT INTO id_allocator (allocator_name, next_id, updated_at)
    VALUES (:name, :size, now())
    ON CONFLICT (allocator_name)
    DO UPDATE SET next_id = id_allocator.next_id + :size, updated_at = now()
    RETURNING next_id - :size AS start_id
    """
)


class RangeAllocator:
    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        name: str = "short_codes",
        range_size: int = 1000,
        capacity: int = CAPACITY,
    ) -> None:
        if range_size < 1:
            raise ValueError("range_size must be >= 1")
        self._sessionmaker = sessionmaker
        self._name = name
        self._range_size = range_size
        self._capacity = capacity
        self._lock = asyncio.Lock()
        self._next = 0
        self._end = 0  # exclusive; _next == _end means the block is empty
        self.reservations = 0

    async def next_id(self) -> int:
        async with self._lock:
            if self._next >= self._end:
                await self._reserve()
            value = self._next
            self._next += 1
            return value

    async def _reserve(self) -> None:
        async with self._sessionmaker.begin() as session:
            start = (
                await session.execute(_RESERVE_SQL, {"name": self._name, "size": self._range_size})
            ).scalar_one()
        if start >= self._capacity:
            raise IdSpaceExhausted()
        end = min(start + self._range_size, self._capacity)  # never hand out an ID >= capacity
        self._next, self._end = start, end
        self.reservations += 1
        logger.debug("reserved id range", extra={"start": start, "end": end})
