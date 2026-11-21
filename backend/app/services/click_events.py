"""Click event model shared by the producer (API) and the consumer (worker)."""

from __future__ import annotations

import re
from dataclasses import dataclass

_COUNTRY_RE = re.compile(r"^[A-Z]{2}$")
_CODE_RE = re.compile(r"^[A-Za-z0-9_-]{3,32}$")


class MalformedEvent(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ClickEvent:
    event_id: str
    short_code: str
    ts_ms: int
    user_agent: str
    ip_hash: str
    referrer: str
    country: str | None = None

    def to_fields(self) -> dict[str, str]:
        fields = {
            "event_id": self.event_id,
            "short_code": self.short_code,
            "ts": str(self.ts_ms),
            "ua": self.user_agent,
            "ip_hash": self.ip_hash,
            "ref": self.referrer,
        }
        if self.country:
            fields["country"] = self.country
        return fields

    @classmethod
    def from_fields(cls, fields: dict[str, str]) -> "ClickEvent":
        """Validate a stream entry. Raises :class:`MalformedEvent` on any problem."""
        try:
            event_id = fields["event_id"]
            short_code = fields["short_code"]
            ts_ms = int(fields["ts"])
        except (KeyError, ValueError, TypeError) as exc:
            raise MalformedEvent(f"missing or invalid required field: {exc!r}") from exc
        if not event_id or len(event_id) > 64:
            raise MalformedEvent("invalid event_id")
        if not _CODE_RE.fullmatch(short_code):
            raise MalformedEvent("invalid short_code")
        if ts_ms <= 0 or ts_ms > 32503680000000:  # year 3000
            raise MalformedEvent("invalid timestamp")
        country = fields.get("country") or None
        if country is not None and not _COUNTRY_RE.fullmatch(country):
            country = None
        return cls(
            event_id=event_id,
            short_code=short_code,
            ts_ms=ts_ms,
            user_agent=(fields.get("ua") or "")[:512],
            ip_hash=(fields.get("ip_hash") or "")[:64],
            referrer=(fields.get("ref") or "")[:512],
            country=country,
        )
