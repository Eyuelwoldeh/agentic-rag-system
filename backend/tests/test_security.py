"""Auth, roles, CSRF, throttling, and the OAuth flow, exercised through the real FastAPI app."""
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi.testclient import TestClient

from app import connectors, oauth
from app.connectors import base, notion
from app.db import connect
from app.main import app
from app.security import decrypt, encrypt, hash_password, verify_password

CSRF = {"x-requested-with": "agentic-rag"}
ADMIN = {"email": "admin@acme.test", "password": "correct horse battery"}


def client() -> TestClient:
    return TestClient(app, headers=CSRF)


@pytest.fixture(scope="module")
def admin():
    with connect() as conn:
        conn.execute("TRUNCATE users CASCADE")
    c = client()
    assert c.get("/api/auth/status").json() == {"needs_setup": True}
    assert c.post("/api/auth/setup", json=ADMIN).status_code == 200
    return c


@pytest.fixture
def no_bg_sync(monkeypatch):
    monkeypatch.setattr(connectors, "sync_in_background", lambda *a, **k: None)


def test_password_hashing_and_encryption():
    h = hash_password("hunter2hunter2")
    assert verify_password("hunter2hunter2", h) and not verify_password("wrong", h) and "hunter2" not in h
    assert decrypt(encrypt({"t": "secret"})) == {"t": "secret"}


def test_setup_only_once(admin):
    assert admin.get("/api/auth/me").json()["role"] == "admin"
    assert client().post("/api/auth/setup", json={"email": "evil@x.test", "password": "0123456789ab"}).status_code == 409


def test_everything_requires_sign_in(admin):
    anon = client()
    for method, path in [("GET", "/api/sources"), ("GET", "/api/runs"), ("POST", "/api/chat"), ("GET", "/api/connectors")]:
        assert anon.request(method, path, json={"question": "x"}).status_code == 401, path


def test_csrf_header_required(admin):
    r = admin.post("/api/auth/logout", headers={"x-requested-with": ""})
    assert r.status_code == 403 and "CSRF" in r.json()["detail"]


def test_login_throttled():
    c = client()
    creds = {"email": "throttle@acme.test", "password": "nope"}
    codes = [c.post("/api/auth/login", json=creds).status_code for _ in range(9)]
    assert codes[:8] == [401] * 8 and codes[8] == 429


def test_member_permissions(admin):
    created = admin.post("/api/users", json={"email": "mem@acme.test", "role": "member"}).json()
    member = client()
    assert member.post("/api/auth/login", json={"email": "mem@acme.test", "password": created["temporary_password"]}).status_code == 200
    assert member.get("/api/sources").status_code == 200  # members can use the knowledge base
    for method, path in [("GET", "/api/connectors"), ("GET", "/api/users"), ("GET", "/api/oauth/apps"),
                         ("POST", "/api/connections")]:
        assert member.request(method, path, json={}).status_code == 403, path
    # password change signs out other sessions
    other = client()
    other.post("/api/auth/login", json={"email": "mem@acme.test", "password": created["temporary_password"]})
    # a wrong current password is an input error (400), not "signed out" (401): the app must not bounce to /login
    wrong = member.post("/api/auth/password", json={"current": "nope", "new": "a-much-better-pw"})
    assert wrong.status_code == 400 and member.get("/api/auth/me").status_code == 200
    assert member.post("/api/auth/password", json={"current": created["temporary_password"], "new": "a-much-better-pw"}).status_code == 200
    assert other.get("/api/auth/me").status_code == 401
    assert member.get("/api/auth/me").status_code == 200


def test_oauth_app_secret_is_write_only(admin):
    assert admin.put("/api/oauth/apps/notion", json={"client_id": "cid", "client_secret": "csecret"}).status_code == 200
    apps = {a["provider"]: a for a in admin.get("/api/oauth/apps").json()}
    assert apps["notion"]["configured"] and "csecret" not in str(apps)
    assert apps["notion"]["redirect_uri"].endswith("/api/oauth/notion/callback")
    with connect() as conn:
        assert b"csecret" not in bytes(conn.execute("SELECT client_secret FROM oauth_apps WHERE provider='notion'").fetchone()[0])


def _start(c, provider="notion"):
    r = c.get(f"/api/oauth/{provider}/start", params={"name": "Acme wiki"}, follow_redirects=False)
    assert r.status_code == 303
    loc = urlparse(r.headers["location"])
    return loc, parse_qs(loc.query)


def test_oauth_flow_end_to_end(admin, monkeypatch, no_bg_sync):
    admin.put("/api/oauth/apps/notion", json={"client_id": "cid", "client_secret": "csecret"})
    loc, q = _start(admin)
    assert loc.netloc == "api.notion.com" and q["client_id"] == ["cid"] and q["code_challenge_method"] == ["S256"]
    state = q["state"][0]

    seen = {}

    def token_endpoint(req: httpx.Request):
        seen["auth"], seen["body"] = req.headers["authorization"], req.content
        return httpx.Response(200, json={"access_token": "ntn_oauth_secret", "workspace_name": "Acme"})

    monkeypatch.setattr(base, "http", httpx.Client(transport=httpx.MockTransport(lambda req: (
        token_endpoint(req) if req.url.path == "/v1/oauth/token"
        else httpx.Response(200, json={"bot": {"workspace_name": "Acme"}})))))

    r = admin.get("/api/oauth/notion/callback", params={"state": state, "code": "abc"}, follow_redirects=False)
    assert r.status_code == 303 and "connected=" in r.headers["location"], r.headers["location"]
    assert seen["auth"].startswith("Basic ") and b"code_verifier" in seen["body"]  # client secret + PKCE sent
    conn_id = int(parse_qs(urlparse(r.headers["location"]).query)["connected"][0])
    with connect() as conn:
        blob, account = conn.execute("SELECT credentials, account FROM connections WHERE id = %s", (conn_id,)).fetchone()
    assert b"ntn_oauth_secret" not in bytes(blob) and account == "Acme"
    assert oauth.load_credentials(conn_id)["access_token"] == "ntn_oauth_secret"
    listed = admin.get("/api/connectors").json()["connections"]
    assert "ntn_oauth_secret" not in str(listed)  # never sent to the browser

    # state is single-use
    again = admin.get("/api/oauth/notion/callback", params={"state": state, "code": "abc"}, follow_redirects=False)
    assert "expired" in again.headers["location"]


def test_oauth_state_bound_to_user(admin, no_bg_sync):
    admin.put("/api/oauth/apps/notion", json={"client_id": "cid", "client_secret": "csecret"})
    _, q = _start(admin)
    created = admin.post("/api/users", json={"email": "admin2@acme.test", "role": "admin"}).json()
    other = client()
    other.post("/api/auth/login", json={"email": "admin2@acme.test", "password": created["temporary_password"]})
    r = other.get("/api/oauth/notion/callback", params={"state": q["state"][0], "code": "abc"}, follow_redirects=False)
    assert "doesn" in r.headers["location"] or "match" in r.headers["location"]


def test_expired_token_is_refreshed(admin, monkeypatch):
    admin.put("/api/oauth/apps/gdrive", json={"client_id": "gid", "client_secret": "gsecret"})
    expired = {"access_token": "old", "refresh_token": "r1",
               "expires_at": (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()}
    with connect() as conn:
        conn_id = conn.execute("INSERT INTO connections (kind, name, credentials) VALUES ('gdrive', 'd', %s) RETURNING id",
                               (encrypt(expired),)).fetchone()[0]
    monkeypatch.setattr(base, "http", httpx.Client(transport=httpx.MockTransport(
        lambda req: httpx.Response(200, json={"access_token": "new", "expires_in": 3600}))))
    fresh = oauth.load_credentials(conn_id)
    assert fresh["access_token"] == "new" and fresh["refresh_token"] == "r1"  # refresh token kept
    assert oauth.load_credentials(conn_id)["access_token"] == "new"  # persisted


def test_paste_token_connection(admin, monkeypatch, no_bg_sync):
    monkeypatch.setattr(base, "http", httpx.Client(transport=httpx.MockTransport(
        lambda req: httpx.Response(200, json={"bot": {"workspace_name": "Pasted"}}))))
    r = admin.post("/api/connections", json={"kind": "notion", "name": "wiki", "credential": "ntn_pasted"})
    assert r.status_code == 200
    with connect() as conn:
        assert conn.execute("SELECT account FROM connections WHERE id = %s", (r.json()["id"],)).fetchone()[0] == "Pasted"
    assert admin.post("/api/connections", json={"kind": "notion", "credential": ""}).status_code == 422


def test_members_see_only_their_runs(admin):
    with connect() as conn:
        admin_id = conn.execute("SELECT id FROM users WHERE email = %s", (ADMIN["email"],)).fetchone()[0]
        conn.execute("INSERT INTO runs (question, user_id) VALUES ('admin-only question', %s)", (admin_id,))
    created = admin.post("/api/users", json={"email": "viewer@acme.test", "role": "member"}).json()
    member = client()
    member.post("/api/auth/login", json={"email": "viewer@acme.test", "password": created["temporary_password"]})
    assert all(r["question"] != "admin-only question" for r in member.get("/api/runs").json())
    assert any(r["question"] == "admin-only question" for r in admin.get("/api/runs").json())
