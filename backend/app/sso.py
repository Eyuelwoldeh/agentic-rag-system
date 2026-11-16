"""Single sign-on with any OpenID Connect provider (Okta, Microsoft Entra ID, Google Workspace...).

Authorization code flow with PKCE, a single-use state bound to the browser by a cookie, and a nonce. The ID token's
signature is checked against the provider's published keys (JWKS), plus iss/aud/exp/nonce. New people are created on
first sign-in (just-in-time) when their email domain is allowed.
"""
import base64
import hashlib
import json
import secrets
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, urlencode

import httpx
import jwt
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from . import audit
from .connectors import base
from .db import connect
from .security import (FRONTEND_URL, PUBLIC_URL, SECURE_COOKIES, User, create_session, decrypt, encrypt, hash_password,
                       require_admin, set_session_cookie)

router = APIRouter()
STATE_TTL = timedelta(minutes=10)
STATE_COOKIE = "rag_sso_state"
CACHE_TTL = 3600
ALGORITHMS = ["RS256", "RS384", "RS512", "PS256", "PS384", "PS512", "ES256", "ES384", "ES512"]  # never HS* or none
_cache: dict[str, tuple[float, dict]] = {}


def redirect_uri() -> str:
    return f"{PUBLIC_URL}/api/sso/callback"


def settings() -> dict:
    """{issuer, client_id, domains, default_role, require_sso, client_secret (decrypted) | None}; {} when not set up."""
    with connect() as conn:
        row = conn.execute("SELECT value, secret FROM app_settings WHERE key = 'sso'").fetchone()
    if not row:
        return {}
    return {**row[0], "client_secret": decrypt(row[1], as_json=False) if row[1] else None}


def enabled(s: dict | None = None) -> bool:
    s = settings() if s is None else s
    return bool(s.get("issuer") and s.get("client_id") and s.get("client_secret"))


def required() -> bool:
    """Members must use SSO (admins keep password sign-in as a break-glass)."""
    s = settings()
    return enabled(s) and bool(s.get("require_sso"))


def _get_json(url: str, refresh: bool = False) -> dict:
    hit = _cache.get(url)
    if hit and not refresh and time.time() - hit[0] < CACHE_TTL:
        return hit[1]
    data = base.request("GET", url, retries=2).json()
    _cache[url] = (time.time(), data)
    return data


def discover(issuer: str, refresh: bool = False) -> dict:
    disc = _get_json(issuer.rstrip("/") + "/.well-known/openid-configuration", refresh)
    if disc.get("issuer", "").rstrip("/") != issuer.rstrip("/"):
        raise ValueError(f"Discovery document is for issuer {disc.get('issuer')!r}, not {issuer!r}")
    for k in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
        if not disc.get(k):
            raise ValueError(f"Discovery document has no {k}")
    return disc


def _signing_key(jwks_uri: str, kid: str | None):
    for refresh in (False, True):  # an unknown kid usually means the provider rotated keys: refetch once
        keys = jwt.PyJWKSet.from_dict(_get_json(jwks_uri, refresh)).keys
        match = [k for k in keys if k.key_id == kid] if kid else keys[:1] if len(keys) == 1 else []
        if match:
            return match[0].key
    raise ValueError("The ID token was signed with an unknown key")


def verify_id_token(token: str, s: dict, disc: dict, nonce: str) -> dict:
    algs = [a for a in ALGORITHMS if a in disc.get("id_token_signing_alg_values_supported", ALGORITHMS)]
    key = _signing_key(disc["jwks_uri"], jwt.get_unverified_header(token).get("kid"))
    claims = jwt.decode(token, key, algorithms=algs, audience=s["client_id"], issuer=disc["issuer"], leeway=60,
                        options={"require": ["exp", "iat", "iss", "aud", "sub"]})
    if not secrets.compare_digest(str(claims.get("nonce", "")), nonce):
        raise ValueError("ID token nonce doesn't match this sign-in")
    if isinstance(claims["aud"], list) and len(claims["aud"]) > 1 and claims.get("azp") != s["client_id"]:
        raise ValueError("ID token was issued to a different client")
    if claims.get("email_verified") in (False, "false"):
        raise ValueError("Your identity provider hasn't verified this email address")
    if not claims.get("email"):
        raise ValueError("The identity provider didn't send an email address (add the 'email' claim)")
    return claims


def _exchange(code: str, verifier: str, s: dict, disc: dict) -> dict:
    form = {"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri(), "code_verifier": verifier}
    headers = {"Accept": "application/json"}
    if "client_secret_basic" in disc.get("token_endpoint_auth_methods_supported", ["client_secret_basic"]):
        basic = base64.b64encode(f"{quote(s['client_id'], safe='')}:{quote(s['client_secret'], safe='')}".encode()).decode()
        headers["Authorization"] = f"Basic {basic}"
    else:
        form |= {"client_id": s["client_id"], "client_secret": s["client_secret"]}
    try:
        data = base.request("POST", disc["token_endpoint"], retries=2, data=form, headers=headers).json()
    except httpx.HTTPStatusError as e:
        raise ValueError(f"Token exchange failed: {e.response.text[:200]}")
    if not data.get("id_token"):
        raise ValueError("The identity provider returned no ID token")
    return data


# ---------- sign-in flow (public: these are how people sign in) ----------

@router.get("/api/sso/status")
def status():
    return {"enabled": enabled()}


@router.get("/api/sso/start")
def start():
    s = settings()
    if not enabled(s):
        return RedirectResponse(f"{FRONTEND_URL}/login?{urlencode({'error': 'Single sign-on is not set up.'})}", 303)
    try:
        disc = discover(s["issuer"])
    except Exception as e:
        return RedirectResponse(f"{FRONTEND_URL}/login?{urlencode({'error': f'Identity provider unreachable: {str(e)[:150]}'})}", 303)
    state, verifier, nonce = secrets.token_urlsafe(32), secrets.token_urlsafe(48), secrets.token_urlsafe(24)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    with connect() as conn:
        conn.execute("DELETE FROM sso_states WHERE expires_at < now()")
        conn.execute("INSERT INTO sso_states (state, verifier, nonce, expires_at) VALUES (%s, %s, %s, %s)",
                     (state, verifier, nonce, datetime.now(timezone.utc) + STATE_TTL))
    params = {"client_id": s["client_id"], "redirect_uri": redirect_uri(), "response_type": "code", "scope": "openid email profile",
              "state": state, "nonce": nonce, "code_challenge": challenge, "code_challenge_method": "S256"}
    resp = RedirectResponse(f"{disc['authorization_endpoint']}?{urlencode(params)}", 303)
    # binds the flow to this browser, so nobody can sign a victim into the attacker's account (login CSRF)
    resp.set_cookie(STATE_COOKIE, state, max_age=int(STATE_TTL.total_seconds()), httponly=True, samesite="lax",
                    secure=SECURE_COOKIES, path="/api/sso/")
    return resp


@router.get("/api/sso/callback")
def callback(request: Request, state: str = "", code: str = "", error: str = "", error_description: str = ""):
    def fail(msg: str, email: str | None = None):
        audit.record("user.sso_login_failed", None, email and f"user:{email}", {"reason": msg[:300]})
        resp = RedirectResponse(f"{FRONTEND_URL}/login?{urlencode({'error': msg[:300]})}", 303)
        resp.delete_cookie(STATE_COOKIE, path="/api/sso/")
        return resp

    with connect() as conn:  # single-use: delete as we read it
        row = conn.execute("DELETE FROM sso_states WHERE state = %s AND expires_at > now() RETURNING verifier, nonce",
                           (state,)).fetchone()
    if not row or not secrets.compare_digest(request.cookies.get(STATE_COOKIE, ""), state):
        return fail("This sign-in link expired or was started in another browser. Try again.")
    if error or not code:
        return fail(f"Sign-in was cancelled or refused by your identity provider ({error_description or error or 'no code'}).")
    s = settings()
    if not enabled(s):
        return fail("Single sign-on is not set up.")
    try:
        disc = discover(s["issuer"])
        claims = verify_id_token(_exchange(code, row[0], s, disc)["id_token"], s, disc, row[1])
    except (ValueError, jwt.PyJWTError, httpx.HTTPError) as e:
        return fail(f"Sign-in failed: {e}")
    email = str(claims["email"]).strip().lower()
    domains = s.get("domains") or []
    if domains and email.rsplit("@", 1)[-1] not in domains:
        return fail(f"{email} isn't in a domain allowed to sign in here.", email)
    with connect() as conn:
        user = conn.execute("SELECT id, role, disabled FROM users WHERE email = %s", (email,)).fetchone()
        if not user and domains:  # just-in-time provisioning, only for explicitly allowed domains
            user = conn.execute(
                "INSERT INTO users (email, password_hash, role, display_name) VALUES (%s, %s, %s, %s) "
                "ON CONFLICT (email) DO UPDATE SET email = EXCLUDED.email RETURNING id, role, disabled",
                (email, hash_password(secrets.token_urlsafe(32)), s.get("default_role", "member"), claims.get("name"))).fetchone()
            audit.record("user.create", User(id=user[0], email=email, role=user[1]), f"user:{user[0]}", {"via": "sso", "role": user[1]})
    if not user:
        return fail(f"There's no account for {email}. Ask an administrator to add you.", email)
    if user[2]:
        return fail("Your account is deactivated. Contact an administrator.", email)
    audit.record("user.sso_login", User(id=user[0], email=email, role=user[1]), f"user:{user[0]}", {"issuer": s["issuer"]})
    resp = RedirectResponse(f"{FRONTEND_URL}/", 303)
    resp.delete_cookie(STATE_COOKIE, path="/api/sso/")
    set_session_cookie(resp, create_session(user[0]))
    return resp


# ---------- admin settings ----------

class SSOSettings(BaseModel):
    issuer: str = Field(min_length=8, max_length=500)
    client_id: str = Field(min_length=1, max_length=500)
    client_secret: str = Field("", max_length=2000)  # "" keeps the stored secret
    domains: list[str] = Field(default_factory=list, max_length=50)
    default_role: str = Field("member", pattern="^(admin|member)$")
    require_sso: bool = False


@router.get("/api/sso/settings")
def get_settings(_: User = Depends(require_admin)):
    s = settings()
    return {"issuer": s.get("issuer", ""), "client_id": s.get("client_id", ""), "domains": s.get("domains", []),
            "default_role": s.get("default_role", "member"), "require_sso": bool(s.get("require_sso")),
            "has_secret": bool(s.get("client_secret")), "enabled": enabled(s), "redirect_uri": redirect_uri()}


@router.put("/api/sso/settings")
def put_settings(body: SSOSettings, admin: User = Depends(require_admin)):
    issuer = body.issuer.strip().rstrip("/")
    if not (issuer.startswith("https://") or issuer.startswith(("http://localhost", "http://127.0.0.1"))):
        raise HTTPException(422, "The issuer URL must use https")
    domains = sorted({d.strip().lower().lstrip("@") for d in body.domains if d.strip()})
    if any("." not in d or " " in d or "@" in d for d in domains):
        raise HTTPException(422, "Enter domains like company.com")
    secret = body.client_secret.strip() or settings().get("client_secret")
    if not secret:
        raise HTTPException(422, "Client secret is required")
    try:  # verify before saving: a typo'd issuer should fail here, not on the login page
        discover(issuer, refresh=True)
    except Exception as e:
        raise HTTPException(422, f"Couldn't read the provider's OpenID configuration: {str(e)[:200]}")
    value = {"issuer": issuer, "client_id": body.client_id.strip(), "domains": domains, "default_role": body.default_role,
             "require_sso": body.require_sso}
    with connect() as conn:
        conn.execute("INSERT INTO app_settings (key, value, secret) VALUES ('sso', %s, %s) ON CONFLICT (key) DO UPDATE "
                     "SET value = EXCLUDED.value, secret = EXCLUDED.secret, updated_at = now()",
                     (json.dumps(value), encrypt(secret)))
    audit.record("sso.settings_change", admin, "sso", {**value, "secret_changed": bool(body.client_secret.strip())})
    return get_settings(admin)


@router.delete("/api/sso/settings")
def delete_settings(admin: User = Depends(require_admin)):
    with connect() as conn:
        conn.execute("DELETE FROM app_settings WHERE key = 'sso'")
    audit.record("sso.settings_change", admin, "sso", {"removed": True})
    return {"ok": True}
