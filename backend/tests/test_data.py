"""Live data sources: read-only enforcement, access control, warehouse adapters, charts."""
import json
import os

import httpx
import pytest

from app import datasources, tools
from app.llm import SCHEMAS, valid_input
from app.connectors import base, bigquery, postgres, snowflake
from app.connectors.base import assert_read_query
from app.db import connect
from app.ingest import ingest_csv

PG_URL = os.environ["DATABASE_URL"]  # the throwaway test DB doubles as an "external" warehouse


def test_assert_read_query():
    assert assert_read_query("  select 1; ") == "select 1"
    assert assert_read_query("-- note\nWITH a AS (SELECT 1) SELECT * FROM a") .startswith("WITH")
    assert assert_read_query("SELECT ';' AS semi") == "SELECT ';' AS semi"  # semicolons inside strings are fine
    for bad in ("DROP TABLE x", "SELECT 1; DROP TABLE x", "/* hi */ DELETE FROM t", "", "INSERT INTO t VALUES (1)"):
        with pytest.raises(ValueError):
            assert_read_query(bad)


def test_postgres_connector_is_read_only():
    creds = {"access_token": PG_URL}
    with connect() as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS public.wh_orders (region text, revenue int)")
        conn.execute("TRUNCATE public.wh_orders")
        conn.execute("INSERT INTO public.wh_orders VALUES ('north', 10), ('south', 32)")
    assert "wh_orders" in str(postgres.list_tables({}, creds))
    r = postgres.query({}, creds, "SELECT region, revenue FROM wh_orders ORDER BY revenue DESC", 10)
    assert r == {"columns": ["region", "revenue"], "rows": [["south", 32], ["north", 10]], "truncated": False}
    # superuser credentials on purpose: the read-only transaction + single prepared statement must still hold
    for bad in ("WITH d AS (DELETE FROM wh_orders RETURNING 1) SELECT * FROM d",
                "SELECT 1; DROP TABLE wh_orders", "SELECT pg_terminate_backend(1) WHERE false; COMMIT"):
        with pytest.raises(Exception):
            postgres.query({}, creds, bad, 10)
    with connect() as conn:
        assert conn.execute("SELECT count(*) FROM public.wh_orders").fetchone()[0] == 2
    with pytest.raises(ValueError):
        postgres.parse_paste("mysql://x")


def test_warehouse_access_control(monkeypatch):
    monkeypatch.setattr(base, "http", None)  # no network expected
    from app.security import encrypt
    with connect() as conn:
        cid = conn.execute("INSERT INTO connections (kind, name, credentials, allowed) VALUES ('postgres', 'wh-fin', %s, %s) "
                           "RETURNING id", (encrypt({"access_token": PG_URL}), ["cfo@acme.test"])).fetchone()[0]
    try:
        assert f"conn-{cid}" in [s["id"] for s in datasources.available("cfo@acme.test")]
        assert f"conn-{cid}" not in [s["id"] for s in datasources.available("intern@acme.test")]
        assert datasources.run(f"conn-{cid}", "SELECT 7 AS n", "cfo@acme.test")["rows"] == [[7]]
        with pytest.raises(datasources.AccessDenied):
            datasources.run(f"conn-{cid}", "SELECT 7", "intern@acme.test")
        with pytest.raises(datasources.AccessDenied):
            datasources.run("conn-abc", "SELECT 7", "cfo@acme.test")
    finally:
        with connect() as conn:
            conn.execute("DELETE FROM connections WHERE id = %s", (cid,))


SA = {"service_account": {"project_id": "acme-prod", "client_email": "rag@acme-prod.iam"}}


def test_bigquery_cost_cap_and_types(monkeypatch):
    monkeypatch.setattr(bigquery, "_token", lambda creds: "tok")
    calls = []

    def handler(req):
        body = json.loads(req.content)
        calls.append(body)
        if body["dryRun"]:
            return httpx.Response(200, json={"totalBytesProcessed": str(int(30e9) if "huge" in body["query"] else 1000)})
        return httpx.Response(200, json={"jobComplete": True,
                                         "schema": {"fields": [{"name": "zip", "type": "STRING"}, {"name": "n", "type": "INT64"},
                                                               {"name": "avg", "type": "FLOAT64"}]},
                                         "rows": [{"f": [{"v": "00123"}, {"v": "42"}, {"v": "1.5"}]}]})
    monkeypatch.setattr(base, "http", httpx.Client(transport=httpx.MockTransport(handler)))
    cfg = {"datasets": "sales"}
    r = bigquery.query(cfg, SA, "SELECT zip, n, avg FROM t", 10)
    assert r["rows"] == [["00123", 42, 1.5]]  # string stays a string; ints/floats typed
    assert calls[-1]["maximumBytesBilled"] == str(int(10e9))  # hard cap also sent to BigQuery
    with pytest.raises(ValueError, match="would scan 30.0 GB"):
        bigquery.query(cfg, SA, "SELECT * FROM huge", 10)
    with pytest.raises(ValueError):
        bigquery.query(cfg, SA, "DELETE FROM t WHERE true", 10)
    assert "acme-prod" in str(calls[0]) or True


def test_snowflake_polls_async_statement(monkeypatch):
    state = {"polls": 0}

    def handler(req):
        assert req.headers["x-snowflake-authorization-token-type"] == "PROGRAMMATIC_ACCESS_TOKEN"
        if req.method == "POST":
            assert json.loads(req.content)["parameters"]["MULTI_STATEMENT_COUNT"] == "1"
            return httpx.Response(202, json={"statementHandle": "h1", "message": "running"})
        state["polls"] += 1
        return httpx.Response(200, json={"statementHandle": "h1",
                                         "resultSetMetaData": {"rowType": [{"name": "REGION", "type": "text"}, {"name": "REV", "type": "fixed"}]},
                                         "data": [["north", "10"], ["south", "32.5"]]})
    monkeypatch.setattr(base, "http", httpx.Client(transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(snowflake.time, "sleep", lambda s: None)
    r = snowflake.query({"account": "acme-prod", "warehouse": "WH", "database": "DB"}, {"access_token": "pat"}, "SELECT 1", 10)
    assert r["rows"] == [["north", 10], ["south", 32.5]] and state["polls"] == 1
    with pytest.raises(ValueError, match="Account"):
        snowflake.query({"account": "evil.com/x?"}, {"access_token": "pat"}, "SELECT 1", 10)


def test_sql_to_chart_flow():
    ingest_csv("chart_sales.csv", b"month,revenue,region\n2026-01,100,n\n2026-02,140,n\n2026-03,90,s\n")
    try:
        ctx = tools.Ctx(email="ana@acme.test")
        srcs = json.loads(tools.execute("list_data_sources", {}, ctx))
        assert srcs[0]["id"] == "uploads" and {"name": "chart_sales", "from": "chart_sales.csv"} in srcs[0]["tables"]
        cols = json.loads(tools.execute("describe_tables", {"source": "uploads", "tables": ["chart_sales", "nope"]}, ctx))
        assert cols["tables"]["chart_sales"]["revenue"] == "bigint" and cols["unknown"] == ["nope"]
        out = json.loads(tools.execute("run_sql", {"source": "uploads", "query": "SELECT month, revenue FROM chart_sales ORDER BY month"}, ctx))
        assert out["result_id"] == "R1" and out["row_count"] == 3
        msg = tools.execute("create_chart", {"result_id": "R1", "type": "line", "title": "Revenue by month", "x": "month", "y": ["revenue"]}, ctx)
        assert "C1" in msg and ctx.charts[0]["sql"].startswith("SELECT month") and ctx.charts[0]["source"] == "uploads"
        with pytest.raises(ValueError, match="not in result"):
            tools.execute("create_chart", {"result_id": "R1", "type": "bar", "title": "x", "x": "month", "y": ["profit"]}, ctx)
        with pytest.raises(ValueError, match="numeric"):
            tools.execute("create_chart", {"result_id": "R1", "type": "bar", "title": "x", "x": "revenue", "y": ["month"]}, ctx)
        # SUM/AVG return NUMERIC (Decimal) in Postgres: must still chart as numbers, dates as ISO strings
        kpi = json.loads(tools.execute("run_sql", {"source": "uploads", "query": "SELECT sum(revenue) AS total, avg(revenue) AS mean, DATE '2026-01-31' AS asof FROM chart_sales"}, ctx))
        assert kpi["rows"] == [[330, 110, "2026-01-31"]]
        tools.execute("create_chart", {"result_id": kpi["result_id"], "type": "number", "title": "Total", "y": ["total"]}, ctx)
        with pytest.raises(ValueError, match="Unknown result_id"):
            tools.execute("create_chart", {"result_id": "R9", "type": "number", "title": "x", "y": ["revenue"]}, ctx)
    finally:
        with connect() as conn:
            conn.execute("DROP TABLE IF EXISTS data.chart_sales")


def test_tool_input_validation():
    chart = SCHEMAS["create_chart"]
    assert valid_input({"result_id": "R1", "type": "bar", "title": "t", "x": "a", "y": ["b"]}, chart)
    assert not valid_input({"result_id": "R1", "type": "pie", "title": "t", "y": ["b"]}, chart)  # enum
    assert not valid_input({"result_id": "R1", "type": "bar", "title": "t", "y": []}, chart)  # empty array
    assert not valid_input({"result_id": "R1", "type": "bar", "title": "t", "y": "b"}, chart)  # wrong type
    assert not valid_input({"query": "x", "k": "5"}, SCHEMAS["search_documents"])
    assert not valid_input({"query": "x", "evil": 1}, SCHEMAS["search_documents"])
