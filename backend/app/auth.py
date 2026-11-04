"""Accounts: first-run admin setup, sign in/out, password change, admin user management."""
import re
import secrets

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from .db import connect
from .security import (SESSION_COOKIE, User, check_password_policy, clear_failures, create_session, current_user,
                       delete_session, hash_password, record_failure, require_admin, set_session_cookie, throttle,
                       verify_password)

router = APIRouter()
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_DUMMY_HASH = hash_password(secrets.token_hex(8))  # equalizes timing for unknown emails


class Credentials(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(max_length=200)


def _email(raw: str) -> str:
    email = raw.strip().lower()
    if not EMAIL_RE.match(email):
        raise HTTPException(422, "Enter a valid email address")
    return email


@router.get("/api/auth/status")
def status():
    with connect() as conn:
        return {"needs_setup": conn.execute("SELECT NOT EXISTS (SELECT 1 FROM users)").fetchone()[0]}


@router.post("/api/auth/setup")
def setup(body: Credentials, response: Response):
    """Create the first (admin) account. Only works while there are no users at all."""
    email = _email(body.email)
    check_password_policy(body.password)
    with connect() as conn, conn.transaction():
        conn.execute("LOCK TABLE users IN EXCLUSIVE MODE")  # two simultaneous setups can't both win
        row = conn.execute(
            "INSERT INTO users (email, password_hash, role) SELECT %s, %s, 'admin' "
            "WHERE NOT EXISTS (SELECT 1 FROM users) RETURNING id",
            (email, hash_password(body.password))).fetchone()
    if not row:
        raise HTTPException(409, "Setup is already done. Sign in instead.")
    set_session_cookie(response, create_session(row[0]))
    return {"id": row[0], "email": email, "role": "admin"}


@router.post("/api/auth/login")
def login(body: Credentials, request: Request, response: Response):
    email = body.email.strip().lower()
    key = f"{email}|{request.client.host if request.client else ''}"
    throttle(key)
    with connect() as conn:
        row = conn.execute("SELECT id, password_hash, role FROM users WHERE email = %s", (email,)).fetchone()
    if not verify_password(body.password, row[1] if row else _DUMMY_HASH) or not row:
        record_failure(key)
        raise HTTPException(401, "Wrong email or password")
    clear_failures(key)
    set_session_cookie(response, create_session(row[0]))
    return {"id": row[0], "email": email, "role": row[2]}


@router.post("/api/auth/logout")
def logout(request: Request, response: Response):
    if token := request.cookies.get(SESSION_COOKIE):
        delete_session(token)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}


@router.get("/api/auth/me")
def me(user: User = Depends(current_user)):
    return user


class PasswordChange(BaseModel):
    current: str = Field(max_length=200)
    new: str = Field(max_length=200)


@router.post("/api/auth/password")
def change_password(body: PasswordChange, request: Request, response: Response, user: User = Depends(current_user)):
    with connect() as conn:
        stored = conn.execute("SELECT password_hash FROM users WHERE id = %s", (user.id,)).fetchone()[0]
    if not verify_password(body.current, stored):
        raise HTTPException(401, "Current password is wrong")
    check_password_policy(body.new)
    with connect() as conn:
        conn.execute("UPDATE users SET password_hash = %s WHERE id = %s", (hash_password(body.new), user.id))
        conn.execute("DELETE FROM sessions WHERE user_id = %s", (user.id,))  # sign out everywhere else
    set_session_cookie(response, create_session(user.id))
    return {"ok": True}


# ---------- admin: users ----------

class NewUser(BaseModel):
    email: str = Field(max_length=254)
    role: str = Field(pattern="^(admin|member)$")


@router.get("/api/users")
def list_users(_: User = Depends(require_admin)):
    with connect() as conn:
        rows = conn.execute("SELECT id, email, role, created_at FROM users ORDER BY id").fetchall()
    return [{"id": r[0], "email": r[1], "role": r[2], "created_at": r[3]} for r in rows]


@router.post("/api/users")
def create_user(body: NewUser, _: User = Depends(require_admin)):
    """Creates the account with a one-time temporary password, returned once for the admin to share."""
    email = _email(body.email)
    temp = secrets.token_urlsafe(12)
    with connect() as conn:
        row = conn.execute("INSERT INTO users (email, password_hash, role) VALUES (%s, %s, %s) "
                           "ON CONFLICT (email) DO NOTHING RETURNING id", (email, hash_password(temp), body.role)).fetchone()
    if not row:
        raise HTTPException(409, "A user with that email already exists")
    return {"id": row[0], "email": email, "role": body.role, "temporary_password": temp}


@router.delete("/api/users/{user_id}")
def delete_user(user_id: int, admin: User = Depends(require_admin)):
    if user_id == admin.id:
        raise HTTPException(400, "You can't delete your own account")
    with connect() as conn:
        conn.execute("DELETE FROM users WHERE id = %s", (user_id,))  # sessions cascade
    return {"ok": True}
