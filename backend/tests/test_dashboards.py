"""Dashboards, feedback and insights through the API."""
import pytest
from fastapi.testclient import TestClient

from app.db import connect
from app.ingest import ingest_csv
from app.main import app
from app.security import encrypt
import os

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


@pytest.fixture(scope="module")
def data():
    ingest_csv("dash_rev.csv", b"month,revenue\n2026-01,100\n2026-02,140\n")
    with connect() as conn:
        cid = conn.execute("INSERT INTO connections (kind, name, credentials, allowed) VALUES ('postgres', 'dash-fin', %s, %s) "
                           "RETURNING id", (encrypt({"access_token": os.environ["DATABASE_URL"]}), ["dash-cfo@acme.test"])).fetchone()[0]
    yield cid
    with connect() as conn:
        conn.execute("DROP TABLE IF EXISTS data.dash_rev")
        conn.execute("DELETE FROM connections WHERE id = %s", (cid,))


TILE = {"title": "Revenue by month", "type": "line", "x": "month", "y": ["revenue"], "source": "uploads",
        "sql": "SELECT month, revenue FROM dash_rev ORDER BY month"}


def test_pin_view_share_delete(data):
    ana, bo = login("dash-ana@acme.test"), login("dash-bo@acme.test")
    tid = ana.post("/api/dashboard/tiles", json=TILE).json()["id"]
    d = ana.get(f"/api/dashboard/tiles/{tid}/data").json()
    assert d["rows"] == [["2026-01", 100], ["2026-02", 140]] and d["spec"]["type"] == "line"

    assert tid not in [t["id"] for t in bo.get("/api/dashboard").json()]  # private by default
    assert bo.get(f"/api/dashboard/tiles/{tid}/data").status_code == 404
    assert bo.patch(f"/api/dashboard/tiles/{tid}", json={"shared": True}).status_code in (403, 404)

    assert ana.patch(f"/api/dashboard/tiles/{tid}", json={"shared": True}).status_code == 200
    assert tid in [t["id"] for t in bo.get("/api/dashboard").json()]
    assert bo.get(f"/api/dashboard/tiles/{tid}/data").json()["rows"][1] == ["2026-02", 140]
    assert bo.delete(f"/api/dashboard/tiles/{tid}").status_code == 403  # shared ≠ editable
    assert ana.delete(f"/api/dashboard/tiles/{tid}").status_code == 200


def test_refresh_picks_up_new_data(data):
    ana = login("dash-ana@acme.test")
    tid = ana.post("/api/dashboard/tiles", json=TILE).json()["id"]
    ingest_csv("dash_rev.csv", b"month,revenue\n2026-01,100\n2026-02,140\n2026-03,180\n")
    assert len(ana.get(f"/api/dashboard/tiles/{tid}/data").json()["rows"]) == 2  # cached
    assert len(ana.get(f"/api/dashboard/tiles/{tid}/data?refresh=true").json()["rows"]) == 3
    ana.delete(f"/api/dashboard/tiles/{tid}")


def test_restricted_source_never_leaks_through_cache(data):
    cfo, intern = login("dash-cfo@acme.test"), login("dash-intern@acme.test")
    src = f"conn-{data}"
    tile = {**TILE, "title": "Secret", "source": src, "sql": "SELECT 'q1' AS q, 99 AS profit", "x": "q", "y": ["profit"], "type": "bar"}
    assert intern.post("/api/dashboard/tiles", json=tile).status_code == 403  # can't pin what you can't read
    tid = cfo.post("/api/dashboard/tiles", json={**tile, "shared": True}).json()["id"]
    assert cfo.get(f"/api/dashboard/tiles/{tid}/data").json()["rows"] == [["q1", 99]]
    leak = intern.get(f"/api/dashboard/tiles/{tid}/data").json()
    assert leak.get("no_access") and "rows" not in leak  # shared tile, but the cached numbers are withheld
    cfo.delete(f"/api/dashboard/tiles/{tid}")


def test_bad_tile_rejected(data):
    ana = login("dash-ana@acme.test")
    assert ana.post("/api/dashboard/tiles", json={**TILE, "y": ["nope"]}).status_code == 422
    assert ana.post("/api/dashboard/tiles", json={**TILE, "sql": "DROP TABLE dash_rev"}).status_code == 422
    assert ana.post("/api/dashboard/tiles", json={**TILE, "type": "pie"}).status_code == 422


def test_feedback_and_insights():
    ana, admin = login("dash-ana@acme.test"), login("dash-admin@acme.test", role="admin")
    with connect() as conn:
        uid = conn.execute("SELECT id FROM users WHERE email = 'dash-ana@acme.test'").fetchone()[0]
        rid = conn.execute("INSERT INTO runs (question, answer, user_id, input_tokens, output_tokens, model, critic, latency_ms) "
                           "VALUES ('What is our refund policy?', 'x', %s, 1000000, 100000, 'claude-opus-5-5', "
                           "'{\"grounded\": false, \"complete\": true, \"hallucination_risk\": \"low\"}', 1200) RETURNING id",
                           (uid,)).fetchone()[0]
    assert admin.post(f"/api/runs/{rid}/feedback", json={"value": -1}).status_code == 404  # only the asker rates
    assert ana.post(f"/api/runs/{rid}/feedback", json={"value": -1, "note": "policy changed in 2026"}).status_code == 200
    assert ana.get("/api/insights").status_code == 403
    ins = admin.get("/api/insights").json()
    gap = next(g for g in ins["gaps"] if g["id"] == rid)
    assert gap["reason"] == "thumbs down" and gap["note"] == "policy changed in 2026"
    assert ins["thumbs_down"] >= 1 and ins["estimated_cost_usd"] >= 6.0  # 1M in × $4 + 0.1M out × $20
