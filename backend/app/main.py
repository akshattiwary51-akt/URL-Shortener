"""Application factory.

Run with ``uvicorn app.main:create_app --factory`` so settings are read at start-up
rather than at import time.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import redirect, system
from app.api.v1 import api_router
from app.context import build_context
from app.core.config import Settings, get_settings
from app.core.exceptions import register_exception_handlers
from app.core.logging import configure_logging
from app.middleware.request_context import RequestContextMiddleware
from app.monitoring.metrics import AppMetrics

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, settings.log_json)

    metrics = AppMetrics()  # created up-front so the middleware stack (built before lifespan) can use it

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        ctx = build_context(settings, metrics)
        app.state.ctx = ctx
        logger.info("application started", extra={"environment": settings.environment})
        try:
            yield
        finally:
            await ctx.close()
            logger.info("application stopped")

    app = FastAPI(
        title="URL Shortener",
        version="1.0.0",
        description="Low-latency URL shortener with Redis caching, distributed rate limiting and async analytics.",
        lifespan=lifespan,
    )

    # Added last = outermost: CORS headers also appear on error responses.
    app.add_middleware(RequestContextMiddleware, settings=settings, metrics=metrics)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
        expose_headers=["X-Request-ID", "X-RateLimit-Limit", "X-RateLimit-Remaining", "Retry-After"],
        allow_credentials=False,
        max_age=600,
    )

    register_exception_handlers(app)
    # Order matters: concrete routes first, the catch-all ``/{short_code}`` last.
    app.include_router(system.router)
    app.include_router(api_router)
    app.include_router(redirect.router)
    return app
