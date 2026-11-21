"""Domain errors and the consistent JSON error envelope.

Every error response has the shape::

    {"error": {"code": "...", "message": "...", "details": [...], "request_id": "..."}}
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import DBAPIError, InterfaceError, OperationalError
from sqlalchemy.exc import TimeoutError as SATimeoutError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.logging import request_id_ctx

logger = logging.getLogger(__name__)


class AppError(Exception):
    status_code = 500
    code = "internal_error"
    message = "An unexpected error occurred."

    def __init__(
        self,
        message: str | None = None,
        *,
        code: str | None = None,
        headers: dict[str, str] | None = None,
        details: list[dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(message or self.message)
        self.message = message or self.message
        self.code = code or self.code
        self.headers = headers or {}
        self.details = details


class ValidationFailed(AppError):
    status_code = 422
    code = "validation_error"
    message = "The request is invalid."


class AuthenticationError(AppError):
    status_code = 401
    code = "unauthorized"
    message = "Authentication required."

    def __init__(self, message: str | None = None, **kw: Any) -> None:
        super().__init__(message, **kw)
        self.headers.setdefault("WWW-Authenticate", "Bearer")


class NotFoundError(AppError):
    status_code = 404
    code = "not_found"
    message = "Resource not found."


class GoneError(AppError):
    status_code = 410
    code = "link_gone"
    message = "This link has expired or been disabled."


class ConflictError(AppError):
    status_code = 409
    code = "conflict"
    message = "Conflict."


class AliasConflict(ConflictError):
    code = "alias_conflict"
    message = "That alias is already in use."


class EmailTaken(ConflictError):
    code = "email_taken"
    message = "An account with that email already exists."


class PayloadTooLarge(AppError):
    status_code = 413
    code = "payload_too_large"
    message = "Request body too large."


class RateLimitExceeded(AppError):
    status_code = 429
    code = "rate_limited"
    message = "Too many requests."


class ServiceUnavailable(AppError):
    status_code = 503
    code = "service_unavailable"
    message = "Service temporarily unavailable."


class IdSpaceExhausted(AppError):
    status_code = 503
    code = "id_space_exhausted"
    message = "The short code space is exhausted."


def is_db_unavailable(exc: BaseException) -> bool:
    """True for connectivity-type failures (as opposed to bugs / constraint errors)."""
    if isinstance(exc, (OperationalError, InterfaceError, SATimeoutError, OSError, asyncio.TimeoutError)):
        return True
    if isinstance(exc, DBAPIError) and exc.connection_invalidated:
        return True
    return False


def error_body(code: str, message: str, details: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {"code": code, "message": message, "request_id": request_id_ctx.get()}
    if details:
        body["details"] = details
    return {"error": body}


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            error_body(exc.code, exc.message, exc.details), status_code=exc.status_code, headers=exc.headers
        )

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        # Never echo ``input`` back: it may contain passwords or tokens.
        details = [
            {"loc": [str(p) for p in e.get("loc", ())], "message": e.get("msg", ""), "type": e.get("type", "")}
            for e in exc.errors()
        ]
        return JSONResponse(
            error_body("validation_error", "The request is invalid.", details), status_code=422
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        codes = {400: "bad_request", 401: "unauthorized", 403: "forbidden", 404: "not_found", 405: "method_not_allowed"}
        return JSONResponse(
            error_body(codes.get(exc.status_code, "http_error"), str(exc.detail)),
            status_code=exc.status_code,
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception) -> JSONResponse:
        if is_db_unavailable(exc):
            logger.error("database unavailable", extra={"error_type": type(exc).__name__})
            return JSONResponse(
                error_body("service_unavailable", "Service temporarily unavailable."),
                status_code=503,
                headers={"Retry-After": "5"},
            )
        logger.exception("unhandled exception")
        return JSONResponse(error_body("internal_error", "An unexpected error occurred."), status_code=500)
