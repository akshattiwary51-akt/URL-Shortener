import base64
import json
import time

import jwt

from tests.conftest import build_client, register_and_login


async def test_register_login_me(client):
    r = await client.post("/api/v1/auth/register", json={"email": "Bob@Example.com", "password": "s3cret-pass"})
    assert r.status_code == 201
    body = r.json()
    assert body["email"] == "bob@example.com" and "password" not in body and "hashed_password" not in body

    r = await client.post("/api/v1/auth/login", json={"email": "BOB@example.com", "password": "s3cret-pass"})
    assert r.status_code == 200
    data = r.json()
    assert data["token_type"] == "bearer" and data["expires_in"] == 3600

    me = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {data['access_token']}"})
    assert me.status_code == 200 and me.json()["email"] == "bob@example.com"


async def test_password_is_stored_as_argon2_hash(client):
    from sqlalchemy import select

    from app.database.models import User

    await register_and_login(client, "carol@example.com", "another-pass-123")
    async with client.app_ctx.sessionmaker() as s:
        user = (await s.execute(select(User))).scalar_one()
    assert user.hashed_password.startswith("$argon2id$")
    assert "another-pass-123" not in user.hashed_password


async def test_duplicate_email_conflict(client):
    await register_and_login(client)
    r = await client.post("/api/v1/auth/register", json={"email": "alice@example.com", "password": "whatever-123"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "email_taken"


async def test_validation_errors_do_not_echo_the_password(client):
    r = await client.post("/api/v1/auth/register", json={"email": "bad", "password": "zq9-pw"})
    assert r.status_code == 422
    assert "zq9-pw" not in r.text
    assert r.json()["error"]["code"] == "validation_error"
    assert "input" not in json.dumps(r.json())


async def test_register_rejects_unknown_fields(client):
    r = await client.post(
        "/api/v1/auth/register", json={"email": "x@example.com", "password": "long-enough-pw", "is_admin": True}
    )
    assert r.status_code == 422


async def test_wrong_password_and_unknown_user_are_indistinguishable(client):
    await register_and_login(client)
    a = await client.post("/api/v1/auth/login", json={"email": "alice@example.com", "password": "wrong-password"})
    b = await client.post("/api/v1/auth/login", json={"email": "nobody@example.com", "password": "wrong-password"})
    assert a.status_code == b.status_code == 401
    assert a.json()["error"]["message"] == b.json()["error"]["message"]
    assert a.headers["www-authenticate"] == "Bearer"


async def test_protected_routes_require_a_valid_token(client):
    assert (await client.get("/api/v1/auth/me")).status_code == 401
    assert (await client.get("/api/v1/urls")).status_code == 401
    assert (await client.get("/api/v1/dashboard/summary")).status_code == 401
    r = await client.get("/api/v1/auth/me", headers={"Authorization": "Bearer not.a.token"})
    assert r.status_code == 401 and r.json()["error"]["code"] == "invalid_token"


async def test_expired_token_is_rejected():
    async with build_client(access_token_expire_minutes=-1) as c:
        headers = await register_and_login(c)
        r = await c.get("/api/v1/auth/me", headers=headers)
        assert r.status_code == 401 and r.json()["error"]["code"] == "token_expired"


async def test_tampered_and_alg_none_tokens_are_rejected(client):
    headers = await register_and_login(client)
    token = headers["Authorization"].split()[1]
    head, payload, sig = token.split(".")
    forged_payload = json.loads(base64.urlsafe_b64decode(payload + "=="))
    forged_payload["sub"] = "999"
    forged = base64.urlsafe_b64encode(json.dumps(forged_payload).encode()).decode().rstrip("=")
    assert (await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {head}.{forged}.{sig}"})).status_code == 401

    none_token = jwt.encode({"sub": "1", "exp": int(time.time()) + 600, "iss": "url-shortener"}, key=None, algorithm="none")
    assert (await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {none_token}"})).status_code == 401

    wrong_key = jwt.encode({"sub": "1", "exp": int(time.time()) + 600, "iss": "url-shortener"}, "x" * 40, algorithm="HS256")
    assert (await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {wrong_key}"})).status_code == 401


async def test_change_password_and_delete_account(client):
    headers = await register_and_login(client)
    r = await client.post(
        "/api/v1/auth/change-password", headers=headers, json={"current_password": "nope-nope-nope", "new_password": "brand-new-pass1"}
    )
    assert r.status_code == 401
    r = await client.post(
        "/api/v1/auth/change-password", headers=headers, json={"current_password": "correct-horse-battery", "new_password": "brand-new-pass1"}
    )
    assert r.status_code == 204
    assert (await client.post("/api/v1/auth/login", json={"email": "alice@example.com", "password": "correct-horse-battery"})).status_code == 401
    assert (await client.post("/api/v1/auth/login", json={"email": "alice@example.com", "password": "brand-new-pass1"})).status_code == 200

    created = await client.post("/api/v1/urls", headers=headers, json={"original_url": "https://example.com/a"})
    code = created.json()["short_code"]
    assert (await client.get(f"/{code}")).status_code == 302
    assert (await client.delete("/api/v1/auth/me", headers=headers)).status_code == 204
    assert (await client.get(f"/{code}")).status_code == 404  # URLs and cache are gone with the account
    assert (await client.get("/api/v1/auth/me", headers=headers)).status_code == 401
