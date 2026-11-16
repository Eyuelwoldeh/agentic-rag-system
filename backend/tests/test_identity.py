"""Enterprise identity: groups in access lists, SCIM provisioning, OIDC single sign-on, and the audit log."""
import time
from urllib.parse import parse_qs, urlparse

import httpx
import jwt
import psycopg
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from app import connectors, datasources, groups, notify, sso
from app.connectors import base
from app.db import connect
from app.ingest import drop_tables, ingest_spreadsheet, upsert_document
from app.main import app
from app.previews import _visible_document
from app.security import User, hash_password
from app.tools import search_documents

CSRF = {"x-requested-with": "agentic-rag"}
PW = "correct horse battery"
ADMIN, ANA, BO = "id-admin@acme.test", "id-ana@acme.test", "id-bo@acme.test"
ISSUER = "https://idp.test"


def _user(email: str, role: str = "member") -> int:
    with connect() as conn:
        return conn.execute("INSERT INTO users (email, password_hash, role) VALUES (%s, %s, %s) ON CONFLICT (email) "
                            "DO UPDATE SET role = EXCLUDED.role, disabled = false, password_hash = EXCLUDED.password_hash RETURNING id",
                            (email, hash_password(PW), role)).fetchone()[0]


def _login(email: str) -> TestClient:
    c = TestClient(app, headers=CSRF, follow_redirects=False)
    assert c.post("/api/auth/login", json={"email": email, "password": PW}).status_code == 200
    return c


@pytest.fixture
def people():
    ids = {ADMIN: _user(ADMIN, "admin"), ANA: _user(ANA), BO: _user(BO)}
    yield ids
    with connect() as conn:
        conn.execute("DELETE FROM groups WHERE name ILIKE 'id-%'")
        conn.execute("DELETE FROM documents WHERE name LIKE 'id-%'")
        conn.execute("DELETE FROM connections WHERE name LIKE 'id-%'")
        conn.execute("DELETE FROM app_settings WHERE key IN ('sso', 'scim')")
        conn.execute("DELETE FROM users WHERE email LIKE 'id-%' OR email LIKE 'scim-%'")
    drop_tables("title LIKE %s", ("id-%",))


def _group(name: str, *members: int) -> int:
    gid = groups.create(name)
    groups.add_members(gid, list(members))
    return gid


# ---------- groups in access lists ----------

def test_principals(people):
    _group("id-Finance Team", people[ANA])
    assert groups.principals("ID-Ana@acme.test") == [ANA, "group:id-finance team"]
    assert groups.principals(BO) == [BO] and groups.principals(None) == []


def test_group_restricted_document(people):
    _group("id-Finance", people[ANA])
    doc = upsert_document("id-budget.md", [(1, "quetzalcoatl budget plan for next year")], external_id="id-b", allowed=["group:id-finance"])
    names = lambda email: {h["document"] for h in search_documents("quetzalcoatl budget plan", k=15, email=email)}  # noqa: E731
    assert "id-budget.md" in names(ANA) and "id-budget.md" not in names(BO)
    assert _visible_document(doc["id"], User(id=people[ANA], email=ANA, role="member"))
    with pytest.raises(Exception):
        _visible_document(doc["id"], User(id=people[BO], email=BO, role="member"))
    assert set(notify.users_who_can_see(["group:id-finance"])) == {people[ANA]}
    # the library listing (list_sources) uses the same rule
    assert any(d["name"] == "id-budget.md" for d in _login(ANA).get("/api/sources").json()["documents"])
    assert not any(d["name"] == "id-budget.md" for d in _login(BO).get("/api/sources").json()["documents"])


def test_group_restricted_connection(people):
    gid = _group("id-Sales", people[BO])
    with connect() as conn:
        cid = conn.execute("INSERT INTO connections (kind, name, allowed) VALUES ('web', 'id-crm', %s) RETURNING id",
                           (["group:id-sales"],)).fetchone()[0]
    upsert_document("id-deal.md", [(1, "quetzalcoatl deal pipeline notes")], source="web", external_id="id-d", connection_id=cid)
    names = lambda email: {h["document"] for h in search_documents("quetzalcoatl deal pipeline", k=15, email=email)}  # noqa: E731
    assert "id-deal.md" in names(BO) and "id-deal.md" not in names(ANA)
    groups.remove_members(gid, [people[BO]])  # membership changes apply immediately
    assert "id-deal.md" not in names(BO)


def test_group_restricted_table_is_enforced_by_postgres_role(people):
    gid = _group("id-HR", people[ANA])
    t = ingest_spreadsheet("id-salaries.csv", b"name,salary\nAna,150000\nBo,120000\n", user_id=people[ANA],
                           allowed=["group:id-hr"])[0]["table"]
    assert datasources.run("uploads", f"SELECT sum(salary) FROM {t}", ANA)["rows"] == [[270000]]
    assert t not in datasources.list_tables("uploads", BO)
    with pytest.raises(psycopg.Error):  # bo's database role has no GRANT on the table
        datasources.run("uploads", f"SELECT * FROM data.{t}", BO)
    groups.remove_members(gid, [people[ANA]])  # grants are recomputed on the next query
    with pytest.raises(psycopg.Error):
        datasources.run("uploads", f"SELECT * FROM {t}", ANA)


def test_deleting_a_group_never_makes_content_public_or_leaks_to_a_new_group(people):
    gid = _group("id-Legal", people[ANA])
    upsert_document("id-contract.md", [(1, "quetzalcoatl contract terms")], external_id="id-c", allowed=["group:id-legal"])
    groups.delete(gid)
    with connect() as conn:
        assert conn.execute("SELECT allowed FROM documents WHERE name = 'id-contract.md'").fetchone()[0] == []
    _group("id-legal", people[BO])  # same name, different people
    assert not search_documents("quetzalcoatl contract terms", k=15, email=BO)
    assert not any(h["document"] == "id-contract.md" for h in search_documents("quetzalcoatl contract terms", k=15, email=None))


def test_clean_allowed_accepts_existing_groups_only(people):
    _group("id-Ops")
    assert connectors.clean_allowed(["Group:ID-Ops", "Ana@acme.test"]) == ["ana@acme.test", "group:id-ops"]
    with pytest.raises(ValueError, match="No such group"):
        connectors.clean_allowed(["group:id-nope"])


def test_groups_api_and_audit(people):
    admin, member = _login(ADMIN), _login(ANA)
    assert member.get("/api/groups").status_code == 403
    gid = admin.post("/api/groups", json={"name": "id-Design"}).json()["id"]
    assert admin.post("/api/groups", json={"name": "ID-design"}).status_code == 409
    assert admin.post(f"/api/groups/{gid}/members", json={"email": ANA}).status_code == 200
    assert admin.post(f"/api/groups/{gid}/members", json={"email": "id-ghost@acme.test"}).status_code == 404
    g = next(g for g in admin.get("/api/groups").json() if g["id"] == gid)
    assert g["principal"] == "group:id-design" and [m["email"] for m in g["members"]] == [ANA]
    assert any(n["kind"] == "group" for n in member.get("/api/notifications").json()["items"])
    # a connection can be restricted to the group
    r = admin.put("/api/connections/999999/access", json={"allowed": ["group:id-design"]})
    assert r.status_code == 200 and r.json()["allowed"] == ["group:id-design"]
    assert admin.put("/api/connections/999999/access", json={"allowed": ["group:id-missing"]}).status_code == 422
    assert admin.delete(f"/api/groups/{gid}/members/{people[ANA]}").status_code == 200
    assert admin.delete(f"/api/groups/{gid}").status_code == 200
    log = admin.get("/api/audit", params={"person": ADMIN, "size": 200}).json()
    actions = {i["action"] for i in log["items"]}
    assert {"group.create", "group.member_add", "group.member_remove", "group.delete", "connection.access_change", "user.login"} <= actions
    assert all(i["ip"] == "testclient" for i in log["items"])


# ---------- audit log ----------

def test_audit_log_filters_export_and_admin_only(people):
    admin = _login(ADMIN)
    bad = TestClient(app, headers=CSRF)
    assert bad.post("/api/auth/login", json={"email": ANA, "password": "wrong-password"}).status_code == 401
    _login(ANA).post("/api/auth/logout")
    assert _login(ANA).get("/api/audit").status_code == 403
    fails = admin.get("/api/audit", params={"action": "user.login_failed"}).json()
    assert fails["items"] and all(i["action"] == "user.login_failed" for i in fails["items"]) and "user.login" in fails["actions"]
    assert any(i["action"] == "user.logout" for i in admin.get("/api/audit", params={"person": "id-ana"}).json()["items"])
    assert admin.get("/api/audit", params={"start": "2001-01-01", "end": "2001-01-02"}).json()["total"] == 0
    page = admin.get("/api/audit", params={"size": 1, "page": 2}).json()
    assert len(page["items"]) == 1 and page["total"] >= 2
    csv = admin.get("/api/audit/export", params={"action": "user.login_failed"})
    assert csv.status_code == 200 and csv.headers["content-type"].startswith("text/csv")
    lines = csv.text.strip().splitlines()
    assert lines[0].startswith("id,time,person,action") and all("user.login_failed" in line for line in lines[1:])


def test_user_role_change_is_audited(people):
    admin = _login(ADMIN)
    assert admin.patch(f"/api/users/{people[BO]}", json={"role": "admin"}).status_code == 200
    assert admin.patch(f"/api/users/{people[ADMIN]}", json={"role": "member"}).status_code == 400
    entry = admin.get("/api/audit", params={"action": "user.role_change"}).json()["items"][0]
    assert entry["detail"] == {"email": BO, "from": "member", "to": "admin"}


# ---------- SCIM ----------

SCIM_JSON = {"content-type": "application/scim+json"}


def _scim(people) -> TestClient:
    token = _login(ADMIN).post("/api/scim/token").json()["token"]
    return TestClient(app, headers={"authorization": f"Bearer {token}", **SCIM_JSON})


def test_scim_requires_token(people):
    anon = TestClient(app)
    assert anon.get("/scim/v2/Users").status_code == 401
    assert anon.get("/scim/v2/Users", headers={"authorization": "Bearer nope"}).status_code == 401
    assert _login(ANA).post("/api/scim/token").status_code == 403
    old = _scim(people)
    assert old.get("/scim/v2/Users").status_code == 200
    _scim(people)  # rotating invalidates the old token
    assert old.get("/scim/v2/Users").status_code == 401
    with connect() as conn:
        stored = conn.execute("SELECT value::text FROM app_settings WHERE key = 'scim'").fetchone()[0]
    assert "scim_" not in stored  # only a hash is kept


def test_scim_user_lifecycle(people):
    s = _scim(people)
    r = s.post("/scim/v2/Users", json={"schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"], "userName": "scim-cy@acme.test",
                                       "name": {"givenName": "Cy", "familyName": "Lee"}, "active": True, "externalId": "okta-1"})
    assert r.status_code == 201 and r.headers["content-type"].startswith("application/scim+json")
    uid = r.json()["id"]
    assert r.json()["displayName"] == "Cy Lee" and r.json()["active"] is True
    assert s.post("/scim/v2/Users", json={"userName": "SCIM-cy@acme.test"}).status_code == 409
    found = s.get("/scim/v2/Users", params={"filter": 'userName eq "scim-cy@acme.test"'}).json()
    assert found["totalResults"] == 1 and found["Resources"][0]["id"] == uid
    assert s.get("/scim/v2/Users", params={"filter": 'userName eq "nobody@acme.test"'}).json()["totalResults"] == 0
    assert s.get(f"/scim/v2/Users/{uid}").json()["userName"] == "scim-cy@acme.test"

    # signed in, then deactivated by the IdP (Entra style: capitalized op, string boolean) → locked out immediately
    with connect() as conn:
        conn.execute("UPDATE users SET password_hash = %s WHERE id = %s", (hash_password(PW), int(uid)))
    cy = _login("scim-cy@acme.test")
    patch = {"schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"], "Operations": [{"op": "Replace", "path": "active", "value": "False"}]}
    assert s.patch(f"/scim/v2/Users/{uid}", json=patch).json()["active"] is False
    assert cy.get("/api/auth/me").status_code == 401
    assert TestClient(app, headers=CSRF).post("/api/auth/login", json={"email": "scim-cy@acme.test", "password": PW}).status_code == 403
    # Okta style: replace with a value object
    s.patch(f"/scim/v2/Users/{uid}", json={"Operations": [{"op": "replace", "value": {"active": True}}]})
    assert _login("scim-cy@acme.test").get("/api/auth/me").status_code == 200
    assert s.put(f"/scim/v2/Users/{uid}", json={"userName": "scim-cy@acme.test", "displayName": "Cy L", "active": True}).json()["displayName"] == "Cy L"

    assert s.delete(f"/scim/v2/Users/{uid}").status_code == 204
    assert s.get(f"/scim/v2/Users/{uid}").status_code == 404
    actions = {i["action"] for i in _login(ADMIN).get("/api/audit", params={"person": "scim", "size": 200}).json()["items"]}
    assert {"scim.user_create", "scim.user_deactivate", "scim.user_activate", "scim.user_update", "scim.user_delete"} <= actions


def test_scim_groups(people):
    s = _scim(people)
    r = s.post("/scim/v2/Groups", json={"displayName": "id-Engineering", "members": [{"value": str(people[ANA])}]})
    assert r.status_code == 201
    gid = r.json()["id"]
    assert groups.principals(ANA)[1:] == ["group:id-engineering"]
    assert s.get("/scim/v2/Groups", params={"filter": 'displayName eq "id-Engineering"'}).json()["totalResults"] == 1
    s.patch(f"/scim/v2/Groups/{gid}", json={"Operations": [{"op": "add", "path": "members", "value": [{"value": str(people[BO])}]}]})
    assert {m["value"] for m in s.get(f"/scim/v2/Groups/{gid}").json()["members"]} == {str(people[ANA]), str(people[BO])}
    s.patch(f"/scim/v2/Groups/{gid}", json={"Operations": [{"op": "remove", "path": f'members[value eq "{people[ANA]}"]'}]})
    assert groups.principals(ANA) == [ANA]
    # renaming keeps access lists pointing at the group
    upsert_document("id-eng.md", [(1, "x")], external_id="id-e", allowed=["group:id-engineering"])
    s.patch(f"/scim/v2/Groups/{gid}", json={"Operations": [{"op": "replace", "path": "displayName", "value": "id-Platform"}]})
    with connect() as conn:
        assert conn.execute("SELECT allowed FROM documents WHERE name = 'id-eng.md'").fetchone()[0] == ["group:id-platform"]
    assert s.delete(f"/scim/v2/Groups/{gid}").status_code == 204
    assert s.get(f"/scim/v2/Groups/{gid}").status_code == 404


# ---------- OIDC single sign-on ----------

@pytest.fixture
def idp(monkeypatch):
    """A fake OpenID provider: discovery, JWKS and a token endpoint that returns whatever ID token the test sets."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key(), as_dict=True) | {"kid": "k1", "use": "sig"}
    state = {"id_token": None, "token_requests": []}

    def handler(req: httpx.Request):
        if req.url.path == "/.well-known/openid-configuration":
            return httpx.Response(200, json={"issuer": ISSUER, "authorization_endpoint": f"{ISSUER}/authorize",
                                             "token_endpoint": f"{ISSUER}/token", "jwks_uri": f"{ISSUER}/keys",
                                             "id_token_signing_alg_values_supported": ["RS256"]})
        if req.url.path == "/keys":
            return httpx.Response(200, json={"keys": [jwk]})
        if req.url.path == "/token":
            state["token_requests"].append(req)
            return httpx.Response(200, json={"access_token": "at", "id_token": state["id_token"]})
        return httpx.Response(404)

    monkeypatch.setattr(base, "http", httpx.Client(transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(sso, "_cache", {})

    def token(nonce, signer=key, **claims):
        now = int(time.time())
        body = {"iss": ISSUER, "aud": "cid", "sub": "u1", "iat": now, "exp": now + 300, "nonce": nonce,
                "email": "id-dee@acme.test", "email_verified": True} | claims
        return jwt.encode(body, signer, algorithm="RS256", headers={"kid": "k1"})

    state["token"] = token
    return state


def _configure(admin: TestClient, **over):
    body = {"issuer": ISSUER, "client_id": "cid", "client_secret": "csecret", "domains": ["acme.test"],
            "default_role": "member", "require_sso": False} | over
    r = admin.put("/api/sso/settings", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def _sso(idp, browser: TestClient | None = None, **claims) -> tuple[TestClient, httpx.Response]:
    browser = browser or TestClient(app, headers=CSRF, follow_redirects=False)
    start = browser.get("/api/sso/start")
    assert start.status_code == 303
    loc = urlparse(start.headers["location"])
    q = {k: v[0] for k, v in parse_qs(loc.query).items()}
    assert loc.netloc == "idp.test" and q["code_challenge_method"] == "S256" and q["scope"] == "openid email profile"
    idp["id_token"] = idp["token"](claims.pop("nonce", q["nonce"]), **claims)
    return browser, browser.get("/api/sso/callback", params={"state": q["state"], "code": "abc"})


def _error(resp) -> str:
    return parse_qs(urlparse(resp.headers["location"]).query).get("error", [""])[0]


def test_sso_settings_admin_only_and_secret_write_only(people, idp):
    assert TestClient(app).get("/api/sso/status").json() == {"enabled": False}
    assert _login(ANA).put("/api/sso/settings", json={}).status_code == 403
    admin = _login(ADMIN)
    s = _configure(admin)
    assert s["enabled"] and s["has_secret"] and "csecret" not in str(s) and s["redirect_uri"].endswith("/api/sso/callback")
    assert _configure(admin, client_secret="")["has_secret"]  # blank keeps the stored secret
    assert admin.put("/api/sso/settings", json={"issuer": "http://evil.test", "client_id": "c"}).status_code == 422
    assert TestClient(app).get("/api/sso/status").json() == {"enabled": True}
    assert any(i["action"] == "sso.settings_change" for i in admin.get("/api/audit").json()["items"])


def test_sso_login_provisions_user_just_in_time(people, idp):
    _configure(_login(ADMIN))
    browser, resp = _sso(idp, name="Dee")
    assert resp.status_code == 303 and resp.headers["location"].endswith("/") and not _error(resp)
    me = browser.get("/api/auth/me").json()
    assert me["email"] == "id-dee@acme.test" and me["role"] == "member"
    sent = idp["token_requests"][-1]
    assert b"code_verifier=" in sent.content and sent.headers["authorization"].startswith("Basic ")
    _, again = _sso(idp)  # second login reuses the account
    assert not _error(again)
    actions = [i["action"] for i in _login(ADMIN).get("/api/audit", params={"person": "id-dee"}).json()["items"]]
    assert "user.sso_login" in actions and "user.create" in actions


@pytest.mark.parametrize("claims, why", [
    ({"nonce": "wrong"}, "nonce"),
    ({"aud": "someone-else"}, "audience"),
    ({"iss": "https://other.test"}, "issuer"),
    ({"exp": int(time.time()) - 3600}, "expired"),
    ({"email_verified": False}, "verified"),
    ({"email": "id-x@evil.test"}, "domain"),
])
def test_sso_rejects_bad_tokens(people, idp, claims, why):
    _configure(_login(ADMIN))
    browser, resp = _sso(idp, **claims)
    assert _error(resp), why
    assert browser.get("/api/auth/me").status_code == 401


def test_sso_rejects_forged_signature_and_replayed_state(people, idp):
    _configure(_login(ADMIN))
    forger = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    browser, resp = _sso(idp, signer=forger)
    assert "Sign-in failed" in _error(resp) and browser.get("/api/auth/me").status_code == 401
    # a state started in another browser (login CSRF) is refused
    attacker = TestClient(app, follow_redirects=False)
    q = parse_qs(urlparse(attacker.get("/api/sso/start").headers["location"]).query)
    victim = TestClient(app, follow_redirects=False)
    assert "another browser" in _error(victim.get("/api/sso/callback", params={"state": q["state"][0], "code": "c"}))


def test_sso_disabled_user_refused(people, idp):
    _configure(_login(ADMIN))
    with connect() as conn:
        conn.execute("UPDATE users SET disabled = true WHERE email = %s", (BO,))
    _, resp = _sso(idp, email=BO)
    assert "deactivated" in _error(resp)


def test_require_sso_blocks_member_passwords_but_not_admins(people, idp):
    admin = _login(ADMIN)
    _configure(admin, require_sso=True)
    r = TestClient(app, headers=CSRF).post("/api/auth/login", json={"email": ANA, "password": PW})
    assert r.status_code == 403 and "single sign-on" in r.json()["detail"]
    _login(ADMIN)  # break-glass: admins can still use their password
    admin.delete("/api/sso/settings")
    _login(ANA)
