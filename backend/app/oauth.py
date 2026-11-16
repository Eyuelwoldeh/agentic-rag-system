"""OAuth 2.0 (authorization code + PKCE + state) for connectors, and encrypted credential storage."""
import base64
import hashlib
import json
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from . import audit, connectors
from .connectors.base import request as http_request
from .db import connect
from .security import FRONTEND_URL, PUBLIC_URL, User, current_user, decrypt, encrypt, require_admin

router = APIRouter()
STATE_TTL = timedelta(minutes=10)


def redirect_uri(provider: str) -> str:
    return f"{PUBLIC_URL}/api/oauth/{provider}/callback"


def _oauth_module(provider: str):
    m = connectors.REGISTRY.get(provider)
    if not m or not getattr(m, "OAUTH", None):
        raise HTTPException(404, f"{provider} doesn't support OAuth")
    return m


def _app(provider: str) -> tuple[str, str] | None:
    with connect() as conn:
        row = conn.execute("SELECT client_id, client_secret FROM oauth_apps WHERE provider = %s", (provider,)).fetchone()
    return (row[0], decrypt(row[1], as_json=False)) if row else None


def configured_providers() -> set[str]:
    with connect() as conn:
        return {r[0] for r in conn.execute("SELECT provider FROM oauth_apps").fetchall()}


# ---------- admin: OAuth app credentials ----------

class AppCreds(BaseModel):
    client_id: str = Field(min_length=1, max_length=500)
    client_secret: str = Field(min_length=1, max_length=2000)


@router.get("/api/oauth/apps")
def list_apps(_: User = Depends(require_admin)):
    with connect() as conn:
        rows = {r[0]: (r[1], r[2]) for r in conn.execute("SELECT provider, client_id, updated_at FROM oauth_apps").fetchall()}
    out = []
    for kind, m in connectors.REGISTRY.items():
        if getattr(m, "OAUTH", None):
            cid, updated = rows.get(kind, (None, None))
            out.append({"provider": kind, "label": m.LABEL, "configured": kind in rows, "client_id": cid, "updated_at": updated,
                        "redirect_uri": redirect_uri(kind), "scopes": m.OAUTH["scopes"], "console": m.OAUTH.get("console"),
                        "note": m.OAUTH.get("note")})
    return out  # secrets are write-only: never returned


@router.put("/api/oauth/apps/{provider}")
def save_app(provider: str, body: AppCreds, admin: User = Depends(require_admin)):
    _oauth_module(provider)
    with connect() as conn:
        conn.execute(
            "INSERT INTO oauth_apps (provider, client_id, client_secret) VALUES (%s, %s, %s) "
            "ON CONFLICT (provider) DO UPDATE SET client_id = EXCLUDED.client_id, client_secret = EXCLUDED.client_secret, updated_at = now()",
            (provider, body.client_id.strip(), encrypt(body.client_secret.strip())))
    audit.record("oauth_app.save", admin, f"oauth_app:{provider}", {"client_id": body.client_id.strip()})
    return {"ok": True}


@router.delete("/api/oauth/apps/{provider}")
def delete_app(provider: str, admin: User = Depends(require_admin)):
    with connect() as conn:
        conn.execute("DELETE FROM oauth_apps WHERE provider = %s", (provider,))
    audit.record("oauth_app.delete", admin, f"oauth_app:{provider}")
    return {"ok": True}


# ---------- the OAuth dance ----------

@router.get("/api/oauth/{provider}/start")
def start(provider: str, name: str = "", config: str = "{}", allowed: str = "", user: User = Depends(require_admin)):
    """Top-level navigation from the Connectors page → provider consent screen."""
    m = _oauth_module(provider)
    app = _app(provider)
    if not app:
        raise HTTPException(400, f"{m.LABEL} OAuth isn't set up yet (Settings → OAuth apps)")
    try:
        cfg = connectors.clean_config(provider, json.loads(config))
        acl = connectors.clean_allowed(allowed.split(","))
    except (ValueError, json.JSONDecodeError) as e:
        return RedirectResponse(f"{FRONTEND_URL}/connectors?{urlencode({'error': str(e)})}", 303)
    state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    with connect() as conn:
        conn.execute("DELETE FROM oauth_states WHERE expires_at < now()")
        conn.execute("INSERT INTO oauth_states (state, user_id, provider, verifier, name, config, allowed, expires_at) "
                     "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                     (state, user.id, provider, verifier, name or m.LABEL, json.dumps(cfg), acl,
                      datetime.now(timezone.utc) + STATE_TTL))
    spec = m.OAUTH
    params = {"client_id": app[0], "redirect_uri": redirect_uri(provider), "response_type": "code", "state": state,
              "code_challenge": challenge, "code_challenge_method": "S256", **spec.get("extra", {})}
    if spec["scopes"]:
        params["scope"] = spec.get("scope_sep", " ").join(spec["scopes"])
    return RedirectResponse(f"{spec['authorize_url']}?{urlencode(params)}", 303)


def _exchange(provider: str, form: dict) -> dict:
    """POST to the provider's token endpoint (code exchange or refresh). Returns the normalized creds dict."""
    m = _oauth_module(provider)
    client_id, client_secret = _app(provider) or (None, None)
    if not client_id:
        raise RuntimeError(f"{m.LABEL} OAuth app was removed in Settings")
    spec = m.OAUTH
    headers = {"Accept": "application/json"}
    basic = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    if spec.get("token_auth") == "basic_json":  # Notion: HTTP Basic + JSON body
        resp = http_request("POST", spec["token_url"], json=form, headers={**headers, "Authorization": f"Basic {basic}"})
    elif spec.get("token_auth") == "basic":  # QuickBooks, Xero: HTTP Basic + form body
        resp = http_request("POST", spec["token_url"], data=form, headers={**headers, "Authorization": f"Basic {basic}"})
    else:
        resp = http_request("POST", spec["token_url"], data={**form, "client_id": client_id, "client_secret": client_secret},
                            headers=headers)
    data = resp.json()
    if data.get("error") or data.get("ok") is False or "access_token" not in data:
        raise RuntimeError(f"{m.LABEL} token exchange failed: {data.get('error_description') or data.get('error') or 'no access_token'}")
    creds = {"access_token": data["access_token"]}
    if data.get("refresh_token"):
        creds["refresh_token"] = data["refresh_token"]
    for k in spec.get("keep", []):  # e.g. Salesforce's instance_url
        if data.get(k):
            creds[k] = data[k]
    if expires_in := data.get("expires_in") or spec.get("expires_in"):  # Salesforce omits it: assume the session timeout
        creds["expires_at"] = (datetime.now(timezone.utc) + timedelta(seconds=int(expires_in))).isoformat()
    return creds


@router.get("/api/oauth/{provider}/callback")
def callback(provider: str, request: Request, state: str = "", code: str = "", error: str = ""):
    def back(**q):
        return RedirectResponse(f"{FRONTEND_URL}/connectors?{urlencode(q)}", 303)

    with connect() as conn:  # single-use state: delete as we read it
        row = conn.execute("DELETE FROM oauth_states WHERE state = %s AND expires_at > now() "
                           "RETURNING user_id, provider, verifier, name, config, allowed", (state,)).fetchone()
    if not row or row[1] != provider:
        return back(error="This sign-in link expired or was already used. Try connecting again.")
    try:
        user = current_user(request)  # the browser finishing the flow must be the one that started it
    except HTTPException:
        return back(error="Sign in to the app first, then connect again.")
    if user.id != row[0]:
        return back(error="Signed-in user doesn't match the user who started this connection.")
    if error or not code:
        return back(error=f"{provider} authorization was cancelled ({error or 'no code'}).")
    try:
        creds = _exchange(provider, {"grant_type": "authorization_code", "code": code,
                                     "redirect_uri": redirect_uri(provider), "code_verifier": row[2]})
        for param, key in _oauth_module(provider).OAUTH.get("callback_params", {}).items():  # QuickBooks sends realmId here
            if request.query_params.get(param):
                creds[key] = request.query_params[param]
        conn_id = connectors.create(provider, row[3], row[4], creds=creds, user_id=user.id, allowed=row[5])
    except Exception as e:
        return back(error=str(e)[:300])
    audit.record("connection.create", user, f"connection:{conn_id}", {"kind": provider, "name": row[3], "allowed": row[5], "via": "oauth"})
    connectors.sync_in_background(conn_id)
    return back(connected=str(conn_id))


def load_credentials(conn_id: int) -> dict | None:
    """Decrypt a connection's credentials, refreshing (and re-saving) expired OAuth access tokens."""
    with connect() as conn:
        kind, blob = conn.execute("SELECT kind, credentials FROM connections WHERE id = %s", (conn_id,)).fetchone()
    creds = decrypt(blob)
    if not creds or "expires_at" not in creds:
        return creds
    if datetime.fromisoformat(creds["expires_at"]) - timedelta(minutes=2) > datetime.now(timezone.utc):
        return creds
    if "refresh_token" not in creds:
        raise RuntimeError("Access expired and no refresh token was issued. Reconnect this source.")
    fresh = _exchange(kind, {"grant_type": "refresh_token", "refresh_token": creds["refresh_token"]})
    fresh = {**creds, **fresh}  # keeps refresh_token (Google doesn't resend it), realm_id, instance_url...
    with connect() as conn:
        conn.execute("UPDATE connections SET credentials = %s WHERE id = %s", (encrypt(fresh), conn_id))
    return fresh
