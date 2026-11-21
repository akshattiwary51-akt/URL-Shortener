"""Validation and normalisation of destinations, aliases and expiry dates."""

from __future__ import annotations

import ipaddress
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, urlsplit, urlunsplit

from app.core.exceptions import ValidationFailed
from app.core.reserved import is_reserved

ALIAS_RE = re.compile(r"^[A-Za-z0-9_-]{3,32}$")
_ALLOWED_SCHEMES = {"http", "https"}
_PATH_SAFE = "/%:@!$&'()*+,;=-._~"
_QUERY_SAFE = "=&%/?:@!$'()*+,;-._~"


def _fail(message: str, field: str) -> ValidationFailed:
    return ValidationFailed(message, details=[{"loc": ["body", field], "message": message, "type": "value_error"}])


def normalize_destination(raw: str, *, max_length: int = 2048, blocked_hosts: frozenset[str] = frozenset()) -> str:
    """Return a canonical ASCII http(s) URL or raise :class:`ValidationFailed`.

    * only ``http`` / ``https`` schemes (blocks ``javascript:``, ``data:``, ``file:`` …)
    * no embedded credentials, no whitespace/control characters
    * IDN hosts are punycoded and non-ASCII path/query characters percent-encoded so the
      value is always safe to place in a ``Location`` header
    * links pointing back at this service are rejected (redirect loops)
    """
    url = (raw or "").strip()
    if not url:
        raise _fail("URL must not be empty.", "original_url")
    if len(url) > max_length:
        raise _fail(f"URL must be at most {max_length} characters.", "original_url")
    if any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in url):
        raise _fail("URL must not contain whitespace or control characters.", "original_url")
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        raise _fail("URL is malformed.", "original_url") from None
    if parts.scheme.lower() not in _ALLOWED_SCHEMES:
        raise _fail("Only http and https URLs are allowed.", "original_url")
    host = parts.hostname
    if not host:
        raise _fail("URL must include a host.", "original_url")
    if parts.username is not None or parts.password is not None:
        raise _fail("URLs with embedded credentials are not allowed.", "original_url")

    try:
        ip = ipaddress.ip_address(host)
        host_ascii = f"[{ip.compressed}]" if ip.version == 6 else str(ip)
    except ValueError:
        try:
            host_ascii = host.encode("idna").decode("ascii").lower()
        except UnicodeError:
            raise _fail("URL host is invalid.", "original_url") from None
        if len(host_ascii) > 253 or ".." in host_ascii or host_ascii.startswith("."):
            raise _fail("URL host is invalid.", "original_url")

    if host_ascii.strip("[]") in blocked_hosts:
        raise _fail("URLs pointing to this service are not allowed.", "original_url")

    netloc = host_ascii + (f":{port}" if port else "")
    normalized = urlunsplit(
        (
            parts.scheme.lower(),
            netloc,
            quote(parts.path, safe=_PATH_SAFE),
            quote(parts.query, safe=_QUERY_SAFE),
            quote(parts.fragment, safe=_QUERY_SAFE),
        )
    )
    if len(normalized) > max_length:
        raise _fail(f"URL must be at most {max_length} characters after normalisation.", "original_url")
    return normalized


def validate_alias(alias: str) -> str:
    """Alias rules: [A-Za-z0-9_-], 3-32 chars, not reserved. Case is preserved."""
    if not ALIAS_RE.fullmatch(alias):
        raise _fail("Alias must be 3-32 characters: letters, digits, hyphens or underscores.", "custom_alias")
    if is_reserved(alias):
        raise _fail("That alias is reserved.", "custom_alias")
    return alias


def validate_expiry(expires_at: datetime | None, *, now: datetime | None = None, max_days: int = 3650) -> datetime | None:
    if expires_at is None:
        return None
    if expires_at.tzinfo is None:
        raise _fail("expires_at must include a timezone.", "expires_at")
    now = now or datetime.now(timezone.utc)
    if expires_at <= now:
        raise _fail("expires_at must be in the future.", "expires_at")
    if expires_at > now + timedelta(days=max_days):
        raise _fail(f"expires_at must be within {max_days} days.", "expires_at")
    return expires_at.astimezone(timezone.utc)
