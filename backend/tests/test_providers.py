"""AI providers: settings encryption, fallback between Claude and ChatGPT, the ChatGPT tool loop, no-AI analysis."""
from types import SimpleNamespace as NS

import anthropic
import httpx
import pytest

from app import agent, llm, tools
from app.db import connect
from app.ingest import ingest_spreadsheet


@pytest.fixture
def clean_settings(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with connect() as conn:
        conn.execute("DELETE FROM app_settings")
    yield
    with connect() as conn:
        conn.execute("DELETE FROM app_settings")


def test_settings_keys_encrypted_and_write_only(clean_settings):
    llm.save_settings(primary="openai", models={"openai": "gpt-5.5"}, keys={"openai": "sk-proj-SECRET123"})
    s = llm.ai_settings()
    assert s["primary"] == "openai" and s["models"]["openai"] == "gpt-5.5" and s["keys"] == {"anthropic": None, "openai": "settings"}
    assert "SECRET123" not in str(s)
    with connect() as conn:
        assert b"SECRET123" not in bytes(conn.execute("SELECT secret FROM app_settings WHERE key = 'ai_key:openai'").fetchone()[0])
    assert llm.api_key("openai") == "sk-proj-SECRET123"
    assert [p.name for p in llm.providers()] == ["openai"]  # only configured providers, primary first
    llm.save_settings(keys={"openai": ""})
    assert llm.providers() == []


def test_unavailable_reason():
    req = httpx.Request("POST", "https://api.anthropic.com")
    credit = anthropic.BadRequestError("Your credit balance is too low", response=httpx.Response(400, request=req), body=None)
    assert llm.unavailable_reason(credit) == "out of API credits"
    assert llm.unavailable_reason(RuntimeError("insufficient_quota: You exceeded your current quota")) == "out of API credits"
    assert llm.unavailable_reason(ValueError("bug in my code")) is None


class FakeProvider(llm.Provider):
    """Scriptable provider: raises `error` or answers `text`."""
    supports_web = False

    def __init__(self, name, error=None, text="ok", partial=""):
        self.name, self.error, self.text, self.partial, self.model = name, error, text, partial, f"{name}-model"

    def start(self, history, q):
        return [*history, {"role": "user", "content": q}]

    def add_feedback(self, conv, text):
        conv.append({"role": "user", "content": text})

    def loop(self, conv, ctx, evidence, usage, system, mcp):
        if self.partial:
            yield {"type": "text", "text": self.partial}
        if self.error:
            raise self.error
        yield {"type": "text", "text": self.text}
        return self.text

    def review(self, q, answer, evidence, usage, verdict_model):
        return verdict_model(grounded=True, complete=True, relevant=True, hallucination_risk="low", feedback="OK")


def run_with(monkeypatch, provs, **kw):
    monkeypatch.setattr(llm, "providers", lambda: provs)
    return list(agent.run("What is our refund policy?", [], **kw))


def test_falls_back_to_second_provider(monkeypatch):
    broke = FakeProvider("anthropic", error=RuntimeError("Your credit balance is too low"), partial="Half an ans")
    events = run_with(monkeypatch, [broke, FakeProvider("openai", text="Refunds within 30 days.")])
    restart = next(e for e in events if e["type"] == "restart")
    assert "Claude is unavailable (out of API credits)" in restart["reason"] and "ChatGPT" in restart["reason"]
    done = events[-1]
    assert done["answer"] == "Refunds within 30 days." and done["verified"]
    with connect() as conn:
        assert conn.execute("SELECT model FROM runs WHERE id = %s", (done["run_id"],)).fetchone()[0] == "openai-model"


def test_real_errors_are_not_swallowed(monkeypatch):
    events = run_with(monkeypatch, [FakeProvider("anthropic", error=ValueError("tool exploded")), FakeProvider("openai")])
    assert any(e["type"] == "error" and "tool exploded" in e["message"] for e in events)
    assert not any(e["type"] == "restart" for e in events)


def test_no_ai_analysis_still_answers(monkeypatch):
    rows = ["month,region,revenue"] + [f"2026-{m:02d}-01,{r},{v * m}" for m in range(1, 7) for r, v in (("North", 100), ("South", 40))]
    t = ingest_spreadsheet("prov-sales.csv", "\n".join(rows).encode())[0]["table"]
    events = run_with(monkeypatch, [FakeProvider("anthropic", error=RuntimeError("insufficient_quota"))], analyze=t)
    charts = [e["chart"] for e in events if e["type"] == "chart"]
    assert {c["type"] for c in charts} >= {"number", "line", "bar"}
    done = events[-1]
    assert "North leads region" in done["answer"] and "Connect an AI provider" in done["answer"]


def _chunk(content=None, tool_calls=None, finish=None, usage=None):
    choice = NS(delta=NS(content=content, tool_calls=tool_calls), finish_reason=finish)
    return NS(choices=[choice] if (content or tool_calls or finish) else [], usage=usage)


def test_chatgpt_tool_loop(monkeypatch):
    ingest_spreadsheet("prov-kpi.csv", b"region,revenue\nNorth,10\nSouth,32\n")
    turns = iter([
        [_chunk(tool_calls=[NS(index=0, id="call_1", function=NS(name="run_sql", arguments='{"source": "uploads", '))]),
         _chunk(tool_calls=[NS(index=0, id=None, function=NS(name="", arguments='"query": "SELECT sum(revenue) AS t FROM prov_kpi"}'))]),
         _chunk(finish="tool_calls"), _chunk(usage=NS(prompt_tokens=100, completion_tokens=20))],
        [_chunk(content="Total revenue "), _chunk(content="is 42."), _chunk(finish="stop"), _chunk(usage=NS(prompt_tokens=150, completion_tokens=8))],
    ])
    sent = []

    def create(**kw):
        sent.append(kw)
        return iter(next(turns))

    fake = NS(chat=NS(completions=NS(create=create)))
    p = llm.OpenAIProvider("sk", "gpt-5.5", client=fake)
    ctx, usage, conv = tools.Ctx(email=None), {"input_tokens": 0, "output_tokens": 0}, p.start([], "total revenue?")
    gen = p.loop(conv, ctx, [], usage, "sys", ([], []))
    events = []
    try:
        while True:
            events.append(next(gen))
    except StopIteration as stop:
        answer = stop.value
    assert answer == "Total revenue is 42."
    assert any(e["type"] == "tool" and e["name"] == "run_sql" for e in events)
    tool_msg = next(m for m in conv if m["role"] == "tool")
    assert tool_msg["tool_call_id"] == "call_1" and '"rows": [[42]]' in tool_msg["content"]
    assert usage == {"input_tokens": 250, "output_tokens": 28}
    assert sent[0]["tools"][0]["type"] == "function" and sent[0]["messages"][0] == {"role": "system", "content": "sys"}


def test_ai_settings_api(clean_settings, monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app
    from app.security import hash_password
    with connect() as conn:
        conn.execute("INSERT INTO users (email, password_hash, role) VALUES ('prov-admin@acme.test', %s, 'admin') "
                     "ON CONFLICT (email) DO UPDATE SET password_hash = EXCLUDED.password_hash, role = 'admin'", (hash_password("pw-1234567890"),))
    c = TestClient(app, headers={"x-requested-with": "agentic-rag"})
    c.post("/api/auth/login", json={"email": "prov-admin@acme.test", "password": "pw-1234567890"})

    def fake_models(provider, key=None):
        if key == "bad":
            raise ValueError("401 invalid api key")
        return ["gpt-5.5", "gpt-5.4-mini"]
    monkeypatch.setattr(llm, "list_models", fake_models)
    assert c.put("/api/ai", json={"keys": {"openai": "bad"}}).status_code == 422
    r = c.put("/api/ai", json={"primary": "openai", "keys": {"openai": "sk-good"}, "models": {"openai": "gpt-5.4-mini"}})
    assert r.status_code == 200 and r.json()["keys"]["openai"] == "settings" and "sk-good" not in r.text
    assert c.get("/api/ai").json()["models"]["openai"] == "gpt-5.4-mini"
