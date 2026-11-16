"""Auth & secrets: password hashing, sessions, roles, CSRF guard, login throttling, encryption at rest."""
import base64
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone

from cryptography.fernet import Fernet, InvalidToken
from fastapi import Depends, HTTPException, Request
from pydantic import BaseModel

from .db import connect

SESSION_COOKIE = "rag_session"
SESSION_DAYS = 14
PUBLIC_URL = os.getenv("PUBLIC_URL", "http://localhost:8000").rstrip("/")
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:3000").rstrip("/")
SECURE_COOKIES = PUBLIC_URL.startswith("https://")


# ---------- encryption at rest ----------

def _fernet() -> Fernet:
    key = os.getenv("SECRETS_KEY")
    if not key:
        raise RuntimeError("SECRETS_KEY is not set. Generate one: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\"")
    return Fernet(key)


def encrypt(data: dict | str) -> bytes:
    raw = data if isinstance(data, str) else json.dumps(data)
    return _fernet().encrypt(raw.encode())


def decrypt(blob: bytes | memoryview | None, as_json: bool = True):
    if blob is None:
        return None
    try:
        raw = _fernet().decrypt(bytes(blob)).decode()
    except InvalidToken:
        raise RuntimeError("Stored secret can't be decrypted: SECRETS_KEY changed? Reconnect this source.")
    return json.loads(raw) if as_json else raw


# ---------- passwords (stdlib scrypt) ----------

def hash_password(pw: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.scrypt(pw.encode(), salt=salt, n=2**14, r=8, p=1)
    return f"scrypt${base64.b64encode(salt).decode()}${base64.b64encode(dk).decode()}"


def verify_password(pw: str, stored: str) -> bool:
    try:
        _, salt, dk = stored.split("$")
        got = hashlib.scrypt(pw.encode(), salt=base64.b64decode(salt), n=2**14, r=8, p=1)
        return hmac.compare_digest(got, base64.b64decode(dk))
    except ValueError:
        return False


def check_password_policy(pw: str) -> None:
    if len(pw) < 10:
        raise HTTPException(422, "Password must be at least 10 characters")


# ---------- sessions ----------

def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    with connect() as conn:
        conn.execute("DELETE FROM sessions WHERE expires_at < now()")
        conn.execute("INSERT INTO sessions (token_hash, user_id, expires_at) VALUES (%s, %s, %s)",
                     (_hash_token(token), user_id, datetime.now(timezone.utc) + timedelta(days=SESSION_DAYS)))
    return token


def delete_session(token: str) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM sessions WHERE token_hash = %s", (_hash_token(token),))


def set_session_cookie(response, token: str) -> None:
    response.set_cookie(SESSION_COOKIE, token, max_age=SESSION_DAYS * 86400, httponly=True,
                        samesite="lax", secure=SECURE_COOKIES, path="/")


class User(BaseModel):
    id: int
    email: str
    role: str


def current_user(request: Request) -> User:
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        with connect() as conn:
            row = conn.execute(
                "SELECT u.id, u.email, u.role FROM sessions s JOIN users u ON u.id = s.user_id "
                "WHERE s.token_hash = %s AND s.expires_at > now()", (_hash_token(token),)).fetchone()
        if row:
            return User(id=row[0], email=row[1], role=row[2])
    raise HTTPException(401, "Not signed in")


def require_admin(user: User = Depends(current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(403, "Admins only")
    return user


# ---------- CSRF ----------
# Cookies are SameSite=Lax, which still allows *same-site* cross-origin POSTs (e.g. another localhost port).
# Requiring a custom header forces a CORS preflight, which only our frontend origin passes.

UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
CSRF_EXEMPT = ("/api/oauth/", "/api/slack/events")  # OAuth: top-level GETs anyway. Slack events: verified by signing secret


async def csrf_middleware(request: Request, call_next):
    if (request.method in UNSAFE_METHODS and not request.url.path.startswith(CSRF_EXEMPT)
            and request.headers.get("x-requested-with") != "agentic-rag"):
        from fastapi.responses import JSONResponse
        return JSONResponse({"detail": "Missing CSRF header"}, status_code=403)
    return await call_next(request)


# ---------- login throttling ----------

_fails: dict[str, list[float]] = {}
_fails_lock = threading.Lock()
MAX_FAILS, WINDOW_S = 8, 15 * 60


def throttle(key: str) -> None:
    # ponytail: in-process counter; use Redis if you run several backend instances
    now = time.time()
    with _fails_lock:
        recent = [t for t in _fails.get(key, []) if now - t < WINDOW_S]
        _fails[key] = recent
        if len(recent) >= MAX_FAILS:
            raise HTTPException(429, "Too many failed sign-in attempts. Try again in 15 minutes.")


def record_failure(key: str) -> None:
    with _fails_lock:
        _fails.setdefault(key, []).append(time.time())


def clear_failures(key: str) -> None:
    with _fails_lock:
        _fails.pop(key, None)
