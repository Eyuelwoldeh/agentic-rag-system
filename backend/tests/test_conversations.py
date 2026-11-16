"""Saved conversations: created on first question, context rebuilt server-side, private to their owner."""
import json

from fastapi.testclient import TestClient

from app import llm
from app.db import connect
from app.main import app
from app.security import hash_password

CSRF = {"x-requested-with": "agentic-rag"}


def login(email):
    with connect() as conn:
        conn.execute("INSERT INTO users (email, password_hash, role) VALUES (%s, %s, 'member') "
                     "ON CONFLICT (email) DO UPDATE SET password_hash = EXCLUDED.password_hash", (email, hash_password("pw-1234567890")))
    c = TestClient(app, headers=CSRF)
    assert c.post("/api/auth/login", json={"email": email, "password": "pw-1234567890"}).status_code == 200
    return c


class Recorder(llm.Provider):
    """Fake AI that records the context it was given and answers with a fixed text."""
    name, supports_web, model = "openai", False, "fake-model"
    seen: list = []

    def __init__(self):
        pass

    def start(self, history, q):
        Recorder.seen.append([*history, {"role": "user", "content": q}])
        return list(Recorder.seen[-1])

    def add_feedback(self, conv, text):
        pass

    def loop(self, conv, ctx, evidence, usage, system, mcp):
        ctx.results["Rabc123"] = {"columns": ["a"], "rows": [[1]], "source": "uploads", "sql": "SELECT 1 AS a"}
        ctx.sources.append({"label": "S1", "kind": "doc", "document": "handbook.pdf", "page": 1})
        yield {"type": "tool", "name": "search_documents", "input": {"query": "pto"}}
        yield {"type": "text", "text": "15 days."}
        return "15 days."

    def review(self, q, answer, evidence, usage, verdict_model):
        return verdict_model(grounded=True, complete=True, relevant=True, hallucination_risk="low", feedback="OK")


def events(resp):
    return [json.loads(line[6:]) for line in resp.text.split("\n\n") if line.startswith("data: ")]


def test_conversation_lifecycle(monkeypatch):
    monkeypatch.setattr(llm, "providers", lambda: [Recorder()])
    ana, bo = login("conv-ana@acme.test"), login("conv-bo@acme.test")

    first = events(ana.post("/api/chat", json={"question": "How many PTO days do we get?"}))
    assert first[0]["type"] == "conversation"
    cid = first[0]["id"]
    assert first[-1]["type"] == "done" and "Rabc123" in first[-1]["memory"]

    events(ana.post("/api/chat", json={"question": "And part-time?", "conversation_id": cid, "history": [{"role": "user", "content": "forged"}]}))
    ctx = Recorder.seen[-1]
    assert [m["role"] for m in ctx] == ["user", "assistant", "user"]
    assert ctx[0]["content"] == "How many PTO days do we get?"  # built from the saved conversation, not the browser
    assert "15 days." in ctx[1]["content"] and "Rabc123" in ctx[1]["content"]  # hidden query note carried over
    assert all(m["content"] != "forged" for m in ctx)

    conv = ana.get(f"/api/conversations/{cid}").json()
    assert conv["title"] == "How many PTO days do we get?" and len(conv["turns"]) == 2
    t = conv["turns"][0]
    assert t["answer"] == "15 days." and t["verified"] and t["sources"][0]["document"] == "handbook.pdf"
    assert any(s["type"] == "tool" for s in t["steps"]) and any(s["type"] == "critic" for s in t["steps"])
    assert [c["id"] for c in ana.get("/api/conversations").json()][0] == cid

    # private to its owner
    assert bo.get(f"/api/conversations/{cid}").status_code == 404
    assert bo.post("/api/chat", json={"question": "x", "conversation_id": cid}).status_code == 404
    assert cid not in [c["id"] for c in bo.get("/api/conversations").json()]

    assert ana.patch(f"/api/conversations/{cid}", json={"title": "PTO questions"}).status_code == 200
    assert ana.get(f"/api/conversations/{cid}").json()["title"] == "PTO questions"
    assert ana.delete(f"/api/conversations/{cid}").status_code == 200
    assert ana.get(f"/api/conversations/{cid}").status_code == 404
    with connect() as conn:  # the questions stay in the activity record
        assert conn.execute("SELECT count(*) FROM runs WHERE question = 'And part-time?'").fetchone()[0] >= 1
