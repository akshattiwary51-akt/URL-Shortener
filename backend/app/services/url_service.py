"""URL creation and management. All cache invalidation happens *after* the DB commit."""

from __future__ import annotations

import base64
import binascii
import logging
from datetime import datetime, timezone
from urllib.parse import urlsplit

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.exceptions import AliasConflict, NotFoundError, ServiceUnavailable, ValidationFailed
from app.database.models import Url
from app.services import base62
from app.services.cache_service import GONE, NOT_FOUND, CacheRecord, CacheService
from app.services.id_allocator import RangeAllocator
from app.services.url_validation import normalize_destination, validate_alias, validate_expiry

logger = logging.getLogger(__name__)
_MAX_GENERATED_ATTEMPTS = 5


def _is_short_code_conflict(exc: IntegrityError) -> bool:
    return "uq_urls_short_code" in str(exc.orig)


def encode_cursor(last_id: int) -> str:
    return base64.urlsafe_b64encode(f"id:{last_id}".encode()).decode().rstrip("=")


def decode_cursor(cursor: str) -> int:
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode()
        prefix, _, value = raw.partition(":")
        if prefix != "id" or not value.isdigit():
            raise ValueError
        return int(value)
    except (binascii.Error, ValueError, UnicodeDecodeError):
        raise ValidationFailed(
            "Invalid pagination cursor.", details=[{"loc": ["query", "cursor"], "message": "invalid cursor", "type": "value_error"}]
        ) from None


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def cache_record_for(url: Url) -> CacheRecord:
    return CacheRecord(url.original_url, int(url.expires_at.timestamp()) if url.expires_at else None)


class UrlService:
    def __init__(self, settings: Settings, allocator: RangeAllocator, cache: CacheService) -> None:
        self._settings = settings
        self._allocator = allocator
        self._cache = cache
        host = urlsplit(settings.base_url).hostname
        self._blocked_hosts = frozenset({host.lower()}) if host else frozenset()

    def code_for_id(self, n: int) -> str:
        scrambled = base62.scramble(n, self._settings.id_scramble_multiplier, self._settings.id_scramble_offset)
        return base62.encode_fixed(scrambled)

    # ------------------------------------------------------------------ create
    async def create(
        self,
        session: AsyncSession,
        *,
        owner_id: int | None,
        original_url: str,
        custom_alias: str | None,
        expires_at: datetime | None,
    ) -> Url:
        destination = normalize_destination(
            original_url, max_length=self._settings.max_url_length, blocked_hosts=self._blocked_hosts
        )
        expiry = validate_expiry(expires_at, max_days=self._settings.max_expiry_days)

        if custom_alias is not None:
            url = await self._insert_alias(session, validate_alias(custom_alias), destination, owner_id, expiry)
        else:
            url = await self._insert_generated(session, destination, owner_id, expiry)
        await session.commit()

        # Write-through (plain SET) so a stale negative-cache entry cannot hide the new link.
        await self._cache.write_record(url.short_code, cache_record_for(url), datetime.now(timezone.utc).timestamp())
        return url

    async def _insert_alias(
        self, session: AsyncSession, alias: str, destination: str, owner_id: int | None, expiry: datetime | None
    ) -> Url:
        url = Url(short_code=alias, original_url=destination, owner_id=owner_id, expires_at=expiry)
        session.add(url)
        try:
            await session.flush()
        except IntegrityError as exc:
            await session.rollback()
            if _is_short_code_conflict(exc):
                raise AliasConflict() from exc
            raise
        return url

    async def _insert_generated(
        self, session: AsyncSession, destination: str, owner_id: int | None, expiry: datetime | None
    ) -> Url:
        for _ in range(_MAX_GENERATED_ATTEMPTS):
            code = self.code_for_id(await self._allocator.next_id())
            url = Url(short_code=code, original_url=destination, owner_id=owner_id, expires_at=expiry)
            try:
                async with session.begin_nested():  # savepoint: a collision must not poison the txn
                    session.add(url)
                    await session.flush()
                return url
            except IntegrityError as exc:
                if not _is_short_code_conflict(exc):
                    raise
                # A custom alias happened to equal this generated code: take the next ID.
                logger.info("generated code collided with a custom alias", extra={"short_code": code})
        raise ServiceUnavailable("Could not allocate a short code; please retry.")

    # -------------------------------------------------------------------- read
    async def get_owned(self, session: AsyncSession, owner_id: int, short_code: str) -> Url:
        url = (
            await session.execute(select(Url).where(Url.short_code == short_code, Url.owner_id == owner_id))
        ).scalar_one_or_none()
        if url is None:  # also covers "belongs to someone else": do not leak existence
            raise NotFoundError("Short URL not found.", code="url_not_found")
        return url

    async def list_for_owner(
        self, session: AsyncSession, owner_id: int, *, limit: int, cursor: str | None, q: str | None
    ) -> tuple[list[Url], str | None]:
        stmt = select(Url).where(Url.owner_id == owner_id)
        if cursor:
            stmt = stmt.where(Url.id < decode_cursor(cursor))
        if q:
            pattern = f"%{_escape_like(q.strip())}%"
            stmt = stmt.where(or_(Url.short_code.ilike(pattern, escape="\\"), Url.original_url.ilike(pattern, escape="\\")))
        rows = list((await session.execute(stmt.order_by(Url.id.desc()).limit(limit + 1))).scalars())
        has_more = len(rows) > limit
        items = rows[:limit]
        return items, (encode_cursor(items[-1].id) if has_more and items else None)

    # ------------------------------------------------------------------ update
    async def update(
        self,
        session: AsyncSession,
        owner_id: int,
        short_code: str,
        *,
        is_active: bool | None,
        set_expiry: bool,
        expires_at: datetime | None,
    ) -> Url:
        url = await self.get_owned(session, owner_id, short_code)
        if is_active is not None:
            url.is_active = is_active
        if set_expiry:
            url.expires_at = validate_expiry(expires_at, max_days=self._settings.max_expiry_days)
        await session.commit()
        await self._sync_cache(url)
        return url

    async def _sync_cache(self, url: Url) -> None:
        now = datetime.now(timezone.utc).timestamp()
        if not url.is_active:
            await self._cache.write_tombstone(url.short_code, GONE)
        else:
            await self._cache.write_record(url.short_code, cache_record_for(url), now)

    # ------------------------------------------------------------------ delete
    async def delete(self, session: AsyncSession, owner_id: int, short_code: str) -> None:
        url = await self.get_owned(session, owner_id, short_code)
        await session.delete(url)  # analytics rows go with it (ON DELETE CASCADE)
        await session.commit()
        await self._cache.write_tombstone(short_code, NOT_FOUND)
