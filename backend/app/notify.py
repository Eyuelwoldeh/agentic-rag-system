"""In-app notifications. Any feature can notify people; the bell in the UI reads them."""
import json

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from .db import connect
from .security import User, current_user, require_admin

router = APIRouter()


def notify(user_ids: list[int], kind: str, title: str, body: str = "", link: str | None = None) -> int:
    """Create one notification per user. Returns how many were created."""
    ids = sorted(set(i for i in user_ids if i))
    if not ids:
        return 0
    with connect() as conn, conn.cursor() as cur:
        cur.executemany("INSERT INTO notifications (user_id, kind, title, body, link) VALUES (%s, %s, %s, %s, %s)",
                        [(i, kind, title[:200], body[:2000], link) for i in ids])
    return len(ids)


def users_who_can_see(allowed: list[str] | None) -> list[int]:
    """User ids allowed by an access list (None = everyone). Used to notify only people who may see something."""
    with connect() as conn:
        if allowed is None:
            return [r[0] for r in conn.execute("SELECT id FROM users").fetchall()]
        return [r[0] for r in conn.execute("SELECT id FROM users WHERE email = ANY(%s)", ([a.lower() for a in allowed],)).fetchall()]


@router.get("/api/notifications")
def list_notifications(user: User = Depends(current_user)):
    with connect() as conn:
        rows = conn.execute("SELECT id, kind, title, body, link, read_at, created_at FROM notifications WHERE user_id = %s "
                            "ORDER BY id DESC LIMIT 50", (user.id,)).fetchall()
        unread = conn.execute("SELECT count(*) FROM notifications WHERE user_id = %s AND read_at IS NULL", (user.id,)).fetchone()[0]
    keys = ["id", "kind", "title", "body", "link", "read_at", "created_at"]
    return {"unread": unread, "items": [dict(zip(keys, r)) for r in rows]}


@router.post("/api/notifications/read")
def mark_all_read(user: User = Depends(current_user)):
    with connect() as conn:
        conn.execute("UPDATE notifications SET read_at = now() WHERE user_id = %s AND read_at IS NULL", (user.id,))
    return {"ok": True}


@router.post("/api/notifications/{notification_id}/read")
def mark_read(notification_id: int, user: User = Depends(current_user)):
    with connect() as conn:
        conn.execute("UPDATE notifications SET read_at = coalesce(read_at, now()) WHERE id = %s AND user_id = %s", (notification_id, user.id))
    return {"ok": True}


# ---------- admin settings ----------

DEFAULTS = {"document_updates": True}  # tell people when documents they can see are replaced


def settings() -> dict:
    with connect() as conn:
        row = conn.execute("SELECT value FROM app_settings WHERE key = 'notifications'").fetchone()
    return {**DEFAULTS, **(row[0] if row else {})}


class NotificationSettings(BaseModel):
    document_updates: bool


@router.get("/api/settings/notifications")
def get_settings(_: User = Depends(require_admin)):
    return settings()


@router.put("/api/settings/notifications")
def put_settings(body: NotificationSettings, _: User = Depends(require_admin)):
    with connect() as conn:
        conn.execute("INSERT INTO app_settings (key, value) VALUES ('notifications', %s) ON CONFLICT (key) DO UPDATE "
                     "SET value = EXCLUDED.value, updated_at = now()", (json.dumps(body.model_dump()),))
    return settings()
