"""Collaboration: shared conversations, comments with @mentions, notifications, document update alerts."""
from fastapi.testclient import TestClient
from test_conversations import Recorder, events

from app import llm
from app.comments import mentioned
from app.db import connect
from app.main import app
from app.security import hash_password

CSRF = {"x-requested-with": "agentic-rag"}


def login(email, role="member"):
    with connect() as conn:
        conn.execute("INSERT INTO users (email, password_hash, role) VALUES (%s, %s, %s) ON CONFLICT (email) DO UPDATE "
                     "SET password_hash = EXCLUDED.password_hash, role = EXCLUDED.role", (email, hash_password("pw-1234567890"), role))
    c = TestClient(app, headers=CSRF)
    assert c.post("/api/auth/login", json={"email": email, "password": "pw-1234567890"}).status_code == 200
    return c


def uid(email):
    with connect() as conn:
        return conn.execute("SELECT id FROM users WHERE email = %s", (email,)).fetchone()[0]


def notes(client, kind):
    return [n for n in client.get("/api/notifications").json()["items"] if n["kind"] == kind]


def test_share_view_fork_and_unshare(monkeypatch):
    monkeypatch.setattr(llm, "providers", lambda: [Recorder()])
    ana, bo, cy = login("sh-ana@acme.test"), login("sh-bo@acme.test"), login("sh-cy@acme.test")
    cid = events(ana.post("/api/chat", json={"question": "How many PTO days?"}))[0]["id"]

    assert bo.get(f"/api/conversations/{cid}").status_code == 404
    assert bo.put(f"/api/conversations/{cid}/shares", json={"user_ids": [uid("sh-bo@acme.test")]}).status_code == 404  # only the owner shares
    r = ana.put(f"/api/conversations/{cid}/shares", json={"user_ids": [uid("sh-bo@acme.test")]}).json()
    assert r["user_ids"] == [uid("sh-bo@acme.test")] and not r["everyone"]
    assert "sh-bo@acme.test" in [p["email"] for p in r["people"]]

    n = notes(bo, "share")
    assert n and n[0]["link"] == f"/?c={cid}" and "sh-ana@acme.test" in n[0]["title"]
    conv = bo.get(f"/api/conversations/{cid}").json()
    assert conv["shared_by"] == "sh-ana@acme.test" and conv["turns"][0]["answer"] == "15 days."
    assert ana.get(f"/api/conversations/{cid}").json()["shared_by"] is None
    assert [c["id"] for c in bo.get("/api/shared-conversations").json()] == [cid]
    assert cy.get(f"/api/conversations/{cid}").status_code == 404
    # read-only: viewers can't post into it, rename it or delete it
    assert bo.post("/api/chat", json={"question": "x", "conversation_id": cid}).status_code == 404
    assert bo.delete(f"/api/conversations/{cid}").status_code == 404

    calls = len(Recorder.seen)
    fork = bo.post(f"/api/conversations/{cid}/fork").json()["id"]
    assert len(Recorder.seen) == calls  # copied, not re-run
    forked = bo.get(f"/api/conversations/{fork}").json()
    assert forked["shared_by"] is None and forked["turns"][0]["answer"] == "15 days." and forked["turns"][0]["question"] == "How many PTO days?"

    ana.put(f"/api/conversations/{cid}/shares", json={"everyone": True})
    assert cy.get(f"/api/conversations/{cid}").status_code == 200 and notes(cy, "share")
    ana.put(f"/api/conversations/{cid}/shares", json={})
    assert bo.get(f"/api/conversations/{cid}").status_code == 404
    assert cy.get(f"/api/conversations/{cid}").status_code == 404
    with connect() as conn:
        actions = [r[0] for r in conn.execute("SELECT action FROM audit_log WHERE target = %s", (f"conversation:{cid}",)).fetchall()]
    assert actions.count("conversation.share") == 2 and actions.count("conversation.unshare") == 2


def test_mention_parsing():
    login("ment-dee@acme.test")
    login("ment-eli@other.test")
    ids = mentioned("Thanks @ment-dee, and @ment-eli@other.test. Not bob@ment-dee.com or @nobody")
    assert ids == {uid("ment-dee@acme.test"), uid("ment-eli@other.test")}
    assert mentioned("no mentions here") == set()


def test_comments_on_runs_and_tiles(monkeypatch):
    monkeypatch.setattr(llm, "providers", lambda: [Recorder()])
    ana, bo, cy = login("cm-ana@acme.test"), login("cm-bo@acme.test"), login("cm-cy@acme.test")
    ev = events(ana.post("/api/chat", json={"question": "PTO?"}))
    cid, run_id = ev[0]["id"], ev[-1]["run_id"]

    # cy can't see the run, so can't read or comment, and isn't notified when mentioned
    assert cy.get(f"/api/comments?target_type=run&target_id={run_id}").status_code == 404
    assert cy.post("/api/comments", json={"target_type": "run", "target_id": run_id, "body": "hi"}).status_code == 404
    assert ana.post("/api/comments", json={"target_type": "run", "target_id": run_id, "body": "@cm-cy @cm-bo look"}).status_code == 200
    assert not notes(cy, "mention") and not notes(bo, "mention")  # bo can't see it yet either

    ana.put(f"/api/conversations/{cid}/shares", json={"user_ids": [uid("cm-bo@acme.test")]})
    r = bo.post("/api/comments", json={"target_type": "run", "target_id": run_id, "body": "Agreed, @cm-ana"})
    assert r.status_code == 200
    m = notes(ana, "mention")
    assert m and m[0]["link"] == f"/?c={cid}" and "Agreed" in m[0]["body"]
    thread = ana.get(f"/api/comments?target_type=run&target_id={run_id}").json()
    assert [c["author"] for c in thread] == ["cm-ana@acme.test", "cm-bo@acme.test"] and [c["mine"] for c in thread] == [True, False]

    # edit/delete own only; editing notifies only newly mentioned people
    mine = thread[1]["id"]
    assert ana.patch(f"/api/comments/{mine}", json={"body": "hijack"}).status_code == 404
    assert ana.delete(f"/api/comments/{mine}").status_code == 404
    before = len(notes(ana, "mention"))
    assert bo.patch(f"/api/comments/{mine}", json={"body": "Agreed, @cm-ana (edited)"}).status_code == 200
    assert len(notes(ana, "mention")) == before
    assert bo.get(f"/api/comments?target_type=run&target_id={run_id}").json()[1]["edited_at"]
    assert bo.delete(f"/api/comments/{mine}").status_code == 200
    assert len(bo.get(f"/api/comments?target_type=run&target_id={run_id}").json()) == 1

    # tiles: owner only until shared
    with connect() as conn:
        tile = conn.execute("INSERT INTO dashboard_tiles (owner_id, title, spec, source, sql, shared) VALUES (%s, 't', '{}', 'uploads', 'SELECT 1', false) "
                            "RETURNING id", (uid("cm-ana@acme.test"),)).fetchone()[0]
    assert bo.get(f"/api/comments?target_type=tile&target_id={tile}").status_code == 404
    with connect() as conn:
        conn.execute("UPDATE dashboard_tiles SET shared = true WHERE id = %s", (tile,))
    assert cy.post("/api/comments", json={"target_type": "tile", "target_id": tile, "body": "@cm-bo see this"}).status_code == 200
    assert notes(bo, "mention")[0]["link"] == "/dashboard"
    assert bo.post("/api/comments", json={"target_type": "tile", "target_id": 999999, "body": "x"}).status_code == 404


def test_notification_read_one():
    ana, bo = login("nt-ana@acme.test"), login("nt-bo@acme.test")
    from app.notify import notify
    notify([uid("nt-ana@acme.test")], "share", "a")
    notify([uid("nt-ana@acme.test")], "share", "b")
    items = ana.get("/api/notifications").json()
    first = items["items"][0]["id"]
    assert bo.post(f"/api/notifications/{first}/read").status_code == 200  # someone else's: no effect
    assert ana.get("/api/notifications").json()["unread"] == items["unread"]
    ana.post(f"/api/notifications/{first}/read")
    assert ana.get("/api/notifications").json()["unread"] == items["unread"] - 1


def test_document_update_notifies_people_who_can_see_it():
    ana, bo, admin = login("pol-ana@acme.test"), login("pol-bo@acme.test"), login("pol-admin@acme.test", role="admin")
    v1, v2 = b"# Policy\n\nRemote work: 2 days.\n", b"# Policy\n\nRemote work: 3 days.\n"
    pub = ana.post("/api/upload", files={"file": ("pol-public.md", v1)}).json()["id"]
    private = ana.post("/api/upload", files={"file": ("pol-private.md", v1)}, data={"visibility": "private"}).json()["id"]

    assert ana.post(f"/api/documents/{pub}/replace", files={"file": ("pol-public.md", v2)}).status_code == 200
    n = notes(bo, "policy_change")
    assert n[0]["title"] == "Updated: pol-public.md" and "Remote work: 3 days" in n[0]["body"] and n[0]["link"] == "/library"
    assert not notes(ana, "policy_change")  # not the person who replaced it

    ana.post(f"/api/documents/{private}/replace", files={"file": ("pol-private.md", v2)})
    assert not any(x["title"] == "Updated: pol-private.md" for x in notes(bo, "policy_change"))

    assert bo.put("/api/settings/notifications", json={"document_updates": False}).status_code == 403
    assert admin.get("/api/settings/notifications").json() == {"document_updates": True}
    admin.put("/api/settings/notifications", json={"document_updates": False})
    count = len(notes(bo, "policy_change"))
    ana.post(f"/api/documents/{pub}/replace", files={"file": ("pol-public.md", v1)})
    assert len(notes(bo, "policy_change")) == count
    admin.put("/api/settings/notifications", json={"document_updates": True})
    assert admin.get("/api/settings/notifications").json()["document_updates"] is True
    for d in (pub, private):  # public documents would show up in other tests' searches
        assert ana.delete(f"/api/documents/{d}").status_code == 200
