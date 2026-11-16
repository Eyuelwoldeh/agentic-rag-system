"""Groups of people. Access lists (`allowed` on documents, connections, data_tables) may contain `group:<name>`
entries next to emails; every access check compares the list with principals(email) using array overlap."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from . import audit, notify
from .db import connect
from .security import User, require_admin

router = APIRouter()
PREFIX = "group:"
ACL_TABLES = ("documents", "connections", "data_tables")


def principals(email: str | None) -> list[str]:
    """Everything an access list can name for this person: their lowercased email plus `group:<name>` per group."""
    if not email:
        return []
    email = email.strip().lower()
    with connect() as conn:
        rows = conn.execute("SELECT 'group:' || lower(g.name) FROM groups g JOIN group_members m ON m.group_id = g.id "
                            "JOIN users u ON u.id = m.user_id WHERE u.email = %s", (email,)).fetchall()
    return [email, *(r[0] for r in rows)]


def clean_name(name: str) -> str:
    name = " ".join((name or "").split())
    if not name or len(name) > 100 or "," in name:
        raise ValueError("Group names need 1-100 characters and no commas")
    return name


def existing(keys: list[str]) -> set[str]:
    """Which of these lowercased group names exist."""
    with connect() as conn:
        return {r[0] for r in conn.execute("SELECT lower(name) FROM groups WHERE lower(name) = ANY(%s)", (keys,)).fetchall()}


def create(name: str, external_id: str | None = None) -> int:
    name = clean_name(name)
    with connect() as conn:
        row = conn.execute("INSERT INTO groups (name, external_id) VALUES (%s, %s) ON CONFLICT DO NOTHING RETURNING id",
                           (name, external_id)).fetchone()
    if not row:
        raise ValueError(f"A group named {name} already exists")
    return row[0]


def _repoint(conn, old: str, new: str | None) -> None:
    """Keep access lists in step with a renamed (new) or deleted (None) group, so a later group reusing the old name
    doesn't inherit its access. A list emptied this way stays restricted (empty array = nobody), never public."""
    old, new = PREFIX + old.lower(), new and PREFIX + new.lower()
    for t in ACL_TABLES:  # table names are constants above
        if new:
            conn.execute(f"UPDATE {t} SET allowed = array_replace(allowed, %s, %s) WHERE %s = ANY(allowed)", (old, new, old))
        else:
            conn.execute(f"UPDATE {t} SET allowed = array_remove(allowed, %s) WHERE %s = ANY(allowed)", (old, old))


def rename(group_id: int, name: str) -> None:
    name = clean_name(name)
    with connect() as conn, conn.transaction():
        row = conn.execute("SELECT name FROM groups WHERE id = %s FOR UPDATE", (group_id,)).fetchone()
        if not row:
            raise LookupError("Group not found")
        if row[0] != name:
            if row[0].lower() != name.lower() and existing([name.lower()]):
                raise ValueError(f"A group named {name} already exists")
            conn.execute("UPDATE groups SET name = %s WHERE id = %s", (name, group_id))
            _repoint(conn, row[0], name)


def delete(group_id: int) -> str | None:
    with connect() as conn, conn.transaction():
        row = conn.execute("DELETE FROM groups WHERE id = %s RETURNING name", (group_id,)).fetchone()
        if row:
            _repoint(conn, row[0], None)
    return row[0] if row else None


def set_members(group_id: int, user_ids: list[int]) -> None:
    with connect() as conn, conn.transaction():
        conn.execute("DELETE FROM group_members WHERE group_id = %s", (group_id,))
        add_members(group_id, user_ids, conn)


def add_members(group_id: int, user_ids: list[int], conn=None) -> list[int]:
    """Adds existing users; returns the ids that were newly added."""
    if conn is None:
        with connect() as c:
            return add_members(group_id, user_ids, c)
    rows = conn.execute("INSERT INTO group_members (group_id, user_id) SELECT %s, id FROM users WHERE id = ANY(%s) "
                        "ON CONFLICT DO NOTHING RETURNING user_id", (group_id, user_ids)).fetchall()
    return [r[0] for r in rows]


def remove_members(group_id: int, user_ids: list[int]) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM group_members WHERE group_id = %s AND user_id = ANY(%s)", (group_id, user_ids))


# ---------- admin API ----------

class NewGroup(BaseModel):
    name: str = Field(max_length=100)


class NewMember(BaseModel):
    email: str = Field(max_length=254)


def _group_name(group_id: int) -> str:
    with connect() as conn:
        row = conn.execute("SELECT name FROM groups WHERE id = %s", (group_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Group not found")
    return row[0]


@router.get("/api/groups")
def list_groups(_: User = Depends(require_admin)):
    with connect() as conn:
        rows = conn.execute(
            "SELECT g.id, g.name, g.external_id IS NOT NULL, g.created_at, "
            "coalesce(json_agg(json_build_object('id', u.id, 'email', u.email) ORDER BY u.email) FILTER (WHERE u.id IS NOT NULL), '[]') "
            "FROM groups g LEFT JOIN group_members m ON m.group_id = g.id LEFT JOIN users u ON u.id = m.user_id "
            "GROUP BY g.id ORDER BY lower(g.name)").fetchall()
    return [{"id": r[0], "name": r[1], "principal": PREFIX + r[1].lower(), "managed": r[2], "created_at": r[3], "members": r[4]}
            for r in rows]


@router.post("/api/groups")
def create_group(body: NewGroup, admin: User = Depends(require_admin)):
    try:
        gid = create(body.name)
    except ValueError as e:
        raise HTTPException(409 if "exists" in str(e) else 422, str(e))
    audit.record("group.create", admin, f"group:{gid}", {"name": clean_name(body.name)})
    return {"id": gid}


@router.delete("/api/groups/{group_id}")
def delete_group(group_id: int, admin: User = Depends(require_admin)):
    name = delete(group_id)
    if not name:
        raise HTTPException(404, "Group not found")
    audit.record("group.delete", admin, f"group:{group_id}", {"name": name})
    return {"ok": True}


@router.post("/api/groups/{group_id}/members")
def add_member(group_id: int, body: NewMember, admin: User = Depends(require_admin)):
    name = _group_name(group_id)
    with connect() as conn:
        row = conn.execute("SELECT id FROM users WHERE email = %s", (body.email.strip().lower(),)).fetchone()
    if not row:
        raise HTTPException(404, "No user with that email. Add them in Team first.")
    if add_members(group_id, [row[0]]):
        audit.record("group.member_add", admin, f"group:{group_id}", {"name": name, "email": body.email.strip().lower()})
        notify.notify([row[0]], "group", f"You were added to the {name} group", "You can now see what's shared with this group.")
    return {"ok": True}


@router.delete("/api/groups/{group_id}/members/{user_id}")
def remove_member(group_id: int, user_id: int, admin: User = Depends(require_admin)):
    name = _group_name(group_id)
    remove_members(group_id, [user_id])
    audit.record("group.member_remove", admin, f"group:{group_id}", {"name": name, "user_id": user_id})
    return {"ok": True}
