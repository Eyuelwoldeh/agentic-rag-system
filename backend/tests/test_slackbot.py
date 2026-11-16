"""Slack bot: signature checks, url_verification, and answering a mention as the asking person (no real Slack or AI)."""
import hashlib
import hmac
import json
import time

import httpx
from fastapi.testclient import TestClient
from test_conversations import Recorder

from app import llm, slackbot
from app.connectors import base
from app.db import connect
from app.main import app
from app.security import hash_password

SECRET = "sign-secret"


def signed(payload: dict, ts: int | None = None, secret: str = SECRET) -> tuple[bytes, dict]:
    body = json.dumps(payload).encode()
    ts = str(ts or int(time.time()))
    sig = "v0=" + hmac.new(secret.encode(), f"v0:{ts}:".encode() + body, hashlib.sha256).hexdigest()
    return body, {"x-slack-request-timestamp": ts, "x-slack-signature": sig, "content-type": "application/json"}


def setup_bot():
    admin = TestClient(app, headers={"x-requested-with": "agentic-rag"})
    with connect() as conn:
        conn.execute("INSERT INTO users (email, password_hash, role) VALUES ('slk-admin@acme.test', %s, 'admin') ON CONFLICT (email) DO NOTHING",
                     (hash_password("pw-1234567890"),))
    admin.post("/api/auth/login", json={"email": "slk-admin@acme.test", "password": "pw-1234567890"})
    r = admin.put("/api/slack/settings", json={"signing_secret": SECRET, "bot_token": "xoxb-test"}).json()
    assert r["signing_secret"] and r["bot_token"] and r["request_url"].endswith("/api/slack/events") and "users:read.email" in r["scopes"]
    with connect() as conn:  # stored encrypted
        assert b"xoxb-test" not in bytes(conn.execute("SELECT secret FROM app_settings WHERE key = 'slack_bot'").fetchone()[0])
    return admin


def test_verify_signature():
    body, h = signed({"a": 1})
    assert slackbot.verify(SECRET, h["x-slack-request-timestamp"], body, h["x-slack-signature"])
    assert not slackbot.verify("wrong", h["x-slack-request-timestamp"], body, h["x-slack-signature"])
    assert not slackbot.verify(SECRET, h["x-slack-request-timestamp"], body + b" ", h["x-slack-signature"])
    old_body, old = signed({"a": 1}, ts=int(time.time()) - 600)
    assert not slackbot.verify(SECRET, old["x-slack-request-timestamp"], old_body, old["x-slack-signature"])
    assert not slackbot.verify(SECRET, "nope", body, h["x-slack-signature"])


def test_events_endpoint_checks_signature_and_answers_challenge():
    setup_bot()
    c = TestClient(app)  # Slack sends no session cookie and no CSRF header
    body, h = signed({"type": "url_verification", "challenge": "abc"})
    r = c.post("/api/slack/events", content=body, headers=h)
    assert r.status_code == 200 and r.json() == {"challenge": "abc"}
    body, h = signed({"type": "url_verification", "challenge": "abc"}, secret="forged")
    assert c.post("/api/slack/events", content=body, headers=h).status_code == 401
    body, h = signed({"type": "url_verification", "challenge": "abc"}, ts=int(time.time()) - 400)
    assert c.post("/api/slack/events", content=body, headers=h).status_code == 401


def test_mention_is_answered_with_the_askers_access(monkeypatch):
    setup_bot()
    with connect() as conn:
        conn.execute("INSERT INTO users (email, password_hash, role) VALUES ('slk-ana@acme.test', 'x', 'member') ON CONFLICT DO NOTHING")
    monkeypatch.setattr(llm, "providers", lambda: [Recorder()])
    monkeypatch.setattr(slackbot, "_background", lambda fn, *a: fn(*a))
    posted, emails = [], {"U1": "SLK-ana@acme.test", "U2": "stranger@else.test"}

    def handler(req: httpx.Request):
        assert req.headers["authorization"] == "Bearer xoxb-test"
        if req.url.path.endswith("users.info"):
            return httpx.Response(200, json={"ok": True, "user": {"profile": {"email": emails[req.url.params["user"]]}}})
        if req.url.path.endswith("chat.postMessage"):
            posted.append(json.loads(req.content))
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(404, json={"ok": False})
    monkeypatch.setattr(base, "http", httpx.Client(transport=httpx.MockTransport(handler)))

    c = TestClient(app)
    ev = {"type": "app_mention", "user": "U1", "text": "<@UBOT> How many PTO days?", "channel": "C1", "ts": "111.1"}
    body, h = signed({"type": "event_callback", "event": ev})
    assert c.post("/api/slack/events", content=body, headers=h).status_code == 200
    assert posted[-1]["channel"] == "C1" and posted[-1]["thread_ts"] == "111.1"
    assert "15 days." in posted[-1]["text"] and "handbook.pdf" in posted[-1]["text"]
    assert Recorder.seen[-1][-1]["content"] == "How many PTO days?"
    with connect() as conn:
        assert conn.execute("SELECT u.email FROM runs r JOIN users u ON u.id = r.user_id ORDER BY r.id DESC LIMIT 1").fetchone()[0] == "slk-ana@acme.test"

    dm = {"type": "message", "channel_type": "im", "user": "U2", "text": "hello?", "channel": "D1", "ts": "222.2", "thread_ts": "200.0"}
    body, h = signed({"type": "event_callback", "event": dm})
    c.post("/api/slack/events", content=body, headers=h)
    assert posted[-1]["text"] == slackbot.ONBOARDING and posted[-1]["thread_ts"] == "200.0"

    n = len(posted)
    for ignored, extra in (({**dm, "user": "U1", "bot_id": "B1"}, {}), ({**dm, "user": "U1", "subtype": "message_changed"}, {}),
                           ({**ev}, {"x-slack-retry-num": "1"})):
        body, h = signed({"type": "event_callback", "event": ignored})
        assert c.post("/api/slack/events", content=body, headers={**h, **extra}).status_code == 200
    assert len(posted) == n


def test_markdown_to_mrkdwn():
    out = slackbot.to_mrkdwn("## Summary\n**PTO** is [15 days](https://x.test/h).\n- one")
    assert out == "*Summary*\n*PTO* is <https://x.test/h|15 days>.\n• one"
