"""Slack bot: people @mention the app (or DM it) and get an answer in the thread, using only what THEY can see.

Slack calls POST /api/slack/events (no session: requests are verified with the signing secret). We reply 200 right
away and answer in a background thread, since Slack retries anything slower than 3 seconds.
"""
import hashlib
import hmac
import json
import logging
import re
import threading
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from . import agent, audit
from .connectors import base
from .db import connect
from .security import PUBLIC_URL, User, decrypt, encrypt, require_admin

router = APIRouter()
log = logging.getLogger("slackbot")
API = "https://slack.com/api/"
MAX_AGE_S = 5 * 60
SCOPES = ["app_mentions:read", "chat:write", "im:history", "users:read", "users:read.email"]
ONBOARDING = "Ask an administrator to add you to Agentic RAG."


# ---------- settings (encrypted in app_settings) ----------

def creds() -> dict:
    with connect() as conn:
        row = conn.execute("SELECT secret FROM app_settings WHERE key = 'slack_bot'").fetchone()
    return (decrypt(row[0]) if row and row[0] else None) or {}


class BotSettings(BaseModel):
    signing_secret: str = Field("", max_length=200)  # blank keeps the stored value
    bot_token: str = Field("", max_length=500)


def _status() -> dict:
    c = creds()
    return {"signing_secret": bool(c.get("signing_secret")), "bot_token": bool(c.get("bot_token")),
            "request_url": f"{PUBLIC_URL}/api/slack/events", "scopes": SCOPES}


@router.get("/api/slack/settings")
def get_settings(_: User = Depends(require_admin)):
    return _status()


@router.put("/api/slack/settings")
def put_settings(body: BotSettings, user: User = Depends(require_admin)):
    c = {**creds(), **{k: v.strip() for k, v in body.model_dump().items() if v.strip()}}
    with connect() as conn:
        conn.execute("INSERT INTO app_settings (key, secret) VALUES ('slack_bot', %s) ON CONFLICT (key) DO UPDATE "
                     "SET secret = EXCLUDED.secret, updated_at = now()", (encrypt(c),))
    audit.record("slack_bot.update", user)
    return _status()


@router.delete("/api/slack/settings")
def delete_settings(user: User = Depends(require_admin)):
    with connect() as conn:
        conn.execute("DELETE FROM app_settings WHERE key = 'slack_bot'")
    audit.record("slack_bot.remove", user)
    return _status()


# ---------- events ----------

def verify(secret: str, timestamp: str, body: bytes, signature: str, now: float | None = None) -> bool:
    """Slack v0 signature: HMAC-SHA256 of "v0:{timestamp}:{body}". Rejects requests older than 5 minutes (replays)."""
    try:
        if abs((now or time.time()) - int(timestamp)) > MAX_AGE_S:
            return False
    except (TypeError, ValueError):
        return False
    expected = "v0=" + hmac.new(secret.encode(), f"v0:{timestamp}:".encode() + body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature or "")


def _background(fn, *args) -> None:  # tests replace this to run inline
    threading.Thread(target=fn, args=args, daemon=True).start()


@router.post("/api/slack/events")
async def events(request: Request):
    body = await request.body()
    secret = creds().get("signing_secret")
    if not secret:
        raise HTTPException(503, "Slack bot is not set up")
    if not verify(secret, request.headers.get("x-slack-request-timestamp", ""), body, request.headers.get("x-slack-signature", "")):
        raise HTTPException(401, "Invalid signature")
    payload = json.loads(body or b"{}")
    if payload.get("type") == "url_verification":
        return {"challenge": payload.get("challenge")}
    if request.headers.get("x-slack-retry-num"):  # we already got this one; the answer is on its way
        return JSONResponse({"ok": True})
    ev = payload.get("event") or {}
    is_dm = ev.get("type") == "message" and ev.get("channel_type") == "im"
    if (ev.get("type") == "app_mention" or is_dm) and not ev.get("bot_id") and not ev.get("subtype") and ev.get("user"):
        _background(answer_event, ev)
    return {"ok": True}


def _slack(token: str, method: str, http_method: str = "GET", **kw) -> dict:
    data = base.request(http_method, API + method, retries=3, headers={"Authorization": f"Bearer {token}"}, **kw).json()
    if not data.get("ok"):
        raise RuntimeError(f"Slack {method}: {data.get('error')}")
    return data


def to_mrkdwn(md: str) -> str:
    """The basics of Markdown → Slack mrkdwn: bold, links, headings, bullets."""
    out = []
    for line in md.splitlines():
        line = re.sub(r"^\s*#{1,6}\s+(.*)$", r"**\1**", line)
        line = re.sub(r"^(\s*)[-*]\s+", r"\1• ", line)
        out.append(line)
    text = "\n".join(out)
    text = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", r"<\2|\1>", text)
    return re.sub(r"\*\*(.+?)\*\*|__(.+?)__", lambda m: f"*{m.group(1) or m.group(2)}*", text)


def _sources(items: list[dict]) -> str:
    lines, seen = [], set()
    for s in items:
        name = s.get("title") if s.get("kind") == "web" else s.get("document")
        if not name or name in seen:
            continue
        seen.add(name)
        lines.append(f"• <{s['url']}|{name}>" if s.get("url") else f"• {name}")
        if len(lines) == 5:
            break
    return "\n\n*Sources*\n" + "\n".join(lines) if lines else ""


def answer_event(ev: dict) -> None:
    token = creds().get("bot_token")
    if not token:
        return
    reply = {"channel": ev["channel"], "thread_ts": ev.get("thread_ts") or ev["ts"]}
    try:
        email = (_slack(token, "users.info", params={"user": ev["user"]})["user"].get("profile", {}).get("email") or "").lower()
        with connect() as conn:
            row = conn.execute("SELECT id, email FROM users WHERE lower(email) = %s", (email,)).fetchone() if email else None
        if not row:
            _slack(token, "chat.postMessage", "POST", json={**reply, "text": ONBOARDING})
            return
        question = re.sub(r"<@[A-Z0-9]+>", "", ev.get("text") or "").strip()
        if not question:
            _slack(token, "chat.postMessage", "POST", json={**reply, "text": "Ask me a question about your company's documents or data."})
            return
        answer, sources = "", []
        for e in agent.run(question, [], user_id=row[0], email=row[1]):  # that person's access, nobody else's
            if e["type"] == "sources":
                sources = e["items"]
            elif e["type"] == "done":
                answer = e["answer"]
        text = to_mrkdwn(answer or "Sorry, I couldn't answer that. Try again in the app.") + _sources(sources)
        _slack(token, "chat.postMessage", "POST", json={**reply, "text": text[:39000]})
    except Exception as e:
        log.warning("slack bot reply failed: %s", e)
