from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Path, Query, Response, status

from app.api.deps import ClientIp, Ctx, CurrentUser, OptionalUser, Session, apply_rate_limit
from app.core.exceptions import AuthenticationError
from app.schemas.urls import UrlCreate, UrlList, UrlOut, UrlUpdate, url_out

router = APIRouter(prefix="/urls", tags=["urls"])

ShortCode = Annotated[str, Path(min_length=3, max_length=32, pattern=r"^[A-Za-z0-9_-]+$")]


@router.post("", response_model=UrlOut, status_code=status.HTTP_201_CREATED)
async def create_url(
    body: UrlCreate, response: Response, ctx: Ctx, session: Session, user: OptionalUser, ip: ClientIp
) -> UrlOut:
    s = ctx.settings
    if user is None and not s.anonymous_creation_enabled:
        raise AuthenticationError("Sign in to create short URLs.")
    ctx.metrics.url_creations.labels(result="attempt").inc()
    if user is not None:
        result = await ctx.rate_limiter.check("create_user", str(user.id), s.rate_limit_create_user_per_window)
    else:
        result = await ctx.rate_limiter.check("create", ip, s.rate_limit_create_per_window)
    apply_rate_limit(result, response)

    try:
        url = await ctx.urls.create(
            session,
            owner_id=user.id if user else None,
            original_url=body.original_url,
            custom_alias=body.custom_alias,
            expires_at=body.expires_at,
        )
    except Exception as exc:
        ctx.metrics.url_creations.labels(result=getattr(exc, "code", "error")).inc()
        raise
    ctx.metrics.url_creations.labels(result="created").inc()
    return url_out(url, s.base_url)


@router.get("", response_model=UrlList)
async def list_urls(
    user: CurrentUser,
    ctx: Ctx,
    session: Session,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    cursor: Annotated[str | None, Query(max_length=128)] = None,
    q: Annotated[str | None, Query(max_length=200, description="Search short code or destination")] = None,
) -> UrlList:
    items, next_cursor = await ctx.urls.list_for_owner(session, user.id, limit=limit, cursor=cursor, q=q)
    return UrlList(items=[url_out(u, ctx.settings.base_url) for u in items], next_cursor=next_cursor)


@router.get("/{short_code}", response_model=UrlOut)
async def get_url(short_code: ShortCode, user: CurrentUser, ctx: Ctx, session: Session) -> UrlOut:
    return url_out(await ctx.urls.get_owned(session, user.id, short_code), ctx.settings.base_url)


@router.patch("/{short_code}", response_model=UrlOut)
async def update_url(short_code: ShortCode, body: UrlUpdate, user: CurrentUser, ctx: Ctx, session: Session) -> UrlOut:
    url = await ctx.urls.update(
        session,
        user.id,
        short_code,
        is_active=body.is_active,
        set_expiry="expires_at" in body.model_fields_set,
        expires_at=body.expires_at,
    )
    return url_out(url, ctx.settings.base_url)


@router.delete("/{short_code}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_url(short_code: ShortCode, user: CurrentUser, ctx: Ctx, session: Session) -> Response:
    await ctx.urls.delete(session, user.id, short_code)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
