"""Signup, verification, password reset, Google sign-in, throttling and deletion"""
import re
from unittest.mock import AsyncMock

import database

PASSWORD = "Passw0rdOk"


def token_from(text: str) -> str:
    return re.search(r"token=([\w.\-]+)", text).group(1)


async def test_health_and_root(client):
    assert (await client.get("/health")).json() == {"status": "ok"}
    assert "endpoints" in (await client.get("/")).json()


async def test_signup_sends_verification_and_verify_marks_user(client, sent_emails):
    r = await client.post("/api/auth/signup", json={"email": "A@Example.com", "name": "Ann", "password": PASSWORD})
    assert r.status_code == 201
    user = r.json()["user"]
    assert user["email"] == "a@example.com"
    assert user["email_verified"] is False
    assert user["plan"] == "free"

    to, subject, text = sent_emails[0]
    assert to == "a@example.com" and "Confirm" in subject

    v = await client.post("/api/auth/verify-email", json={"token": token_from(text)})
    assert v.status_code == 200 and v.json()["email_verified"] is True


async def test_verify_rejects_wrong_token_type(client, sent_emails):
    r = await client.post("/api/auth/signup", json={"email": "b@example.com", "name": "Bob", "password": PASSWORD})
    access = r.json()["access_token"]
    resp = await client.post("/api/auth/verify-email", json={"token": access})
    assert resp.status_code == 400


async def test_password_reset_is_single_use(client, sent_emails):
    await client.post("/api/auth/signup", json={"email": "c@example.com", "name": "Cy", "password": PASSWORD})
    sent_emails.clear()

    r = await client.post("/api/auth/forgot-password", json={"email": "c@example.com"})
    assert r.status_code == 200
    token = token_from(sent_emails[0][2])

    ok = await client.post("/api/auth/reset-password", json={"token": token, "password": "NewPassw0rd"})
    assert ok.status_code == 200 and "access_token" in ok.json()

    login = await client.post("/api/auth/login", json={"email": "c@example.com", "password": "NewPassw0rd"})
    assert login.status_code == 200
    old = await client.post("/api/auth/login", json={"email": "c@example.com", "password": PASSWORD})
    assert old.status_code == 401

    again = await client.post("/api/auth/reset-password", json={"token": token, "password": "Another1Pass"})
    assert again.status_code == 400


async def test_forgot_password_does_not_reveal_accounts(client, sent_emails):
    r = await client.post("/api/auth/forgot-password", json={"email": "nobody@example.com"})
    assert r.status_code == 200
    assert sent_emails == []
    assert "If an account exists" in r.json()["message"]


async def test_reset_rejects_weak_password(client, sent_emails):
    await client.post("/api/auth/signup", json={"email": "d@example.com", "name": "Di", "password": PASSWORD})
    sent_emails.clear()
    await client.post("/api/auth/forgot-password", json={"email": "d@example.com"})
    r = await client.post("/api/auth/reset-password", json={"token": token_from(sent_emails[0][2]), "password": "weak"})
    assert r.status_code == 422


async def test_login_is_rate_limited(client, make_user):
    await make_user(email="e@example.com")
    codes = []
    for _ in range(12):
        r = await client.post("/api/auth/login", json={"email": "e@example.com", "password": "Wrong1234"})
        codes.append(r.status_code)
    assert codes[:10] == [401] * 10
    assert 429 in codes[10:]


async def test_google_login_creates_verified_user(client, monkeypatch):
    claims = {"email": "G@Example.com", "name": "Gina", "aud": "test-google-client", "iss": "accounts.google.com", "email_verified": "true"}
    monkeypatch.setattr("auth_routes.verify_google_credential", AsyncMock(return_value=claims))
    r = await client.post("/api/auth/google", json={"credential": "whatever"})
    assert r.status_code == 200
    user = r.json()["user"]
    assert user["email"] == "g@example.com" and user["email_verified"] is True and user["auth_provider"] == "google"
    # Google-only accounts have no password to log in with
    bad = await client.post("/api/auth/login", json={"email": "g@example.com", "password": PASSWORD})
    assert bad.status_code == 401
    # Second sign-in reuses the account
    again = await client.post("/api/auth/google", json={"credential": "whatever"})
    assert again.json()["user"]["id"] == user["id"]


async def test_google_verification_checks_audience(monkeypatch):
    import auth, httpx
    from fastapi import HTTPException
    import pytest

    class FakeResp:
        status_code = 200
        def json(self):
            return {"aud": "someone-else", "iss": "accounts.google.com", "email": "x@y.com", "email_verified": "true"}

    class FakeClient:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        async def get(self, *a, **k): return FakeResp()

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    with pytest.raises(HTTPException) as exc:
        await auth.verify_google_credential("tok")
    assert exc.value.status_code == 401


async def test_delete_account_erases_everything(client, make_user, mock_ai, db):
    headers, uid = await make_user()
    await client.post("/api/compare", headers=headers, data={"jd_text": "Python developer needed with Docker"},
                      files={"resume_file": ("r.txt", b"Python developer with Docker experience " * 5, "text/plain")})
    assert await db.comparisons.count_documents({"user_id": uid}) == 1

    r = await client.request("DELETE", "/api/auth/account", headers=headers)
    assert r.status_code == 200
    assert await db.users.count_documents({"_id": uid}) == 0
    assert await db.comparisons.count_documents({"user_id": uid}) == 0
    assert await db.usage.count_documents({"user_id": uid}) == 0
    # Token no longer works
    assert (await client.get("/api/auth/me", headers=headers)).status_code == 401


async def test_jwt_secret_required_in_production(monkeypatch):
    import config
    import pytest

    cfg = config.Settings(ENVIRONMENT="production", JWT_SECRET_KEY="")
    with pytest.raises(RuntimeError):
        config._validate(cfg)
