"""``GET /{short_code}`` - the latency-critical path. Keep it lean."""

from __future__ import annotations

from time import perf_counter

from fastapi import APIRouter, Request, Response

from app.context import AppContext
from app.core.exceptions import GoneError, NotFoundError, RateLimitExceeded
from app.services.redirect_service import RedirectService

router = APIRouter(tags=["redirect"])


@router.get(
    "/{short_code}",
    status_code=302,
    summary="Redirect to the original URL",
    responses={
        302: {"description": "Redirect to the destination (Cache-Control: no-store)"},
        404: {"description": "Unknown short code"},
        410: {"description": "Link expired or disabled"},
        429: {"description": "Rate limit exceeded"},
    },
)
async def redirect(short_code: str, request: Request) -> Response:
    ctx: AppContext = request.app.state.ctx
    metrics = ctx.metrics
    started = perf_counter()
    metrics.redirect_requests.inc()
    try:
        if not RedirectService.is_valid_code(short_code):
            metrics.redirect_errors.labels(reason="invalid_code").inc()
            raise NotFoundError("Short link not found.", code="link_not_found")

        ip = ctx.client_ip(request)
        limit = await ctx.rate_limiter.check("redirect", ip, ctx.settings.rate_limit_redirect_per_window)
        if not limit.allowed:
            metrics.redirect_errors.labels(reason="rate_limited").inc()
            raise RateLimitExceeded("Rate limit exceeded. Please retry later.", headers=limit.headers())

        resolution = await ctx.redirect.resolve(short_code)
        if resolution.outcome == "not_found":
            metrics.redirect_errors.labels(reason="not_found").inc()
            raise NotFoundError("Short link not found.", code="link_not_found", headers=limit.headers())
        if resolution.outcome == "gone":
            metrics.redirect_errors.labels(reason="gone").inc()
            raise GoneError(headers=limit.headers())

        # Bounded (strict timeout) and never raises: analytics must not fail a valid redirect.
        await ctx.ingestor.enqueue(
            short_code,
            user_agent=request.headers.get("user-agent"),
            ip=ip,
            referrer=request.headers.get("referer"),
        )
        metrics.redirect_success.inc()
        headers = limit.headers()
        headers["Location"] = resolution.url  # type: ignore[assignment]
        headers["Cache-Control"] = "no-store"
        return Response(status_code=302, headers=headers)
    except (NotFoundError, GoneError, RateLimitExceeded):
        raise
    except Exception:
        metrics.redirect_errors.labels(reason="error").inc()
        raise
    finally:
        metrics.redirect_latency.observe(perf_counter() - started)
