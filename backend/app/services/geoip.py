"""Optional local GeoIP (MaxMind MMDB) country lookup.

Resolution happens in the API process at enqueue time so that raw IP addresses
never have to be written to the event stream (see ARCHITECTURE.md, Deviations).
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class GeoResolver:
    def __init__(self, reader) -> None:  # type: ignore[no-untyped-def]
        self._reader = reader

    def country(self, ip: str) -> str | None:
        try:
            record = self._reader.get(ip)
        except (ValueError, OSError):
            return None
        if not isinstance(record, dict):
            return None
        iso = (record.get("country") or {}).get("iso_code")
        return iso.upper() if isinstance(iso, str) and len(iso) == 2 else None

    def close(self) -> None:
        close = getattr(self._reader, "close", None)
        if close:
            close()


def build_geo_resolver(path: str | None) -> GeoResolver | None:
    if not path:
        return None
    try:
        import maxminddb

        return GeoResolver(maxminddb.open_database(path))
    except Exception:  # noqa: BLE001 - optional feature must never block startup
        logger.exception("could not open GeoIP database; country analytics disabled", extra={"path": path})
        return None
