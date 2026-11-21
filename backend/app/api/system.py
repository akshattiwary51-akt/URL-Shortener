"""Liveness, readiness and Prometheus metrics endpoints."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import text

from app.context import AppContext

router = APIRouter(tags=["system"])
_CHECK_TIMEOUT = 2.0


@router.get("/health/live", summary="Liveness: the process is up")
async def live() -> dict[str, str]:
    return {"status": "ok"}


async def _check_postgres(ctx: AppContext) -> str:
    try:
        async with ctx.sessionmaker() as session:
            await asyncio.wait_for(session.execute(text("SELECT 1")), _CHECK_TIMEOUT)
        return "ok"
    except Exception:  # noqa: BLE001
        return "unavailable"


async def _check_redis(client) -> str:  # type: ignore[no-untyped-def]
    try:
        await asyncio.wait_for(client.ping(), _CHECK_TIMEOUT)
        return "ok"
    except Exception:  # noqa: BLE001
        return "unavailable"


@router.get("/health/ready", summary="Readiness: dependencies reachable")
async def ready(request: Request) -> JSONResponse:
    """PostgreSQL is required (503 when down). Redis is optional: the service
    degrades gracefully without it, so a Redis outage reports ``degraded`` with 200."""
    ctx: AppContext = request.app.state.ctx
    pg, cache, limiter, stream = await asyncio.gather(
        _check_postgres(ctx),
        _check_redis(ctx.cache_redis),
        _check_redis(ctx.ratelimit_redis),
        _check_redis(ctx.stream_redis),
    )
    checks = {"postgres": pg, "redis_cache": cache, "redis_ratelimit": limiter, "redis_stream": stream}
    if pg != "ok":
        status, code = "unavailable", 503
    elif any(v != "ok" for v in checks.values()):
        status, code = "degraded", 200
    else:
        status, code = "ok", 200
    return JSONResponse({"status": status, "checks": checks}, status_code=code)


@router.get("/metrics", include_in_schema=False)
async def metrics(request: Request) -> Response:
    ctx: AppContext = request.app.state.ctx
    return Response(generate_latest(ctx.metrics.registry), media_type=CONTENT_TYPE_LATEST)
