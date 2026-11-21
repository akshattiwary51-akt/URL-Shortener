"""Pure-ASGI middleware: correlation ID, body-size limit, security headers, access log, HTTP metrics.

Implemented as raw ASGI (not ``BaseHTTPMiddleware``) to keep per-request overhead
minimal on the redirect hot path.
"""

from __future__ import annotations

import json
import logging
import re
import time
import uuid

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.config import Settings
from app.core.logging import request_id_ctx
from app.monitoring.metrics import AppMetrics

logger = logging.getLogger("app.access")
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._-]{8,64}$")
_DOC_PATHS = ("/docs", "/redoc")

_API_CSP = "default-src 'none'; frame-ancestors 'none'"
_SECURITY_HEADERS = {
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
    "referrer-policy": "no-referrer",
    "cross-origin-resource-policy": "same-site",
    "permissions-policy": "geolocation=(), microphone=(), camera=()",
}


class _BodyTooLarge(Exception):
    pass


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp, settings: Settings, metrics: AppMetrics) -> None:
        self.app = app
        self._max_body = settings.max_request_body_bytes
        self._access_log = settings.access_log_enabled
        self._hsts = settings.hsts_enabled
        self._metrics = metrics

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = None
        content_length = None
        for name, value in scope["headers"]:
            if name == b"x-request-id":
                candidate = value.decode("latin-1")
                if _REQUEST_ID_RE.fullmatch(candidate):
                    request_id = candidate
            elif name == b"content-length":
                content_length = value
        request_id = request_id or uuid.uuid4().hex
        token = request_id_ctx.set(request_id)
        started = time.perf_counter()
        path = scope["path"]
        status_holder = {"code": 500, "started": False}

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                status_holder["code"] = message["status"]
                status_holder["started"] = True
                headers = MutableHeaders(scope=message)
                headers["X-Request-ID"] = request_id
                for k, v in _SECURITY_HEADERS.items():
                    headers.setdefault(k, v)
                if not path.startswith(_DOC_PATHS):  # Swagger/ReDoc need CDN assets
                    headers.setdefault("content-security-policy", _API_CSP)
                if self._hsts:
                    headers.setdefault("strict-transport-security", "max-age=31536000; includeSubDomains")
            await send(message)

        received = 0

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self._max_body:
                    raise _BodyTooLarge
            return message

        try:
            if content_length is not None and content_length.isdigit() and int(content_length) > self._max_body:
                await self._reject_large(send_wrapper)
            else:
                try:
                    await self.app(scope, limited_receive, send_wrapper)
                except _BodyTooLarge:
                    if not status_holder["started"]:
                        await self._reject_large(send_wrapper)
        finally:
            duration = time.perf_counter() - started
            route = scope.get("route")
            template = getattr(route, "path", None) or "unmatched"
            code = status_holder["code"]
            if template != "/{short_code}":  # redirects have a dedicated histogram
                self._metrics.http_latency.labels(scope["method"], template, str(code)).observe(duration)
            if self._access_log:
                logger.info(
                    "request",
                    extra={
                        "method": scope["method"],
                        "path": path,
                        "status": code,
                        "duration_ms": round(duration * 1000, 2),
                    },
                )
            request_id_ctx.reset(token)

    @staticmethod
    async def _reject_large(send: Send) -> None:
        body = json.dumps(
            {"error": {"code": "payload_too_large", "message": "Request body too large.", "request_id": request_id_ctx.get()}}
        ).encode()
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())],
            }
        )
        await send({"type": "http.response.body", "body": body})
