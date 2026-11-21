"""User-Agent and referrer normalisation for analytics (worker side)."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from urllib.parse import urlsplit

import user_agents

DIRECT = "Direct"

_BROWSER_ALIASES = {
    "mobile safari": "Safari",
    "mobile safari ui/wkwebview": "Safari",
    "chrome mobile": "Chrome",
    "chrome mobile ios": "Chrome",
    "chrome mobile webview": "Chrome",
    "firefox mobile": "Firefox",
    "firefox ios": "Firefox",
    "edge mobile": "Edge",
    "opera mini": "Opera",
    "opera mobile": "Opera",
    "samsung internet": "Samsung Internet",
}
_OS_ALIASES = {"mac os x": "macOS", "other": "Unknown"}


@dataclass(frozen=True, slots=True)
class UaInfo:
    browser: str
    os: str
    device: str  # desktop | mobile | tablet | bot | unknown


_UNKNOWN = UaInfo("Unknown", "Unknown", "unknown")


@lru_cache(maxsize=8192)
def parse_user_agent(ua: str) -> UaInfo:
    """Classify a User-Agent. Cached: real traffic has few distinct UA strings."""
    if not ua:
        return _UNKNOWN
    parsed = user_agents.parse(ua)
    family = (parsed.browser.family or "").strip()
    browser = _BROWSER_ALIASES.get(family.lower(), family) if family and family != "Other" else "Unknown"
    os_family = (parsed.os.family or "").strip()
    os_name = _OS_ALIASES.get(os_family.lower(), os_family) or "Unknown"
    if parsed.is_bot:
        device = "bot"
    elif parsed.is_tablet:
        device = "tablet"
    elif parsed.is_mobile:
        device = "mobile"
    elif parsed.is_pc:
        device = "desktop"
    else:
        device = "unknown"
    return UaInfo(browser[:64], os_name[:64], device)


def normalize_referrer(raw: str | None) -> str:
    """Reduce a Referer URL to its bare lowercase host (no path/query, which may hold PII)."""
    if not raw:
        return DIRECT
    try:
        host = urlsplit(raw.strip()).hostname
    except ValueError:
        return DIRECT
    if not host:
        return DIRECT
    host = host.lower()
    if host.startswith("www."):
        host = host[4:]
    return host[:255] or DIRECT
