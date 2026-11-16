"""Platform: job queue, connector sync jobs and deletion sync, health, retention, spend limits, pooling."""
import types

import pytest
from fastapi.testclient import TestClient

from app import connectors, jobs, limits, llm, ops
from app.connectors.base import Doc
from app.db import connect
from app.main import app

CSRF = {"x-requested-with": "agentic-rag"}


def login(email, role="member"):
    from app.security import hash_password
    with connect() as conn:
        conn.execute("INSERT INTO users (email, password_hash, role) VALUES (%s, %s, %s) "
                     "ON CONFLICT (email) DO UPDATE SET password_hash = EXCLUDED.password_hash, role = EXCLUDED.role",
                     (email, hash_password("pw-1234567890"), role))
    c = TestClient(app, headers=CSRF)
    assert c.post("/api/auth/login", json={"email": email, "password": "pw-1234567890"}).status_code == 200
    return c


def user_id(email):
    with connect() as conn:
        return conn.execute("SELECT id FROM users WHERE email = %s", (email,)).fetchone()[0]


@pytest.fixture
def clean_jobs(monkeypatch):
    with connect() as conn:
        conn.execute("DELETE FROM jobs")
    monkeypatch.setattr(jobs, "BACKOFF_SECONDS", 0)  # retries are due immediately


def job(job_id):
    with connect() as conn:
        return conn.execute("SELECT status, attempts, error FROM jobs WHERE id = %s", (job_id,)).fetchone()


# ---------- job queue ----------

def test_jobs_run_dedupe_retry_and_fail(clean_jobs, monkeypatch):
    calls = []
    monkeypatch.setitem(jobs.HANDLERS, "t.ok", lambda p: calls.append(p["n"]))
    flaky = {"left": 1}

    def sometimes(p):
        if flaky["left"]:
            flaky["left"] -= 1
            raise RuntimeError("temporary")
    monkeypatch.setitem(jobs.HANDLERS, "t.flaky", sometimes)
    monkeypatch.setitem(jobs.HANDLERS, "t.bad", lambda p: 1 / 0)

    ok = jobs.enqueue("t.ok", {"n": 1}, dedupe_key="t.ok:1")
    assert jobs.enqueue("t.ok", {"n": 1}, dedupe_key="t.ok:1") is None  # one queued per key
    assert jobs.work_once() and calls == [1] and job(ok)[0] == "done"
    assert jobs.enqueue("t.ok", {"n": 2}, dedupe_key="t.ok:1")  # key is free again once finished
    assert jobs.work_once() and calls == [1, 2]

    fl = jobs.enqueue("t.flaky")
    assert jobs.work_once() and job(fl)[:2] == ("queued", 1) and job(fl)[2] == "temporary"
    assert jobs.work_once() and job(fl)[:2] == ("done", 2)

    bad = jobs.enqueue("t.bad", max_attempts=2)
    jobs.work_once(), jobs.work_once()
    assert job(bad)[:2] == ("failed", 2) and "division" in job(bad)[2]
    assert not jobs.work_once()  # nothing left

    unknown = jobs.enqueue("t.nobody")
    jobs.work_once()
    assert job(unknown)[:2] == ("failed", 1)  # no retries for a kind nobody handles


def test_claim_skips_locked_and_future_jobs(clean_jobs, monkeypatch):
    from datetime import datetime, timedelta, timezone
    monkeypatch.setitem(jobs.HANDLERS, "t.ok", lambda p: None)
    jobs.enqueue("t.ok", run_after=datetime.now(timezone.utc) + timedelta(hours=1))
    assert not jobs.work_once()  # not due yet
    j = jobs.enqueue("t.ok")
    with connect() as conn, conn.transaction():
        conn.execute("SELECT 1 FROM jobs WHERE id = %s FOR UPDATE", (j,))  # another worker holds it
        assert not jobs.work_once()
    assert jobs.work_once() and job(j)[0] == "done"


# ---------- connector sync as a job, and deletion sync ----------

@pytest.fixture
def fake_source(monkeypatch):
    items = {}

    def fetch(config, since, creds):
        for ext, (title, sheet) in list(items.items()):
            yield Doc(external_id=ext, title=title, text=f"{title} body text about zebrafish",
                      table_file=(f"{ext}.csv", sheet) if sheet else None)
    monkeypatch.setitem(connectors.REGISTRY, "fake", types.SimpleNamespace(LABEL="Fake", CONFIG=[], fetch=fetch))
    with connect() as conn:
        cid = conn.execute("INSERT INTO connections (kind, name) VALUES ('fake', 'pytest-fake') RETURNING id").fetchone()[0]
    yield cid, items
    connectors.drop_tables("connection_id = %s", (cid,))
    with connect() as conn:
        conn.execute("DELETE FROM connections WHERE id = %s", (cid,))


def indexed(cid):
    with connect() as conn:
        docs = {r[0] for r in conn.execute("SELECT external_id FROM documents WHERE connection_id = %s", (cid,)).fetchall()}
        tables = {r[0].split("::")[0] for r in conn.execute("SELECT external_id FROM data_tables WHERE connection_id = %s", (cid,))}
    return docs, tables


def test_sync_runs_as_job_and_full_sync_removes_deleted_items(clean_jobs, fake_source):
    cid, items = fake_source
    items.update({"a": ("Alpha", b"m,v\n1,2\n"), "b": ("Bravo", None), "c": ("Charlie", b"x,y\n3,4\n")})
    connectors.sync_in_background(cid)
    connectors.sync_in_background(cid)  # deduped: one sync per connection at a time
    assert connectors.is_running(cid)
    assert jobs.work_once() and not jobs.work_once()
    assert not connectors.is_running(cid)
    assert indexed(cid) == ({"a", "b", "c"}, {"a", "c"})

    del items["a"], items["b"]
    connectors.sync(cid)  # incremental: nothing removed
    assert indexed(cid) == ({"a", "b", "c"}, {"a", "c"})

    with connect() as conn:
        gone = conn.execute("SELECT name FROM data_tables WHERE connection_id = %s AND external_id LIKE 'a::%%'", (cid,)).fetchone()[0]
    connectors.sync(cid, full=True)
    assert indexed(cid) == ({"c"}, {"c"})
    with connect() as conn:
        status = conn.execute("SELECT status FROM connections WHERE id = %s", (cid,)).fetchone()[0]
        names = [r[0] for r in conn.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = 'data'")]
    assert status == "ok, 1 updated, 3 removed"  # documents a and b, table a
    assert gone not in names  # the table itself is dropped, not just unregistered

    items.clear()
    connectors.sync(cid, full=True)  # an empty listing never wipes the index
    assert indexed(cid) == ({"c"}, {"c"})


def test_failed_sync_fails_the_job(clean_jobs, fake_source, monkeypatch):
    cid, _ = fake_source
    monkeypatch.setattr(connectors.REGISTRY["fake"], "fetch", lambda *a: (_ for _ in ()).throw(RuntimeError("token revoked")))
    connectors.sync_in_background(cid)
    jobs.work_once()
    assert jobs.recent(1)[0]["status"] == "failed" and "token revoked" in jobs.recent(1)[0]["error"]


def test_periodic_sync_enqueue_is_deduped(clean_jobs, fake_source, monkeypatch):
    cid, _ = fake_source
    monkeypatch.setattr(connectors, "SYNC_INTERVAL_MIN", 30)
    connectors.enqueue_due_syncs()
    connectors.enqueue_due_syncs()
    with connect() as conn:
        n = conn.execute("SELECT count(*) FROM jobs WHERE dedupe_key = %s", (f"connector.sync:{cid}",)).fetchone()[0]
    assert n == 1


# ---------- health, jobs view ----------

def test_health_needs_no_sign_in_and_reports_worker():
    from app import worker
    worker.heartbeat()
    r = TestClient(app).get("/api/health")
    body = r.json()
    assert r.status_code == 200 and body["db"] and body["migrations"] >= 13 and body["version"]
    assert body["worker"]["healthy"] and body["worker"]["heartbeat_age_seconds"] < 60


def test_one_worker_ticks_per_minute():
    from app import worker
    with connect() as conn:
        conn.execute("DELETE FROM app_settings WHERE key = 'schedules_tick'")
    assert worker.claim_tick() and not worker.claim_tick()


def test_jobs_endpoint_is_admin_only(clean_jobs):
    jobs.enqueue("t.view")
    admin, member = login("plat-admin@acme.test", "admin"), login("plat-member@acme.test")
    assert member.get("/api/jobs").status_code == 403
    body = admin.get("/api/jobs").json()
    assert body["jobs"][0]["kind"] == "t.view" and "mode" in body["worker"]


# ---------- retention ----------

def test_retention_purges_old_rows_and_audits():
    admin = login("ret-admin@acme.test", "admin")
    uid = user_id("ret-admin@acme.test")
    with connect() as conn:
        old_conv = conn.execute("INSERT INTO conversations (user_id, title, updated_at) VALUES (%s, 'old', now() - interval '40 days') "
                                "RETURNING id", (uid,)).fetchone()[0]
        new_conv = conn.execute("INSERT INTO conversations (user_id, title) VALUES (%s, 'new') RETURNING id", (uid,)).fetchone()[0]
        conn.execute("INSERT INTO runs (question, user_id, conversation_id, created_at) VALUES "
                     "('old q', %s, %s, now() - interval '40 days'), ('new q', %s, %s, now())", (uid, old_conv, uid, new_conv))
        conn.execute("INSERT INTO notifications (user_id, kind, title, created_at) VALUES "
                     "(%s, 'x', 'old', now() - interval '10 days'), (%s, 'x', 'new', now())", (uid, uid))
        conn.execute("INSERT INTO audit_log (action, at) VALUES ('pytest.old', now() - interval '400 days')")

    assert admin.put("/api/settings/retention", json={"questions_days": -1}).status_code == 422
    r = admin.put("/api/settings/retention", json={"questions_days": 30, "notifications_days": 7, "audit_days": 365})
    assert r.status_code == 200 and admin.get("/api/settings/retention").json()["notifications_days"] == 7

    deleted = ops.purge()
    assert deleted["questions"] >= 1 and deleted["conversations"] >= 1 and deleted["notifications"] >= 1 and deleted["audit_entries"] >= 1
    with connect() as conn:
        assert conn.execute("SELECT count(*) FROM runs WHERE question IN ('old q', 'new q')").fetchone()[0] == 1
        assert conn.execute("SELECT id FROM conversations WHERE id IN (%s, %s)", (old_conv, new_conv)).fetchall() == [(new_conv,)]
        assert not conn.execute("SELECT 1 FROM audit_log WHERE action = 'pytest.old'").fetchone()
        assert conn.execute("SELECT 1 FROM audit_log WHERE action = 'settings.retention'").fetchone()
        detail = conn.execute("SELECT detail FROM audit_log WHERE action = 'retention.purge' ORDER BY id DESC").fetchone()[0]
    assert detail["deleted"]["questions"] >= 1
    admin.put("/api/settings/retention", json={})  # back to keep forever for other tests


# ---------- spend controls and rate limits ----------

@pytest.fixture
def reset_limits():
    yield
    with connect() as conn:
        conn.execute("DELETE FROM app_settings WHERE key = 'limits'")
    limits._recent.clear()


def test_daily_token_budget_and_per_minute_limit(reset_limits, monkeypatch):
    admin, member = login("lim-admin@acme.test", "admin"), login("lim-member@acme.test")
    monkeypatch.setattr("app.agent.run", lambda *a, **k: iter([{"type": "done"}]))
    assert admin.put("/api/settings/limits", json={"per_minute": 2}).status_code == 200
    assert [member.post("/api/chat", json={"question": "hi"}).status_code for _ in range(3)] == [200, 200, 429]
    assert "per minute" in member.post("/api/chat", json={"question": "hi"}).json()["detail"]

    limits._recent.clear()
    admin.put("/api/settings/limits", json={"daily_tokens": 1000})
    with connect() as conn:
        conn.execute("INSERT INTO runs (question, user_id, input_tokens, output_tokens) VALUES ('q', %s, 900, 200)",
                     (user_id("lim-member@acme.test"),))
    r = member.post("/api/chat", json={"question": "hi"})
    assert r.status_code == 429 and r.json()["detail"].startswith("You've reached today's AI limit")
    assert admin.post("/api/chat", json={"question": "hi"}).status_code == 200  # budgets are per person


def test_allowed_models_validated_and_enforced(reset_limits, monkeypatch):
    admin = login("lim-admin@acme.test", "admin")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    current = llm.ai_settings()["models"]
    assert admin.put("/api/settings/limits", json={"allowed_models": {"anthropic": ["bad model!"]}}).status_code == 422
    r = admin.put("/api/settings/limits", json={"allowed_models": {"anthropic": ["some-other-model"]}})
    assert r.status_code == 422 and current["anthropic"] in r.json()["detail"]  # would strand the model in use
    assert admin.put("/api/settings/limits", json={"allowed_models": {"anthropic": [current["anthropic"]]}}).status_code == 200
    assert {p.name for p in llm.providers()} == {"anthropic", "openai"}
    assert admin.put("/api/ai", json={"models": {"anthropic": "not-allowed-model"}}).status_code == 422

    with connect() as conn:  # a model saved before the list existed is refused at question time
        conn.execute("UPDATE app_settings SET value = jsonb_set(value, '{allowed_models,openai}', '[\"gpt-other\"]') WHERE key = 'limits'")
    assert [p.name for p in llm.providers()] == ["anthropic"]


# ---------- pooling ----------

def test_pooled_connections_are_autocommit_and_reset():
    with connect() as conn:
        conn.autocommit = False
        conn.execute("SELECT 1")
        conn.rollback()
    for _ in range(3):
        with connect() as conn:
            assert conn.autocommit
            assert conn.execute("SELECT '[1,2]'::vector").fetchone()[0].to_list() == [1, 2]  # pgvector registered
