"""FastAPI dependencies: context, DB session, authentication, rate limiting."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.context import AppContext
from app.core.exceptions import AuthenticationError, RateLimitExceeded
from app.core.security import decode_access_token
from app.database.models import User
from app.services.rate_limiter import RateLimitResult

_bearer = HTTPBearer(auto_error=False, description="JWT access token from POST /api/v1/auth/login")


def get_ctx(request: Request) -> AppContext:
    return request.app.state.ctx


Ctx = Annotated[AppContext, Depends(get_ctx)]


async def get_session(ctx: Ctx) -> AsyncIterator[AsyncSession]:
    """One session per request. Services commit explicitly; anything else rolls back on close."""
    async with ctx.sessionmaker() as session:
        yield session


Session = Annotated[AsyncSession, Depends(get_session)]


def get_client_ip(request: Request, ctx: Ctx) -> str:
    return ctx.client_ip(request)


ClientIp = Annotated[str, Depends(get_client_ip)]


async def get_optional_user(
    ctx: Ctx,
    session: Session,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> User | None:
    if credentials is None:
        return None
    user_id = decode_access_token(ctx.settings, credentials.credentials)
    user = await ctx.auth.get_user(session, user_id)
    if user is None:
        raise AuthenticationError("Account no longer exists.", code="invalid_token")
    return user


async def get_current_user(user: Annotated[User | None, Depends(get_optional_user)]) -> User:
    if user is None:
        raise AuthenticationError()
    return user


OptionalUser = Annotated[User | None, Depends(get_optional_user)]
CurrentUser = Annotated[User, Depends(get_current_user)]


def apply_rate_limit(result: RateLimitResult, response: Response | None = None) -> None:
    """Raise 429 (with headers) on rejection; otherwise attach the informational headers."""
    if not result.allowed:
        raise RateLimitExceeded("Rate limit exceeded. Please retry later.", headers=result.headers())
    if response is not None:
        response.headers.update(result.headers())


async def limit_auth(ctx: Ctx, ip: ClientIp, response: Response) -> None:
    result = await ctx.rate_limiter.check("auth", ip, ctx.settings.rate_limit_auth_per_window)
    apply_rate_limit(result, response)
