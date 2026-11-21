"""Password hashing (Argon2), JWT handling and privacy-preserving IP hashing."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import time
import uuid
from functools import lru_cache

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from app.core.config import Settings
from app.core.exceptions import AuthenticationError


@lru_cache(maxsize=8)
def _hasher(time_cost: int, memory_cost: int, parallelism: int) -> PasswordHasher:
    return PasswordHasher(time_cost=time_cost, memory_cost=memory_cost, parallelism=parallelism)


def _ph(settings: Settings) -> PasswordHasher:
    return _hasher(settings.argon2_time_cost, settings.argon2_memory_cost_kib, settings.argon2_parallelism)


async def hash_password(settings: Settings, password: str) -> str:
    # Argon2 is deliberately CPU/memory hard: keep it off the event loop.
    return await asyncio.to_thread(_ph(settings).hash, password)


async def verify_password(settings: Settings, hashed: str, password: str) -> bool:
    def _verify() -> bool:
        try:
            return _ph(settings).verify(hashed, password)
        except (VerificationError, InvalidHashError):
            return False

    return await asyncio.to_thread(_verify)


async def verify_dummy(settings: Settings, password: str) -> None:
    """Burn comparable time when the account does not exist (user-enumeration defence)."""
    dummy = await asyncio.to_thread(_ph(settings).hash, "dummy-password-for-timing")
    await verify_password(settings, dummy, password)


def create_access_token(settings: Settings, user_id: int) -> tuple[str, int]:
    now = int(time.time())
    ttl = settings.access_token_expire_minutes * 60
    payload = {
        "sub": str(user_id),
        "iat": now,
        "exp": now + ttl,
        "iss": settings.jwt_issuer,
        "jti": uuid.uuid4().hex,
    }
    token = jwt.encode(payload, settings.secret_key.get_secret_value(), algorithm=settings.jwt_algorithm)
    return token, ttl


def decode_access_token(settings: Settings, token: str) -> int:
    try:
        payload = jwt.decode(
            token,
            settings.secret_key.get_secret_value(),
            algorithms=[settings.jwt_algorithm],  # pinned: never trust the token's own alg header
            issuer=settings.jwt_issuer,
            options={"require": ["exp", "sub", "iss"]},
        )
        return int(payload["sub"])
    except jwt.ExpiredSignatureError as exc:
        raise AuthenticationError("Token has expired.", code="token_expired") from exc
    except (jwt.InvalidTokenError, ValueError) as exc:
        raise AuthenticationError("Invalid token.", code="invalid_token") from exc


def hash_ip(secret: bytes, ip: str) -> str:
    """HMAC-SHA256 of the client IP, truncated to 128 bits. Raw IPs are never stored."""
    return hmac.new(secret, ip.encode(), hashlib.sha256).hexdigest()[:32]
