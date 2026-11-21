from datetime import datetime, timedelta, timezone

import pytest

from app.core.exceptions import ValidationFailed
from app.services.url_validation import normalize_destination, validate_alias, validate_expiry


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "data:text/html,<script>alert(1)</script>",
        "file:///etc/passwd",
        "ftp://example.com/x",
        "//example.com/path",
        "http://",
        "https:///nohost",
        "http://user:pass@example.com/",
        "http://exa mple.com/",
        "http://example.com/a\nb",
        "",
        "   ",
        "http://example.com:99999/",
        "http://.example.com/",
        "http://a..b.com/",
    ],
)
def test_rejects_bad_destinations(url):
    with pytest.raises(ValidationFailed):
        normalize_destination(url)


def test_rejects_too_long():
    with pytest.raises(ValidationFailed):
        normalize_destination("https://example.com/" + "a" * 2100)


def test_rejects_self_reference():
    with pytest.raises(ValidationFailed):
        normalize_destination("https://short.test/abc", blocked_hosts=frozenset({"short.test"}))


def test_normalises_host_case_scheme_and_idn():
    assert normalize_destination("HTTPS://Example.COM/Path?Q=1#Frag") == "https://example.com/Path?Q=1#Frag"
    assert normalize_destination("http://bücher.example/") == "http://xn--bcher-kva.example/"


def test_percent_encodes_non_ascii_and_keeps_existing_escapes():
    out = normalize_destination("https://example.com/日本語?q=é&x=%20y")
    assert out.isascii()
    assert "%E6%97%A5" in out and "q=%C3%A9" in out and "x=%20y" in out


def test_keeps_ports_ipv4_and_ipv6():
    assert normalize_destination("http://127.0.0.1:8080/x") == "http://127.0.0.1:8080/x"
    assert normalize_destination("http://[2001:db8::1]:9000/") == "http://[2001:db8::1]:9000/"


@pytest.mark.parametrize("alias", ["abc", "My-Alias_01", "a" * 32, "ABC", "a-b", "_-_"])
def test_valid_aliases_preserve_case(alias):
    assert validate_alias(alias) == alias


@pytest.mark.parametrize("alias", ["ab", "a" * 33, "has space", "dot.dot", "slash/x", "ünï", "", "a$b"])
def test_invalid_aliases(alias):
    with pytest.raises(ValidationFailed):
        validate_alias(alias)


@pytest.mark.parametrize("alias", ["api", "API", "Health", "metrics", "docs", "login", "ADMIN"])
def test_reserved_aliases_rejected_case_insensitively(alias):
    with pytest.raises(ValidationFailed):
        validate_alias(alias)


def test_expiry_validation():
    now = datetime.now(timezone.utc)
    assert validate_expiry(None) is None
    assert validate_expiry(now + timedelta(hours=1)) is not None
    with pytest.raises(ValidationFailed):
        validate_expiry(now - timedelta(seconds=1))
    with pytest.raises(ValidationFailed):
        validate_expiry(datetime.now() + timedelta(days=1))  # naive
    with pytest.raises(ValidationFailed):
        validate_expiry(now + timedelta(days=4000), max_days=3650)
