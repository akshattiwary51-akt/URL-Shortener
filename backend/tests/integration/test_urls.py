import asyncio
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.database.models import Url
from tests.conftest import register_and_login


def iso(**delta):
    return (datetime.now(timezone.utc) + timedelta(**delta)).isoformat()


async def test_anonymous_creation_returns_seven_char_code(client):
    r = await client.post("/api/v1/urls", json={"original_url": "https://example.com/page?a=1"})
    assert r.status_code == 201
    body = r.json()
    assert len(body["short_code"]) == 7 and body["short_code"].isalnum()
    assert body["short_url"] == f"http://short.test/{body['short_code']}"
    assert body["is_owned"] is False and body["total_clicks"] == 0 and body["is_active"] is True
    assert r.headers["x-ratelimit-limit"] and r.headers["x-ratelimit-remaining"]


async def test_creation_rejects_mass_assignment_and_bad_input(client, auth):
    for payload in (
        {"original_url": "https://example.com", "owner_id": 1},
        {"original_url": "https://example.com", "total_clicks": 999},
        {"original_url": "https://example.com", "short_code": "hijack1"},
        {"original_url": "javascript:alert(1)"},
        {"original_url": "https://example.com", "expires_at": iso(days=-1)},
        {"original_url": "https://example.com", "expires_at": "2030-01-01T00:00:00"},  # naive
        {"original_url": "https://example.com", "custom_alias": "no spaces"},
        {"original_url": "https://example.com", "custom_alias": "ab"},
        {"original_url": "https://example.com", "custom_alias": "api"},
        {"original_url": "https://short.test/abc"},  # points at ourselves
        {},
    ):
        r = await client.post("/api/v1/urls", json=payload, headers=auth)
        assert r.status_code == 422, (payload, r.text)
        assert r.json()["error"]["code"] == "validation_error"


async def test_custom_alias_preserves_case_and_conflicts_with_409(client, auth):
    r = await client.post("/api/v1/urls", json={"original_url": "https://example.com/1", "custom_alias": "MyBrand-2026"}, headers=auth)
    assert r.status_code == 201 and r.json()["short_code"] == "MyBrand-2026"
    assert (await client.get("/MyBrand-2026")).status_code == 302
    assert (await client.get("/mybrand-2026")).status_code == 404  # case-sensitive

    dup = await client.post("/api/v1/urls", json={"original_url": "https://example.com/2", "custom_alias": "MyBrand-2026"})
    assert dup.status_code == 409 and dup.json()["error"]["code"] == "alias_conflict"


async def test_simultaneous_alias_creation_has_exactly_one_winner(client):
    results = await asyncio.gather(
        *[client.post("/api/v1/urls", json={"original_url": f"https://example.com/{i}", "custom_alias": "race-alias"}) for i in range(25)]
    )
    codes = sorted(r.status_code for r in results)
    assert codes.count(201) == 1 and codes.count(409) == 24
    async with client.app_ctx.sessionmaker() as s:
        assert len((await s.execute(select(Url).where(Url.short_code == "race-alias"))).scalars().all()) == 1


async def test_concurrent_generated_creation_never_collides(client):
    results = await asyncio.gather(*[client.post("/api/v1/urls", json={"original_url": f"https://example.com/{i}"}) for i in range(150)])
    assert all(r.status_code == 201 for r in results)
    codes = [r.json()["short_code"] for r in results]
    assert len(set(codes)) == 150


async def test_generated_code_that_collides_with_custom_alias_is_skipped(client):
    ctx = client.app_ctx
    taken = ctx.urls.code_for_id(0)  # the code the allocator would hand out first
    r = await client.post("/api/v1/urls", json={"original_url": "https://example.com/alias", "custom_alias": taken})
    assert r.status_code == 201 and r.json()["short_code"] == taken
    r = await client.post("/api/v1/urls", json={"original_url": "https://example.com/generated"})
    assert r.status_code == 201
    assert r.json()["short_code"] == ctx.urls.code_for_id(1)  # transparently skipped to the next ID
    assert (await client.get(f"/{taken}")).headers["location"] == "https://example.com/alias"


async def test_listing_uses_keyset_pagination_and_search(client, auth):
    for i in range(25):
        await client.post("/api/v1/urls", json={"original_url": f"https://example.com/item-{i}"}, headers=auth)
    await client.post("/api/v1/urls", json={"original_url": "https://other.org/x", "custom_alias": "needle-link"}, headers=auth)

    seen, cursor, pages = [], None, 0
    while True:
        params = {"limit": 10, **({"cursor": cursor} if cursor else {})}
        r = await client.get("/api/v1/urls", params=params, headers=auth)
        assert r.status_code == 200
        body = r.json()
        seen += [u["short_code"] for u in body["items"]]
        pages += 1
        cursor = body["next_cursor"]
        if not cursor:
            break
    assert pages == 3 and len(seen) == 26 and len(set(seen)) == 26
    assert seen[0] == "needle-link"  # newest first

    r = await client.get("/api/v1/urls", params={"q": "needle"}, headers=auth)
    assert [u["short_code"] for u in r.json()["items"]] == ["needle-link"]
    r = await client.get("/api/v1/urls", params={"q": "other.org"}, headers=auth)
    assert len(r.json()["items"]) == 1
    r = await client.get("/api/v1/urls", params={"q": "%"}, headers=auth)  # LIKE wildcards are escaped
    assert r.json()["items"] == []

    assert (await client.get("/api/v1/urls", params={"cursor": "garbage!!"}, headers=auth)).status_code == 422
    assert (await client.get("/api/v1/urls", params={"limit": 0}, headers=auth)).status_code == 422
    assert (await client.get("/api/v1/urls", params={"limit": 101}, headers=auth)).status_code == 422


async def test_ownership_is_enforced_everywhere(client):
    alice = await register_and_login(client, "alice@example.com")
    bob = await register_and_login(client, "bob@example.com")
    code = (await client.post("/api/v1/urls", json={"original_url": "https://example.com/private"}, headers=alice)).json()["short_code"]

    for method, path, kwargs in (
        ("GET", f"/api/v1/urls/{code}", {}),
        ("PATCH", f"/api/v1/urls/{code}", {"json": {"is_active": False}}),
        ("DELETE", f"/api/v1/urls/{code}", {}),
        ("GET", f"/api/v1/analytics/{code}", {}),
        ("GET", f"/api/v1/analytics/{code}/daily", {}),
        ("GET", f"/api/v1/analytics/{code}/browsers", {}),
        ("GET", f"/api/v1/analytics/{code}/devices", {}),
        ("GET", f"/api/v1/analytics/{code}/referrers", {}),
        ("GET", f"/api/v1/analytics/{code}/os", {}),
        ("GET", f"/api/v1/analytics/{code}/countries", {}),
    ):
        r = await client.request(method, path, headers=bob, **kwargs)
        assert r.status_code == 404, (method, path, r.status_code)  # 404, not 403: no existence leak

    assert (await client.get("/api/v1/urls", headers=bob)).json()["items"] == []
    assert (await client.get(f"/api/v1/urls/{code}", headers=alice)).status_code == 200
    assert (await client.get(f"/{code}")).status_code == 302  # Bob's attempts changed nothing


async def test_anonymous_urls_have_no_owner_access(client, auth):
    code = (await client.post("/api/v1/urls", json={"original_url": "https://example.com/anon"})).json()["short_code"]
    assert (await client.get(f"/api/v1/urls/{code}", headers=auth)).status_code == 404
    assert (await client.delete(f"/api/v1/urls/{code}", headers=auth)).status_code == 404


async def test_anonymous_creation_can_be_disabled():
    from tests.conftest import build_client

    async with build_client(anonymous_creation_enabled=False) as c:
        assert (await c.post("/api/v1/urls", json={"original_url": "https://example.com"})).status_code == 401
        headers = await register_and_login(c)
        assert (await c.post("/api/v1/urls", json={"original_url": "https://example.com"}, headers=headers)).status_code == 201


async def test_get_update_delete_flow(client, auth):
    code = (await client.post("/api/v1/urls", json={"original_url": "https://example.com/x"}, headers=auth)).json()["short_code"]
    r = await client.patch(f"/api/v1/urls/{code}", json={"expires_at": iso(days=2)}, headers=auth)
    assert r.status_code == 200 and r.json()["expires_at"] is not None
    r = await client.patch(f"/api/v1/urls/{code}", json={"expires_at": None}, headers=auth)  # explicit null clears
    assert r.json()["expires_at"] is None
    r = await client.patch(f"/api/v1/urls/{code}", json={"is_active": False}, headers=auth)
    assert r.json()["is_active"] is False and r.json()["expires_at"] is None  # untouched fields stay
    assert (await client.patch(f"/api/v1/urls/{code}", json={"owner_id": 5}, headers=auth)).status_code == 422
    assert (await client.delete(f"/api/v1/urls/{code}", headers=auth)).status_code == 204
    assert (await client.get(f"/api/v1/urls/{code}", headers=auth)).status_code == 404
    assert (await client.delete(f"/api/v1/urls/{code}", headers=auth)).status_code == 404


async def test_error_envelope_is_consistent(client):
    r = await client.get("/api/v1/does-not-exist")
    assert r.status_code == 404 and set(r.json()["error"]) >= {"code", "message", "request_id"}
    r = await client.post("/api/v1/urls", content=b"{not json", headers={"content-type": "application/json"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "validation_error"
    r = await client.put("/api/v1/urls")
    assert r.status_code == 405 and r.json()["error"]["code"] == "method_not_allowed"


async def test_oversized_body_is_rejected(client):
    r = await client.post("/api/v1/urls", content=b"x" * 100_000, headers={"content-type": "application/json"})
    assert r.status_code == 413 and r.json()["error"]["code"] == "payload_too_large"


async def test_security_headers_and_request_id(client):
    r = await client.get("/health/live", headers={"X-Request-ID": "trace-abc-12345"})
    assert r.headers["x-request-id"] == "trace-abc-12345"
    assert r.headers["x-content-type-options"] == "nosniff" and r.headers["x-frame-options"] == "DENY"
    assert "default-src 'none'" in r.headers["content-security-policy"]
    r = await client.get("/health/live", headers={"X-Request-ID": "bad id with spaces!"})
    assert r.headers["x-request-id"] != "bad id with spaces!" and len(r.headers["x-request-id"]) == 32


async def test_cors_only_allows_configured_origins(client):
    ok = await client.options(
        "/api/v1/urls", headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "POST"}
    )
    assert ok.headers.get("access-control-allow-origin") == "http://localhost:5173"
    bad = await client.options(
        "/api/v1/urls", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"}
    )
    assert "access-control-allow-origin" not in bad.headers
