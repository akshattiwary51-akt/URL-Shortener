import pytest

from app.services.click_events import ClickEvent, MalformedEvent
from app.services.event_parsing import DIRECT, normalize_referrer, parse_user_agent

CHROME_WIN = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
IPHONE = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
IPAD = "Mozilla/5.0 (iPad; CPU OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
FIREFOX_LINUX = "Mozilla/5.0 (X11; Linux x86_64; rv:121.0) Gecko/20100101 Firefox/121.0"
GOOGLEBOT = "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"


def test_desktop_chrome():
    ua = parse_user_agent(CHROME_WIN)
    assert (ua.browser, ua.os, ua.device) == ("Chrome", "Windows", "desktop")


def test_mobile_safari_is_normalised_to_safari():
    ua = parse_user_agent(IPHONE)
    assert ua.browser == "Safari" and ua.device == "mobile" and ua.os == "iOS"


def test_tablet_and_linux_firefox_and_bot():
    assert parse_user_agent(IPAD).device == "tablet"
    ua = parse_user_agent(FIREFOX_LINUX)
    assert (ua.browser, ua.device) == ("Firefox", "desktop") and "Linux" in ua.os
    assert parse_user_agent(GOOGLEBOT).device == "bot"


@pytest.mark.parametrize("ua", ["", "garbage", "\x00\x01", "a" * 5000])
def test_never_raises_on_junk(ua):
    info = parse_user_agent(ua)
    assert info.browser and info.os and info.device


@pytest.mark.parametrize(
    "raw,expected",
    [
        (None, DIRECT),
        ("", DIRECT),
        ("   ", DIRECT),
        ("https://www.Google.com/search?q=secret+term", "google.com"),
        ("http://t.co/abc", "t.co"),
        ("https://news.ycombinator.com/item?id=1", "news.ycombinator.com"),
        ("not a url", DIRECT),
        ("android-app://com.google.android.gm", "com.google.android.gm"),
        ("http://[::1", DIRECT),
    ],
)
def test_referrer_normalisation_keeps_only_the_host(raw, expected):
    assert normalize_referrer(raw) == expected


def test_event_roundtrip_and_validation():
    ev = ClickEvent("e" * 32, "abc1234", 1_700_000_000_000, "UA", "h" * 32, "https://r.example/", "IN")
    assert ClickEvent.from_fields(ev.to_fields()) == ev
    no_country = ClickEvent("e1", "abc1234", 1_700_000_000_000, "", "", "")
    assert "country" not in no_country.to_fields()


@pytest.mark.parametrize(
    "fields",
    [
        {},
        {"event_id": "x", "short_code": "abc1234"},  # no ts
        {"event_id": "x", "short_code": "abc1234", "ts": "nan"},
        {"event_id": "x", "short_code": "ab", "ts": "1700000000000"},
        {"event_id": "", "short_code": "abc1234", "ts": "1700000000000"},
        {"event_id": "x" * 65, "short_code": "abc1234", "ts": "1700000000000"},
        {"event_id": "x", "short_code": "abc1234", "ts": "-5"},
        {"event_id": "x", "short_code": "abc1234", "ts": "99999999999999999"},
        {"event_id": "x", "short_code": "bad code", "ts": "1700000000000"},
    ],
)
def test_malformed_events_rejected(fields):
    with pytest.raises(MalformedEvent):
        ClickEvent.from_fields(fields)


def test_invalid_country_is_dropped_not_fatal():
    ev = ClickEvent.from_fields({"event_id": "x", "short_code": "abc1234", "ts": "1700000000000", "country": "usa"})
    assert ev.country is None
