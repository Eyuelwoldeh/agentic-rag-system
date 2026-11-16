"""Audit trail: record security-relevant actions. Never raises (auditing must not break the action itself)."""
import contextvars
import json
import logging

from .db import connect

log = logging.getLogger("audit")
_client_ip: contextvars.ContextVar[str | None] = contextvars.ContextVar("audit_client_ip", default=None)


class ClientIP:
    """ASGI middleware: remembers the request's client address so record() fills `ip` without passing Request around.
    Behind a proxy, run uvicorn with --proxy-headers so this is the real client, not the proxy."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        token = _client_ip.set((scope.get("client") or (None,))[0])
        try:
            await self.app(scope, receive, send)
        finally:
            _client_ip.reset(token)


def record(action: str, actor=None, target: str | None = None, detail: dict | None = None, ip: str | None = None) -> None:
    """actor: a security.User (or None for system actions). Example: record("connection.create", user, f"connection:{cid}")."""
    try:
        with connect() as conn:
            conn.execute("INSERT INTO audit_log (actor_id, actor_email, action, target, detail, ip) VALUES (%s, %s, %s, %s, %s, %s)",
                         (getattr(actor, "id", None), getattr(actor, "email", None), action, target,
                          json.dumps(detail, default=str) if detail else None, ip or _client_ip.get()))
    except Exception as e:  # pragma: no cover
        log.warning("audit write failed: %s", e)
