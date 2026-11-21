"""Client IP resolution that only trusts forwarded headers from configured proxies."""

from __future__ import annotations

import ipaddress
from collections.abc import Iterable, Sequence

Network = ipaddress.IPv4Network | ipaddress.IPv6Network
Address = ipaddress.IPv4Address | ipaddress.IPv6Address


def parse_networks(values: Iterable[str]) -> list[Network]:
    return [ipaddress.ip_network(v, strict=False) for v in values]


def _parse(value: str) -> Address | None:
    try:
        return ipaddress.ip_address(value.strip())
    except ValueError:
        return None


def _trusted(addr: Address, networks: Sequence[Network]) -> bool:
    return any(addr in n for n in networks)


def resolve_client_ip(peer: str | None, forwarded_for: str | None, trusted: Sequence[Network]) -> str:
    """Return the originating client IP.

    ``X-Forwarded-For`` is honoured only when the direct peer is a trusted proxy.
    The header is then walked right-to-left, skipping trusted proxies; the first
    untrusted hop is the client. Malformed entries stop the walk (fail closed to
    the last hop that could be validated).
    """
    peer_addr = _parse(peer) if peer else None
    if peer_addr is None:
        return "unknown"
    if not trusted or not forwarded_for or not _trusted(peer_addr, trusted):
        return str(peer_addr)
    hops = [h for h in forwarded_for.split(",") if h.strip()]
    last_valid: Address = peer_addr
    for raw in reversed(hops):
        addr = _parse(raw)
        if addr is None:
            return str(last_valid)
        if not _trusted(addr, trusted):
            return str(addr)
        last_valid = addr
    return str(last_valid)
