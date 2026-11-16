"""Saved conversations: list, open, rename, delete, share read-only, fork. Private to their owner unless shared."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from . import audit, notify
from .db import connect
from .security import User, current_user

router = APIRouter()
HISTORY_TURNS = 10  # questions + answers sent back to the AI as context (20 messages)


def owned(conversation_id: int, user: User) -> dict:
    with connect() as conn:
        row = conn.execute("SELECT id, title, created_at, updated_at FROM conversations WHERE id = %s AND user_id = %s",
                           (conversation_id, user.id)).fetchone()
    if not row:
        raise HTTPException(404, "Conversation not found")
    return {"id": row[0], "title": row[1], "created_at": row[2], "updated_at": row[3]}


def create(user: User, first_question: str) -> int:
    title = " ".join(first_question.split())
    title = title if len(title) <= 80 else title[:79].rsplit(" ", 1)[0] + "…"
    with connect() as conn:
        return conn.execute("INSERT INTO conversations (user_id, title) VALUES (%s, %s) RETURNING id", (user.id, title)).fetchone()[0]


def history(conversation_id: int) -> list[dict]:
    """The context the AI gets: recent questions and answers, each answer with its hidden query note."""
    with connect() as conn:
        rows = conn.execute("SELECT question, answer, display->>'memory' FROM runs WHERE conversation_id = %s "
                            "ORDER BY id DESC LIMIT %s", (conversation_id, HISTORY_TURNS)).fetchall()
    out = []
    for q, a, memory in reversed(rows):
        out.append({"role": "user", "content": q})
        if a:
            out.append({"role": "assistant", "content": f"{a}\n\n{memory}" if memory else a})
    return out


@router.get("/api/conversations")
def list_conversations(user: User = Depends(current_user)):
    with connect() as conn:
        rows = conn.execute("SELECT c.id, c.title, c.updated_at, count(r.id) FROM conversations c LEFT JOIN runs r ON r.conversation_id = c.id "
                            "WHERE c.user_id = %s GROUP BY c.id ORDER BY c.updated_at DESC LIMIT 100", (user.id,)).fetchall()
    return [{"id": r[0], "title": r[1], "updated_at": r[2], "questions": r[3]} for r in rows]


SHARED_WITH = "EXISTS (SELECT 1 FROM conversation_shares s WHERE s.conversation_id = c.id AND (s.user_id IS NULL OR s.user_id = %s))"


def viewable(conversation_id: int, user: User) -> dict:
    """Owner, or someone it's shared with (directly or with everyone). shared_by is set for viewers."""
    with connect() as conn:
        row = conn.execute(f"SELECT c.id, c.title, c.created_at, c.updated_at, c.user_id, u.email FROM conversations c "
                           f"JOIN users u ON u.id = c.user_id WHERE c.id = %s AND (c.user_id = %s OR {SHARED_WITH})",
                           (conversation_id, user.id, user.id)).fetchone()
    if not row:
        raise HTTPException(404, "Conversation not found")
    return {"id": row[0], "title": row[1], "created_at": row[2], "updated_at": row[3],
            "shared_by": None if row[4] == user.id else row[5]}


@router.get("/api/shared-conversations")
def shared_with_me(user: User = Depends(current_user)):
    with connect() as conn:
        rows = conn.execute(f"SELECT c.id, c.title, c.updated_at, u.email FROM conversations c JOIN users u ON u.id = c.user_id "
                            f"WHERE c.user_id <> %s AND {SHARED_WITH} ORDER BY c.updated_at DESC LIMIT 100", (user.id, user.id)).fetchall()
    return [{"id": r[0], "title": r[1], "updated_at": r[2], "shared_by": r[3]} for r in rows]


@router.get("/api/conversations/{conversation_id}")
def get_conversation(conversation_id: int, user: User = Depends(current_user)):
    conv = viewable(conversation_id, user)
    with connect() as conn:
        rows = conn.execute("SELECT id, question, answer, display, latency_ms, attempts, critic, feedback, input_tokens, output_tokens "
                            "FROM runs WHERE conversation_id = %s ORDER BY id", (conversation_id,)).fetchall()
    turns = []
    for rid, q, a, display, latency, attempts, critic, feedback, tin, tout in rows:
        d = display or {}
        verified = bool(critic and critic.get("grounded") and critic.get("complete") and critic.get("relevant", True)
                        and critic.get("hallucination_risk") != "high")
        turns.append({"run_id": rid, "question": q, "answer": a or "", "sources": d.get("sources", []), "charts": d.get("charts", []),
                      "steps": d.get("steps", []), "memory": d.get("memory", ""), "latency_ms": latency, "attempts": attempts,
                      "verified": verified, "feedback": None if conv["shared_by"] else feedback, "input_tokens": tin, "output_tokens": tout})
    return {**conv, "turns": turns}


class Rename(BaseModel):
    title: str = Field(min_length=1, max_length=120)


@router.patch("/api/conversations/{conversation_id}")
def rename_conversation(conversation_id: int, body: Rename, user: User = Depends(current_user)):
    owned(conversation_id, user)
    with connect() as conn:
        conn.execute("UPDATE conversations SET title = %s WHERE id = %s", (body.title.strip(), conversation_id))
    return {"ok": True}


@router.delete("/api/conversations/{conversation_id}")
def delete_conversation(conversation_id: int, user: User = Depends(current_user)):
    """Removes it from your list. The questions stay in Activity and Usage records for administrators."""
    owned(conversation_id, user)
    with connect() as conn:
        conn.execute("DELETE FROM conversations WHERE id = %s", (conversation_id,))
    return {"ok": True}


# ---------- sharing (read-only) ----------

class Shares(BaseModel):
    everyone: bool = False
    user_ids: list[int] = Field(default_factory=list, max_length=500)


@router.get("/api/conversations/{conversation_id}/shares")
def get_shares(conversation_id: int, user: User = Depends(current_user)):
    owned(conversation_id, user)
    with connect() as conn:
        shared = [r[0] for r in conn.execute("SELECT user_id FROM conversation_shares WHERE conversation_id = %s", (conversation_id,)).fetchall()]
        people = conn.execute("SELECT id, email FROM users WHERE id <> %s ORDER BY email", (user.id,)).fetchall()
    return {"everyone": None in shared, "user_ids": [i for i in shared if i is not None],
            "people": [{"id": r[0], "email": r[1]} for r in people]}


@router.put("/api/conversations/{conversation_id}/shares")
def set_shares(conversation_id: int, body: Shares, user: User = Depends(current_user)):
    conv = owned(conversation_id, user)
    with connect() as conn, conn.transaction():
        valid = {r[0] for r in conn.execute("SELECT id FROM users WHERE id = ANY(%s) AND id <> %s", (body.user_ids, user.id)).fetchall()}
        want = valid | ({None} if body.everyone else set())
        have = {r[0] for r in conn.execute("SELECT user_id FROM conversation_shares WHERE conversation_id = %s", (conversation_id,)).fetchall()}
        added, removed = want - have, have - want
        if removed:
            conn.execute("DELETE FROM conversation_shares WHERE conversation_id = %s AND coalesce(user_id, 0) = ANY(%s)",
                         (conversation_id, [i or 0 for i in removed]))
        for i in added:
            conn.execute("INSERT INTO conversation_shares (conversation_id, user_id, shared_by) VALUES (%s, %s, %s)",
                         (conversation_id, i, user.id))
    target = f"conversation:{conversation_id}"
    for i in added:
        audit.record("conversation.share", user, target, {"with": "everyone" if i is None else i})
    for i in removed:
        audit.record("conversation.unshare", user, target, {"with": "everyone" if i is None else i})
    if added:
        recipients = [u for u in notify.users_who_can_see(None) if u != user.id] if None in added else [i for i in added if i]
        notify.notify(recipients, "share", f"{user.email} shared a conversation with you", conv["title"], f"/?c={conversation_id}")
    return get_shares(conversation_id, user)


@router.post("/api/conversations/{conversation_id}/fork")
def fork_conversation(conversation_id: int, user: User = Depends(current_user)):
    """Copy a conversation shared with you into a new one you own, so you can continue it. Answers are copied, not re-run."""
    conv = viewable(conversation_id, user)
    with connect() as conn, conn.transaction():
        new_id = conn.execute("INSERT INTO conversations (user_id, title) VALUES (%s, %s) RETURNING id", (user.id, conv["title"][:120])).fetchone()[0]
        conn.execute("INSERT INTO runs (question, answer, display, critic, attempts, latency_ms, user_id, conversation_id, input_tokens, output_tokens) "
                     "SELECT question, answer, display, critic, attempts, latency_ms, %s, %s, 0, 0 FROM runs WHERE conversation_id = %s ORDER BY id",
                     (user.id, new_id, conversation_id))
    return {"id": new_id}
