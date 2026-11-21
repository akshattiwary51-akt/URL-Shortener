import pytest

from app.core.net import parse_networks, resolve_client_ip
from app.services.cache_service import CacheRecord, decode_record

TRUSTED = parse_networks(["10.0.0.0/8", "192.168.1.1"])


def test_ignores_forwarded_header_when_no_trusted_proxies():
    assert resolve_client_ip("203.0.113.9", "1.2.3.4", []) == "203.0.113.9"


def test_ignores_forwarded_header_from_untrusted_peer():
    assert resolve_client_ip("203.0.113.9", "1.2.3.4", TRUSTED) == "203.0.113.9"


def test_uses_forwarded_header_from_trusted_peer():
    assert resolve_client_ip("10.0.0.5", "198.51.100.2", TRUSTED) == "198.51.100.2"


def test_walks_right_to_left_skipping_trusted_hops():
    # client -> 10.0.0.9 (proxy) -> 10.0.0.5 (peer)
    assert resolve_client_ip("10.0.0.5", "198.51.100.2, 10.0.0.9", TRUSTED) == "198.51.100.2"


def test_spoofed_leftmost_entry_is_not_trusted():
    # An attacker prepends a fake IP; the real client (rightmost untrusted) wins.
    assert resolve_client_ip("10.0.0.5", "6.6.6.6, 198.51.100.2", TRUSTED) == "198.51.100.2"


def test_malformed_entry_fails_closed():
    assert resolve_client_ip("10.0.0.5", "garbage", TRUSTED) == "10.0.0.5"


def test_missing_peer():
    assert resolve_client_ip(None, "1.1.1.1", TRUSTED) == "unknown"


def test_ipv6_peer():
    assert resolve_client_ip("2001:db8::1", None, TRUSTED) == "2001:db8::1"


@pytest.mark.parametrize(
    "raw",
    ["", "not json", "[]", '"str"', "{}", '{"u": 5}', '{"u": "javascript:alert(1)"}', '{"u": "https://a.b", "e": "x"}',
     '{"u": "https://a.b", "e": true}', '{"u": "https://a.b", "e": 1.5}', "{"],
)
def test_decode_record_rejects_malformed(raw):
    assert decode_record(raw) is None


def test_decode_record_roundtrip():
    rec = CacheRecord("https://example.com/x", 1_900_000_000)
    assert decode_record(rec.encode()) == rec
    assert decode_record(CacheRecord("http://a.b", None).encode()) == CacheRecord("http://a.b", None)


def test_record_expiry():
    rec = CacheRecord("https://a.b", 100)
    assert rec.is_expired(100) and rec.is_expired(101) and not rec.is_expired(99.9)
    assert not CacheRecord("https://a.b", None).is_expired(10**12)
