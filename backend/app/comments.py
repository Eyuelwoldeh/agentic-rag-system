"""Comments on answers (runs) and dashboard tiles, with @mentions. Only people who can see the target can read or post."""
import re
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from . import notify
from .db import connect
from .security import User, current_user

router = APIRouter()
Target = Literal["run", "tile"]
MENTION = re.compile(r"(?<![\w.])@([\w.+-]+(?:@[\w-]+(?:\.[\w-]+)+)?)")


def audience(target_type: str, target_id: int) -> tuple[list[int], str] | None:
    """(user ids who can see the target, in-app link), or None if it doesn't exist.
    Run: whoever asked it plus anyone its conversation is shared with. Tile: its owner, or everyone if shared."""
    with connect() as conn:
        if target_type == "run":
            row = conn.execute("SELECT user_id, conversation_id FROM runs WHERE id = %s", (target_id,)).fetchone()
            if not row:
                return None
            owner, conv = row
            shares = [r[0] for r in conn.execute("SELECT user_id FROM conversation_shares WHERE conversation_id = %s", (conv,)).fetchall()] if conv else []
            ids = notify.users_who_can_see(None) if None in shares else [owner, *shares]
            return [i for i in ids if i], f"/?c={conv}" if conv else "/runs"
        row = conn.execute("SELECT owner_id, shared FROM dashboard_tiles WHERE id = %s", (target_id,)).fetchone()
        if not row:
            return None
        return (notify.users_who_can_see(None) if row[1] else [row[0]]), "/dashboard"


def _visible(target_type: str, target_id: int, user: User) -> tuple[list[int], str]:
    a = audience(target_type, target_id)
    if not a or user.id not in a[0]:
        raise HTTPException(404, "Not found")
    return a


def mentioned(body: str) -> set[int]:
    """User ids named as @email or @local-part (the part before the @)."""
    names = {m.rstrip(".").lower() for m in MENTION.findall(body)}
    if not names:
        return set()
    with connect() as conn:
        rows = conn.execute("SELECT id, lower(email) FROM users").fetchall()
    return {i for i, email in rows if email in names or email.split("@")[0] in names}


def _notify_mentions(ids: set[int], audience_ids: list[int], author: User, body: str, link: str) -> None:
    targets = [i for i in ids if i in audience_ids and i != author.id]  # never tell someone about something they can't open
    notify.notify(targets, "mention", f"{author.email} mentioned you in a comment", body[:300], link)


class NewComment(BaseModel):
    target_type: Target
    target_id: int
    body: str = Field(min_length=1, max_length=5000)


class EditComment(BaseModel):
    body: str = Field(min_length=1, max_length=5000)


@router.get("/api/comments")
def list_comments(target_type: Target, target_id: int, user: User = Depends(current_user)):
    _visible(target_type, target_id, user)
    with connect() as conn:
        rows = conn.execute("SELECT c.id, u.email, c.author_id = %s, c.body, c.created_at, c.edited_at FROM comments c "
                            "LEFT JOIN users u ON u.id = c.author_id WHERE c.target_type = %s AND c.target_id = %s AND NOT c.deleted "
                            "ORDER BY c.id", (user.id, target_type, target_id)).fetchall()
    return [{"id": r[0], "author": r[1] or "Former user", "mine": r[2], "body": r[3], "created_at": r[4], "edited_at": r[5]} for r in rows]


@router.post("/api/comments")
def add_comment(c: NewComment, user: User = Depends(current_user)):
    ids, link = _visible(c.target_type, c.target_id, user)
    body = c.body.strip()
    if not body:
        raise HTTPException(422, "Comment is empty")
    with connect() as conn:
        cid = conn.execute("INSERT INTO comments (target_type, target_id, author_id, body) VALUES (%s, %s, %s, %s) RETURNING id",
                           (c.target_type, c.target_id, user.id, body)).fetchone()[0]
    _notify_mentions(mentioned(body), ids, user, body, link)
    return {"id": cid}


def _own(comment_id: int, user: User) -> tuple[str, int, str]:
    with connect() as conn:
        row = conn.execute("SELECT target_type, target_id, body FROM comments WHERE id = %s AND author_id = %s AND NOT deleted",
                           (comment_id, user.id)).fetchone()
    if not row:
        raise HTTPException(404, "Comment not found")
    return row


@router.patch("/api/comments/{comment_id}")
def edit_comment(comment_id: int, c: EditComment, user: User = Depends(current_user)):
    target_type, target_id, old = _own(comment_id, user)
    ids, link = _visible(target_type, target_id, user)
    body = c.body.strip()
    if not body:
        raise HTTPException(422, "Comment is empty")
    with connect() as conn:
        conn.execute("UPDATE comments SET body = %s, edited_at = now() WHERE id = %s", (body, comment_id))
    _notify_mentions(mentioned(body) - mentioned(old), ids, user, body, link)  # only people newly mentioned
    return {"ok": True}


@router.delete("/api/comments/{comment_id}")
def delete_comment(comment_id: int, user: User = Depends(current_user)):
    _own(comment_id, user)
    with connect() as conn:
        conn.execute("UPDATE comments SET deleted = true WHERE id = %s", (comment_id,))
    return {"ok": True}
