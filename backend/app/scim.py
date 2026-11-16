"""SCIM 2.0 provisioning (/scim/v2): the Users and Groups subset Okta and Microsoft Entra ID use.

Authenticated by a bearer token an admin generates in Settings (shown once, stored as a SHA-256). Deactivating a user
(active=false) blocks sign-in immediately: their sessions are deleted and current_user rejects disabled accounts.
"""
import hashlib
import json
import re
import secrets
from types import SimpleNamespace

import psycopg

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Response
from fastapi.responses import JSONResponse

from . import audit, groups
from .db import connect
from .security import PUBLIC_URL, User, hash_password, require_admin

router = APIRouter()
BASE = "/scim/v2"
USER_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:User"
GROUP_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:Group"
LIST_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:ListResponse"
ERROR_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:Error"
PATCH_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:PatchOp"
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
ACTOR = SimpleNamespace(id=None, email="scim")  # audit label for changes made by the identity provider


def _out(body: dict | None, status: int = 200) -> Response:
    if body is None:
        return Response(status_code=status)
    return JSONResponse(body, status_code=status, media_type="application/scim+json")


def _err(status: int, detail: str, scim_type: str | None = None) -> Response:
    body = {"schemas": [ERROR_SCHEMA], "status": str(status), "detail": detail}
    if scim_type:
        body["scimType"] = scim_type
    return _out(body, status)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def scim_auth(authorization: str = Header("")) -> None:
    with connect() as conn:
        row = conn.execute("SELECT value->>'token_hash' FROM app_settings WHERE key = 'scim'").fetchone()
    token = authorization[7:].strip() if authorization[:7].lower() == "bearer " else ""
    if not (row and row[0] and token and secrets.compare_digest(_hash(token), row[0])):
        raise HTTPException(401, "Invalid SCIM token", headers={"WWW-Authenticate": "Bearer"})


def _bool(v) -> bool:
    return v if isinstance(v, bool) else str(v).strip().lower() == "true"  # Entra sends "False" as a string


def _filter_value(flt: str | None, attr: str) -> str | None:
    """Supports the one filter IdPs use: `<attr> eq "value"`. Returns the value, or None for no filter."""
    if not flt:
        return None
    m = re.fullmatch(rf'\s*{attr}\s+eq\s+"((?:[^"\\]|\\.)*)"\s*', flt, re.I)
    if not m:
        raise HTTPException(400, f"Only '{attr} eq \"...\"' filters are supported")
    return m.group(1).replace('\\"', '"')


def _page(rows: list[dict], start: int, count: int) -> dict:
    start = max(1, start)
    page = rows[start - 1:start - 1 + max(0, count)]
    return {"schemas": [LIST_SCHEMA], "totalResults": len(rows), "startIndex": start, "itemsPerPage": len(page), "Resources": page}


# ---------- users ----------

USER_COLS = "id, email, display_name, external_id, disabled, created_at"


def _user(row) -> dict:
    uid, email, name, ext, disabled, created = row
    return {"schemas": [USER_SCHEMA], "id": str(uid), "userName": email, "externalId": ext, "active": not disabled,
            "displayName": name or email, "emails": [{"value": email, "primary": True, "type": "work"}],
            "meta": {"resourceType": "User", "created": created.isoformat(), "location": f"{PUBLIC_URL}{BASE}/Users/{uid}"}}


def _get_user(uid: str):
    with connect() as conn:
        row = conn.execute(f"SELECT {USER_COLS} FROM users WHERE id = %s", (int(uid),)).fetchone() if uid.isdigit() else None
    if not row:
        raise HTTPException(404, "User not found")
    return row


def _set_user(uid: int, changes: dict) -> None:
    """Apply email/display_name/external_id/disabled changes; deactivation also ends every session."""
    allowed = {k: v for k, v in changes.items() if k in ("email", "display_name", "external_id", "disabled")}
    if not allowed:
        return
    with connect() as conn:
        before = conn.execute("SELECT email, disabled FROM users WHERE id = %s", (uid,)).fetchone()
        cols = ", ".join(f"{k} = %s" for k in allowed)  # keys come from the fixed list above
        try:
            conn.execute(f"UPDATE users SET {cols} WHERE id = %s", (*allowed.values(), uid))
        except psycopg.errors.UniqueViolation:
            raise HTTPException(409, "Another user already has that userName")
        if allowed.get("disabled"):
            conn.execute("DELETE FROM sessions WHERE user_id = %s", (uid,))
    if "disabled" in allowed and allowed["disabled"] != before[1]:
        audit.record("scim.user_deactivate" if allowed["disabled"] else "scim.user_activate", ACTOR, f"user:{uid}", {"email": before[0]})
    rest = {k: v for k, v in allowed.items() if k != "disabled"}
    if rest:
        audit.record("scim.user_update", ACTOR, f"user:{uid}", rest)


def _user_fields(body: dict) -> dict:
    out = {}
    if "userName" in body:
        email = str(body["userName"]).strip().lower()
        if not EMAIL_RE.match(email):
            raise HTTPException(400, "userName must be an email address")
        out["email"] = email
    if "active" in body:
        out["disabled"] = not _bool(body["active"])
    if "externalId" in body:
        out["external_id"] = body["externalId"]
    name = body.get("displayName") or " ".join(filter(None, [(body.get("name") or {}).get("givenName"),
                                                             (body.get("name") or {}).get("familyName")]))
    if name:
        out["display_name"] = str(name)[:200]
    return out


@router.get(f"{BASE}/Users", dependencies=[Depends(scim_auth)])
def list_users(filter: str | None = None, startIndex: int = 1, count: int = 100):
    email = _filter_value(filter, "userName")
    with connect() as conn:
        rows = conn.execute(f"SELECT {USER_COLS} FROM users WHERE %(e)s::text IS NULL OR email = lower(%(e)s) ORDER BY id",
                            {"e": email}).fetchall()
    return _out(_page([_user(r) for r in rows], startIndex, count))


@router.get(f"{BASE}/Users/{{uid}}", dependencies=[Depends(scim_auth)])
def get_user(uid: str):
    return _out(_user(_get_user(uid)))


@router.post(f"{BASE}/Users", dependencies=[Depends(scim_auth)])
def create_user(body: dict = Body(...)):
    f = _user_fields(body)
    if "email" not in f:
        return _err(400, "userName is required", "invalidValue")
    from .sso import settings as sso_settings
    role = sso_settings().get("default_role", "member")
    with connect() as conn:
        row = conn.execute(
            "INSERT INTO users (email, password_hash, role, display_name, external_id, disabled) VALUES (%s, %s, %s, %s, %s, %s) "
            f"ON CONFLICT (email) DO NOTHING RETURNING {USER_COLS}",
            (f["email"], hash_password(secrets.token_urlsafe(32)), role, f.get("display_name"), f.get("external_id"),
             f.get("disabled", False))).fetchone()
    if not row:
        return _err(409, "A user with that userName already exists", "uniqueness")
    audit.record("scim.user_create", ACTOR, f"user:{row[0]}", {"email": f["email"], "role": role})
    return _out(_user(row), 201)


@router.put(f"{BASE}/Users/{{uid}}", dependencies=[Depends(scim_auth)])
def replace_user(uid: str, body: dict = Body(...)):
    row = _get_user(uid)
    _set_user(row[0], _user_fields(body))
    return _out(_user(_get_user(uid)))


@router.patch(f"{BASE}/Users/{{uid}}", dependencies=[Depends(scim_auth)])
def patch_user(uid: str, body: dict = Body(...)):
    row = _get_user(uid)
    changes: dict = {}
    for op in body.get("Operations", []):
        kind, path, value = str(op.get("op", "")).lower(), op.get("path"), op.get("value")
        if kind not in ("replace", "add"):
            continue  # removing attributes we don't store is a no-op
        if path:
            path = {"name.givenName": "displayName", "name.formatted": "displayName"}.get(path, path)
            value = {path: value} if path in ("active", "userName", "displayName", "externalId") else {}
        changes |= _user_fields(value if isinstance(value, dict) else {})
    _set_user(row[0], changes)
    return _out(_user(_get_user(uid)))


@router.delete(f"{BASE}/Users/{{uid}}", dependencies=[Depends(scim_auth)])
def delete_user(uid: str):
    row = _get_user(uid)
    with connect() as conn:
        conn.execute("DELETE FROM users WHERE id = %s", (row[0],))  # sessions and memberships cascade
    audit.record("scim.user_delete", ACTOR, f"user:{row[0]}", {"email": row[1]})
    return _out(None, 204)


# ---------- groups ----------

def _group(gid: int) -> dict:
    with connect() as conn:
        row = conn.execute("SELECT id, name, external_id, created_at FROM groups WHERE id = %s", (gid,)).fetchone()
        if not row:
            raise HTTPException(404, "Group not found")
        members = conn.execute("SELECT u.id, u.email FROM group_members m JOIN users u ON u.id = m.user_id "
                               "WHERE m.group_id = %s ORDER BY u.id", (gid,)).fetchall()
    return {"schemas": [GROUP_SCHEMA], "id": str(row[0]), "displayName": row[1], "externalId": row[2],
            "members": [{"value": str(m[0]), "display": m[1]} for m in members],
            "meta": {"resourceType": "Group", "created": row[3].isoformat(), "location": f"{PUBLIC_URL}{BASE}/Groups/{row[0]}"}}


def _gid(gid: str) -> int:
    with connect() as conn:
        row = conn.execute("SELECT id FROM groups WHERE id = %s", (int(gid),)).fetchone() if gid.isdigit() else None
    if not row:
        raise HTTPException(404, "Group not found")
    return row[0]


def _member_ids(value) -> list[int]:
    items = value if isinstance(value, list) else [value] if value else []
    return [int(m["value"]) for m in items if isinstance(m, dict) and str(m.get("value", "")).isdigit()]


@router.get(f"{BASE}/Groups", dependencies=[Depends(scim_auth)])
def list_groups(filter: str | None = None, startIndex: int = 1, count: int = 100):
    name = _filter_value(filter, "displayName")
    with connect() as conn:
        ids = [r[0] for r in conn.execute("SELECT id FROM groups WHERE %(n)s::text IS NULL OR lower(name) = lower(%(n)s) ORDER BY id",
                                          {"n": name}).fetchall()]
    return _out(_page([_group(i) for i in ids], startIndex, count))


@router.get(f"{BASE}/Groups/{{gid}}", dependencies=[Depends(scim_auth)])
def get_group(gid: str):
    return _out(_group(_gid(gid)))


@router.post(f"{BASE}/Groups", dependencies=[Depends(scim_auth)])
def create_group(body: dict = Body(...)):
    try:
        gid = groups.create(str(body.get("displayName", "")), body.get("externalId"))
    except ValueError as e:
        return _err(409 if "exists" in str(e) else 400, str(e), "uniqueness" if "exists" in str(e) else "invalidValue")
    groups.add_members(gid, _member_ids(body.get("members")))
    audit.record("scim.group_create", ACTOR, f"group:{gid}", {"name": body.get("displayName"), "members": _member_ids(body.get("members"))})
    return _out(_group(gid), 201)


@router.put(f"{BASE}/Groups/{{gid}}", dependencies=[Depends(scim_auth)])
def replace_group(gid: str, body: dict = Body(...)):
    g = _gid(gid)
    _apply_group(g, [{"op": "replace", "value": {k: body[k] for k in ("displayName", "members") if k in body}}])
    return _out(_group(g))


@router.patch(f"{BASE}/Groups/{{gid}}", dependencies=[Depends(scim_auth)])
def patch_group(gid: str, body: dict = Body(...)):
    g = _gid(gid)
    _apply_group(g, body.get("Operations", []))
    return _out(_group(g))


def _apply_group(gid: int, ops: list[dict]) -> None:
    for op in ops:
        kind, path, value = str(op.get("op", "")).lower(), op.get("path") or "", op.get("value")
        m = re.fullmatch(r'members\[value eq "(\d+)"\]', path)
        try:
            if m and kind == "remove":  # Entra/Okta: remove one member by filter
                groups.remove_members(gid, [int(m.group(1))])
                audit.record("scim.group_update", ACTOR, f"group:{gid}", {"removed": [int(m.group(1))]})
            elif path == "members" and kind == "add":
                groups.add_members(gid, _member_ids(value))
                audit.record("scim.group_update", ACTOR, f"group:{gid}", {"added": _member_ids(value)})
            elif path == "members" and kind == "remove":
                groups.remove_members(gid, _member_ids(value))
                audit.record("scim.group_update", ACTOR, f"group:{gid}", {"removed": _member_ids(value)})
            elif path == "members" and kind == "replace":
                groups.set_members(gid, _member_ids(value))
                audit.record("scim.group_update", ACTOR, f"group:{gid}", {"members": _member_ids(value)})
            elif path == "displayName" and kind in ("replace", "add"):
                groups.rename(gid, str(value))
                audit.record("scim.group_update", ACTOR, f"group:{gid}", {"name": value})
            elif not path and kind in ("replace", "add") and isinstance(value, dict):
                if "displayName" in value:
                    groups.rename(gid, str(value["displayName"]))
                    audit.record("scim.group_update", ACTOR, f"group:{gid}", {"name": value["displayName"]})
                if "members" in value:
                    (groups.set_members if kind == "replace" else groups.add_members)(gid, _member_ids(value["members"]))
                    audit.record("scim.group_update", ACTOR, f"group:{gid}", {"members": _member_ids(value["members"])})
        except ValueError as e:
            raise HTTPException(409 if "exists" in str(e) else 400, str(e))


@router.delete(f"{BASE}/Groups/{{gid}}", dependencies=[Depends(scim_auth)])
def delete_group(gid: str):
    name = groups.delete(_gid(gid))
    audit.record("scim.group_delete", ACTOR, f"group:{gid}", {"name": name})
    return _out(None, 204)


# ---------- admin: the bearer token ----------

@router.get("/api/scim/token")
def token_status(_: User = Depends(require_admin)):
    with connect() as conn:
        row = conn.execute("SELECT updated_at FROM app_settings WHERE key = 'scim'").fetchone()
    return {"configured": bool(row), "created_at": row[0] if row else None, "base_url": f"{PUBLIC_URL}{BASE}"}


@router.post("/api/scim/token")
def new_token(admin: User = Depends(require_admin)):
    """Generates (or rotates) the token. Returned once; only its hash is kept."""
    token = "scim_" + secrets.token_urlsafe(32)
    with connect() as conn:
        conn.execute("INSERT INTO app_settings (key, value) VALUES ('scim', %s) ON CONFLICT (key) DO UPDATE "
                     "SET value = EXCLUDED.value, updated_at = now()", (json.dumps({"token_hash": _hash(token)}),))
    audit.record("scim.token_create", admin, "scim")
    return {"token": token, "base_url": f"{PUBLIC_URL}{BASE}"}


@router.delete("/api/scim/token")
def revoke_token(admin: User = Depends(require_admin)):
    with connect() as conn:
        conn.execute("DELETE FROM app_settings WHERE key = 'scim'")
    audit.record("scim.token_revoke", admin, "scim")
    return {"ok": True}
