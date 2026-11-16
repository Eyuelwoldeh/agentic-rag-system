"""Metrics (semantic layer), forecasting, scheduled reports, alerts and conversation export."""
import json
from datetime import date, datetime

import pymupdf
import pytest
from fastapi.testclient import TestClient

from app import alerts, analysis, export, llm, metrics, reports, tools
from app.db import connect
from app.ingest import drop_tables, ingest_spreadsheet
from app.main import app
from app.security import hash_password

CSRF = {"x-requested-with": "agentic-rag"}


def login(email, role="member"):
    with connect() as conn:
        uid = conn.execute("INSERT INTO users (email, password_hash, role) VALUES (%s, %s, %s) ON CONFLICT (email) DO UPDATE "
                           "SET password_hash = EXCLUDED.password_hash, role = EXCLUDED.role RETURNING id",
                           (email, hash_password("pw-1234567890"), role)).fetchone()[0]
    c = TestClient(app, headers=CSRF)
    assert c.post("/api/auth/login", json={"email": email, "password": "pw-1234567890"}).status_code == 200
    c.uid = uid
    return c


@pytest.fixture(scope="module")
def sales():
    rows = ["day,location,revenue,cost_of_goods"]
    for y in (2025, 2026):
        for m in range(1, 12):
            rows.append(f"{y}-{m:02d}-05,Riverside,{1000 if (y, m) != (2026, 11) else 400},300")
            rows.append(f"{y}-{m:02d}-05,Downtown,{500 + (100 if y == 2026 else 0)},200")
    table = ingest_spreadsheet("ins-sales.csv", "\n".join(rows).encode())[0]["table"]
    secret = ingest_spreadsheet("ins-secret.csv", b"day,profit\n2026-11-02,5\n", allowed=["ins-cfo@acme.test"])[0]["table"]
    yield table, secret
    drop_tables("title LIKE %s", ("ins-%",))
    with connect() as conn:
        conn.execute("DELETE FROM metrics WHERE name LIKE 'ins %'")


# ---------- semantic layer ----------

COLS = ["day", "location", "revenue", "cost_of_goods"]


def test_expression_validation():
    assert metrics.clean_expression("sum(revenue) - SUM(Cost_Of_Goods)", COLS) == 'sum ( "revenue" ) - sum ( "cost_of_goods" )'
    assert metrics.clean_expression("round(coalesce(sum(revenue), 0) / nullif(count(*), 0), 2)", COLS)
    for bad in ("pg_sleep(1)", "sum(revenue); DROP TABLE x", "sum(secret)", "sum(revenue) -- x", "revenue",
                "sum((select 1))", "sum(revenue) > 5", "sum('a')", "sum(revenue", "sum(revenue::int)", "sum(revenue) /* x */"):
        with pytest.raises(ValueError):
            metrics.clean_expression(bad, COLS)
    assert metrics.clean_filter("location = 'River''side' AND day >= DATE '2026-01-01'", COLS) == \
        "\"location\" = 'River''side' AND \"day\" >= DATE '2026-01-01'"
    assert metrics.clean_filter("date > 3", ["date"]) == '"date" > 3'  # a column named date still works
    for bad in ("1 = 1 OR pg_sleep(1) IS NULL", "location = 'x' UNION SELECT 1", "sum(revenue) > 1"):
        with pytest.raises(ValueError):
            metrics.clean_filter(bad, COLS)


def test_build_sql():
    m = {"name": "Gross margin", "source": "uploads", "table_name": "t", "expression": "sum(revenue) - sum(cost_of_goods)",
         "time_column": "day", "filters": "location <> 'Closed'"}
    sql = metrics.build_sql(m, COLS, "postgres", group_by="location", period="month", start="2026-01-01", end="2026-07-01")
    assert sql == ("SELECT date_trunc('month', \"day\")::date AS period, \"location\", sum ( \"revenue\" ) - sum ( \"cost_of_goods\" ) "
                   "AS \"gross_margin\" FROM \"t\" WHERE (\"location\" <> 'Closed') AND \"day\" >= DATE '2026-01-01' "
                   "AND \"day\" < DATE '2026-07-01' GROUP BY 1, 2 ORDER BY 1")
    assert "DATE_TRUNC(DATE(`day`), QUARTER)" in metrics.build_sql(m, COLS, "bigquery", period="quarter")
    for kw in ({"group_by": "nope"}, {"group_by": "location; drop"}, {"period": "decade"}, {"start": "2026-01-01' OR 1=1 --"}):
        with pytest.raises(ValueError):
            metrics.build_sql(m, COLS, "postgres", **kw)


def test_metric_api_and_agent_tools(sales):
    table, secret = sales
    admin, ana = login("ins-admin@acme.test", "admin"), login("ins-ana@acme.test")
    body = {"name": "ins Revenue", "description": "Net sales", "source": "uploads", "table_name": table, "expression": "sum(revenue)",
            "time_column": "day", "format": "currency"}
    assert ana.post("/api/metrics", json=body).status_code == 403  # admins define metrics
    assert admin.post("/api/metrics", json={**body, "expression": "sum(nope)"}).status_code == 422
    assert admin.post("/api/metrics", json={**body, "expression": "pg_sleep(5)"}).status_code == 422
    mid = admin.post("/api/metrics", json=body).json()["id"]
    assert admin.post("/api/metrics", json={**body, "name": "INS revenue"}).status_code == 409
    assert any(m["name"] == "ins Revenue" for m in ana.get("/api/metrics").json())  # everyone can view
    assert ana.post(f"/api/metrics/{mid}/test").json()["value"] == 2 * 11 * 1000 - 600 + 11 * 500 + 11 * 600

    # a metric on a sheet the member can't see: hidden from the assistant, and Test fails for them
    cfo = login("ins-cfo@acme.test", "admin")
    sid = cfo.post("/api/metrics", json={**body, "name": "ins Profit", "table_name": secret, "expression": "sum(profit)"}).json()["id"]
    assert ana.post(f"/api/metrics/{sid}/test").status_code == 422
    ctx = tools.Ctx(email="ins-ana@acme.test")
    listed = json.loads(tools.execute("list_metrics", {}, ctx))
    assert "ins Revenue" in [m["name"] for m in listed]
    with pytest.raises(Exception):
        tools.execute("query_metric", {"name": "ins Profit"}, ctx)

    out = json.loads(tools.execute("query_metric", {"name": "ins revenue", "period": "month", "start": "2025-01-01", "end": "2026-01-01"}, ctx))
    assert out["metric"] == "ins Revenue" and out["columns"] == ["period", "ins_revenue"] and out["row_count"] == 11
    assert out["rows"][0] == ["2025-01-01", 1500]
    tools.execute("create_chart", {"result_id": out["result_id"], "type": "line", "title": "Revenue", "x": "period", "y": ["ins_revenue"]}, ctx)
    with pytest.raises(ValueError):
        tools.execute("query_metric", {"name": "ins revenue", "group_by": "salary"}, ctx)

    by_loc = json.loads(tools.execute("query_metric", {"name": "ins revenue", "group_by": "location"}, ctx))
    assert by_loc["rows"][0][0] == "Riverside"

    # forecast and what-if on a stored result
    fc = json.loads(tools.execute("forecast", {"result_id": out["result_id"], "date_column": "period", "value_column": "ins_revenue",
                                                "horizon": 3}, ctx))
    assert [f["period"] for f in fc["forecast"]] == ["2025-12-01", "2026-01-01", "2026-02-01"] and fc["method"] == "linear trend"
    chart = ctx.charts[-1]
    assert chart["type"] == "line" and chart["y"] == ["actual", "forecast"] and len(chart["rows"]) == 14
    assert chart["rows"][10][1] == chart["rows"][10][2] == 1500  # forecast line starts at the last actual
    wi = json.loads(tools.execute("what_if", {"result_id": out["result_id"], "column": "ins_revenue", "change_pct": -10}, ctx))
    assert wi["original_total"] == 16500 and wi["adjusted_total"] == 14850 and wi["difference"] == -1650

    assert ana.delete(f"/api/metrics/{mid}").status_code == 403
    assert admin.delete(f"/api/metrics/{mid}").status_code == 200
    with connect() as conn:
        assert conn.execute("SELECT count(*) FROM audit_log WHERE action = 'metric.delete' AND target = %s", (f"metric:{mid}",)).fetchone()[0] == 1


# ---------- forecasting math ----------

def test_linear_forecast_is_exact_on_a_line():
    f = analysis.forecast(["2026-01", "2026-02", "2026-03", "2026-04"], [1, 3, 5, 7], 2)
    assert f["periods"] == ["2026-05", "2026-06"] and f["forecast"] == [9, 11] and f["lower"] == f["upper"] == [9, 11]
    days = analysis.forecast(["2026-01-01", "2026-01-08", "2026-01-15"], [10, 12, 14], 1)
    assert days["periods"] == ["2026-01-22"] and days["forecast"] == [16]
    noisy = analysis.forecast(["a", "b", "c", "d"], [10, 14, 9, 15], 2)
    assert noisy["periods"] == ["+1", "+2"] and all(lo < p < hi for lo, p, hi in zip(noisy["lower"], noisy["forecast"], noisy["upper"]))
    with pytest.raises(ValueError):
        analysis.forecast(["2026-01", "2026-02"], [1, 2], 3)


def test_seasonal_forecast_repeats_the_pattern_plus_trend():
    season = [100, 80, 90, 120, 150, 200, 220, 210, 160, 130, 110, 300]
    values = season + [v + 12 for v in season]  # every month 12 higher than the year before
    periods = [f"{2024 + i // 12}-{i % 12 + 1:02d}-01" for i in range(24)]
    f = analysis.forecast(periods, values, 13)
    assert f["method"].startswith("seasonal") and f["periods"][0] == "2026-01-01" and f["periods"][-1] == "2027-01-01"
    assert f["forecast"][:12] == [v + 24 for v in season] and f["forecast"][12] == 100 + 36
    assert f["lower"][0] == f["forecast"][0]  # perfectly regular history → no spread


def test_what_if():
    assert analysis.what_if([100, 200, None, "x"], 5) == {"original_total": 300, "adjusted_total": 315, "difference": 15, "change_pct": 5}


# ---------- scheduled reports ----------

class FakeAI(llm.Provider):
    """Answers with the asker's email, so each recipient's run is distinguishable."""
    name, supports_web, model = "openai", False, "fake"

    def __init__(self):
        pass

    def start(self, history, q):
        return [{"role": "user", "content": q}]

    def add_feedback(self, conv, text):
        pass

    def loop(self, conv, ctx, evidence, usage, system, mcp):
        yield {"type": "text", "text": f"Answer for {ctx.email.split('@')[0]}"}
        return f"Answer for {ctx.email.split('@')[0]}"

    def review(self, q, answer, evidence, usage, verdict_model):
        return verdict_model(grounded=True, complete=True, relevant=True, hallucination_risk="low", feedback="OK")


def test_next_run():
    now = datetime(2026, 11, 16, 9, 30)  # a Monday
    nr = lambda *a, **k: reports.next_run(*a, after=now, **k).replace(tzinfo=None)  # noqa: E731
    assert nr("daily", 8) == datetime(2026, 11, 17, 8) and nr("daily", 10) == datetime(2026, 11, 16, 10)
    assert nr("weekly", 8, weekday=0) == datetime(2026, 11, 23, 8) and nr("weekly", 8, weekday=4) == datetime(2026, 11, 20, 8)
    assert nr("monthly", 8, day=1) == datetime(2026, 12, 1, 8) and nr("monthly", 8, day=20) == datetime(2026, 11, 20, 8)
    assert reports.next_run("monthly", 8, day=1, after=datetime(2026, 12, 5)).replace(tzinfo=None) == datetime(2027, 1, 1, 8)


def test_question_report_runs_with_each_persons_access(monkeypatch):
    monkeypatch.setattr(llm, "providers", lambda: [FakeAI()])
    monkeypatch.setattr(reports, "_background", lambda fn, *a: fn(*a))
    ana, bo = login("rep-ana@acme.test"), login("rep-bo@acme.test")
    body = {"title": "Weekly sales", "question": "How did sales do last week?", "cadence": "weekly", "weekday": 0, "hour": 7,
            "recipients": [bo.uid]}
    assert ana.post("/api/reports", json={**body, "recipients": [999999]}).status_code == 422
    assert ana.post("/api/reports", json={**body, "tile_ids": [1]}).status_code == 422  # question or charts, not both
    rid = ana.post("/api/reports", json=body).json()["id"]
    rep = ana.get("/api/reports").json()[0]
    assert rep["id"] == rid and rep["recipient_emails"] == ["rep-bo@acme.test"] and rep["next_run_at"]
    assert bo.get("/api/reports").json() == [] and bo.post(f"/api/reports/{rid}/run").status_code == 404

    assert ana.post(f"/api/reports/{rid}/run").status_code == 200
    for c, email in ((ana, "rep-ana@acme.test"), (bo, "rep-bo@acme.test")):
        n = next(i for i in c.get("/api/notifications").json()["items"] if i["kind"] == "report")
        conv = c.get(f"/api/conversations/{int(n['link'].split('=')[1])}").json()  # their own conversation
        assert conv["turns"][0]["answer"] == f"Answer for {email.split('@')[0]}" and conv["title"].startswith("Weekly sales")
    assert ana.get("/api/reports").json()[0]["last_status"] == "ok"

    # due reports run from the scheduler, and move to their next time
    with connect() as conn:
        conn.execute("UPDATE reports SET next_run_at = now() - interval '1 minute' WHERE id = %s", (rid,))
    reports.run_due_reports()
    with connect() as conn:
        assert conn.execute("SELECT next_run_at > now() FROM reports WHERE id = %s", (rid,)).fetchone()[0]
        assert conn.execute("SELECT count(*) FROM conversations WHERE user_id = %s", (bo.uid,)).fetchone()[0] == 2

    assert ana.patch(f"/api/reports/{rid}", json={"enabled": False}).status_code == 200
    assert ana.put(f"/api/reports/{rid}", json={**body, "cadence": "monthly", "day": 3}).status_code == 200
    assert ana.get("/api/reports").json()[0]["day"] == 3
    assert ana.delete(f"/api/reports/{rid}").status_code == 200 and ana.get("/api/reports").json() == []


def test_dashboard_report(sales):
    table, _ = sales
    ana, bo = login("rep-ana@acme.test"), login("rep-bo@acme.test")
    tid = ana.post("/api/dashboard/tiles", json={"title": "Sales by place", "type": "bar", "x": "location", "y": ["r"], "source": "uploads",
                                                  "sql": f'SELECT location, sum(revenue) AS r FROM "{table}" GROUP BY 1'}).json()["id"]
    assert bo.post("/api/reports", json={"title": "x", "tile_ids": [tid], "cadence": "daily"}).status_code == 422  # not on bo's dashboard
    rid = ana.post("/api/reports", json={"title": "Daily charts", "tile_ids": [tid], "cadence": "daily", "recipients": [bo.uid]}).json()["id"]
    assert reports.run_report(rid) == "ok"
    n = ana.get("/api/notifications").json()["items"][0]
    assert n["link"] == "/dashboard" and "Sales by place" in n["body"]
    assert not any(i["title"] == "Dashboard report: Daily charts" for i in bo.get("/api/notifications").json()["items"])  # tile isn't shared
    ana.delete(f"/api/reports/{rid}")
    ana.delete(f"/api/dashboard/tiles/{tid}")


# ---------- alerts ----------

def test_alert_drop_vs_last_year_notifies_once(sales):
    table, secret = sales
    owner, peer = login("alert-owner@acme.test", "admin"), login("alert-peer@acme.test")
    body = {"name": "Sales drop", "source": "uploads", "table_name": table, "expression": "sum(revenue)", "time_column": "day",
            "condition": "drop_vs_last_year", "threshold": 20, "group_by": "location", "recipients": [peer.uid]}
    assert owner.post("/api/alerts", json={**body, "group_by": "nope"}).status_code == 422
    assert owner.post("/api/alerts", json={**body, "time_column": None}).status_code == 422
    aid = owner.post("/api/alerts", json=body).json()["id"]
    today = date(2026, 11, 16)
    a = alerts._load(aid)
    res = alerts.evaluate(a, "alert-owner@acme.test", today)
    rows = {r["group"]: r for r in res["rows"]}
    assert res["triggered"] and rows["Riverside"]["change_pct"] == -60.0 and rows["Downtown"]["change_pct"] == 20.0
    assert "Riverside -60.0%" in res["message"] and "Downtown" not in res["message"]

    def alerts_for(c):
        return [i for i in c.get("/api/notifications").json()["items"] if i["kind"] == "alert"]

    assert alerts.check(aid, today) == "triggered"
    assert alerts.check(aid, today) == "triggered"  # still triggered: no second notification
    assert len(alerts_for(owner)) == 1 and len(alerts_for(peer)) == 1
    assert [e["state"] for e in owner.get(f"/api/alerts/{aid}/events").json()] == ["triggered"]
    assert owner.get("/api/alerts").json()[0]["last_triggered_at"]
    assert alerts.check(aid, date(2026, 10, 16)) == "ok"  # recovered: recorded, nobody notified
    assert len(alerts_for(owner)) == 1 and owner.get(f"/api/alerts/{aid}/events").json()[0]["state"] == "ok"
    assert peer.get(f"/api/alerts/{aid}/events").status_code == 404
    assert owner.post(f"/api/alerts/{aid}/test").status_code == 200
    assert owner.delete(f"/api/alerts/{aid}").status_code == 200


def test_alert_on_metric_skips_recipients_without_access(sales):
    _, secret = sales
    cfo, intern = login("ins-cfo@acme.test", "admin"), login("alert-intern@acme.test")
    mid = cfo.post("/api/metrics", json={"name": "ins Secret profit", "source": "uploads", "table_name": secret,
                                         "expression": "sum(profit)", "time_column": "day"}).json()["id"]
    assert intern.post("/api/alerts", json={"name": "x", "metric_id": mid, "condition": "above", "threshold": 1}).status_code == 422
    aid = cfo.post("/api/alerts", json={"name": "Profit high", "metric_id": mid, "condition": "above", "threshold": 1, "cadence": "hourly",
                                        "recipients": [intern.uid]}).json()["id"]
    assert cfo.get("/api/alerts").json()[0]["metric_name"] == "ins Secret profit"
    assert alerts.check(aid, date(2026, 11, 16)) == "triggered"
    assert any(i["title"] == "Alert: Profit high" for i in cfo.get("/api/notifications").json()["items"])
    assert not any(i["kind"] == "alert" for i in intern.get("/api/notifications").json()["items"])  # can't see the sheet
    with connect() as conn:  # the scheduler claims due alerts only
        conn.execute("UPDATE alerts SET last_checked_at = now() - interval '2 hours' WHERE id = %s", (aid,))
    alerts.check_due_alerts()
    with connect() as conn:
        assert conn.execute("SELECT last_checked_at > now() - interval '1 minute' FROM alerts WHERE id = %s", (aid,)).fetchone()[0]
    assert cfo.patch(f"/api/alerts/{aid}", json={"enabled": False}).status_code == 200
    cfo.delete(f"/api/alerts/{aid}")


# ---------- export ----------

def test_export_markdown_and_pdf():
    ana, bo = login("exp-ana@acme.test"), login("exp-bo@acme.test")
    chart = {"title": "Revenue by month", "type": "line", "x": "m", "y": ["r"], "columns": ["m", "r"], "rows": [["2026-01", 100], ["2026-02", 140]]}
    display = {"sources": [{"label": "S1", "kind": "doc", "document": "plan.pdf", "page": 2, "source": "upload"}], "charts": [chart], "steps": []}
    answer = "## Summary\n\nRevenue **grew** 40%.\n\n- January: 100\n- February: 140\n\n| Month | Revenue |\n|---|---|\n| Jan | 100 |"
    with connect() as conn:
        cid = conn.execute("INSERT INTO conversations (user_id, title) VALUES (%s, 'Q1 revenue') RETURNING id", (ana.uid,)).fetchone()[0]
        conn.execute("INSERT INTO runs (question, answer, user_id, conversation_id, display) VALUES ('How is revenue?', %s, %s, %s, %s)",
                     (answer, ana.uid, cid, json.dumps(display)))
    md = ana.get(f"/api/conversations/{cid}/export?format=md")
    assert md.status_code == 200 and "attachment" in md.headers["content-disposition"]
    text = md.text
    assert text.startswith("# Q1 revenue") and "## How is revenue?" in text and "| m | r |\n| --- | --- |\n| 2026-01 | 100 |" in text
    assert "- [S1] plan.pdf (page 2)" in text

    pdf = ana.get(f"/api/conversations/{cid}/export?format=pdf")
    assert pdf.headers["content-type"] == "application/pdf" and pdf.content.startswith(b"%PDF")
    body = "".join(p.get_text() for p in pymupdf.open(stream=pdf.content, filetype="pdf"))
    for want in ("Q1 revenue", "How is revenue?", "Summary", "grew", "February: 140", "Revenue by month", "plan.pdf"):
        assert want in body
    assert "**" not in body and "|---" not in body
    assert bo.get(f"/api/conversations/{cid}/export?format=pdf").status_code == 404
    assert ana.get(f"/api/conversations/{cid}/export?format=docx").status_code == 422


def test_md_html():
    h = export.md_html("# T\n\n1. a\n2. b\n\ntext <script>\n\n| a | b |\n|---|---|\n| 1 | 2 |")
    assert "<h1>T</h1>" in h and "<ol>\n<li>a</li>\n<li>b</li>\n</ol>" in h and "&lt;script&gt;" in h
    assert "<table><tr><th>a</th><th>b</th></tr><tr><td>1</td><td>2</td></tr></table>" in h
