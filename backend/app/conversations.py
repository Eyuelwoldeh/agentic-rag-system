"""Saved conversations: list, open, rename, delete. Each person only ever sees their own."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

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


@router.get("/api/conversations/{conversation_id}")
def get_conversation(conversation_id: int, user: User = Depends(current_user)):
    conv = owned(conversation_id, user)
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
                      "verified": verified, "feedback": feedback, "input_tokens": tin, "output_tokens": tout})
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
