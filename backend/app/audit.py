"""Audit trail: record security-relevant actions. Never raises (auditing must not break the action itself)."""
import json
import logging

from .db import connect

log = logging.getLogger("audit")


def record(action: str, actor=None, target: str | None = None, detail: dict | None = None, ip: str | None = None) -> None:
    """actor: a security.User (or None for system actions). Example: record("connection.create", user, f"connection:{cid}")."""
    try:
        with connect() as conn:
            conn.execute("INSERT INTO audit_log (actor_id, actor_email, action, target, detail, ip) VALUES (%s, %s, %s, %s, %s, %s)",
                         (getattr(actor, "id", None), getattr(actor, "email", None), action, target,
                          json.dumps(detail, default=str) if detail else None, ip))
    except Exception as e:  # pragma: no cover
        log.warning("audit write failed: %s", e)
