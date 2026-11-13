"""Spreadsheets: Excel parsing, types, and table-level access enforced by Postgres roles."""
import datetime
import io

import openpyxl
import psycopg
import pytest

from app import datasources
from app.db import connect
from app.ingest import drop_tables, ingest_spreadsheet


def xlsx(sheets: dict[str, list[list]]) -> bytes:
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for name, rows in sheets.items():
        ws = wb.create_sheet(name)
        for r in rows:
            ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


@pytest.fixture
def users():
    with connect() as conn:
        ids = {e: conn.execute("INSERT INTO users (email, password_hash, role) VALUES (%s, 'x', 'member') "
                               "ON CONFLICT (email) DO UPDATE SET role = 'member' RETURNING id", (e,)).fetchone()[0]
               for e in ("sheet-ana@acme.test", "sheet-bo@acme.test")}
    yield ids
    drop_tables("title LIKE %s", ("sheet-%",))


def q(email, sql):
    return datasources.run("uploads", sql, email)


def test_excel_every_tab_with_types(users):
    data = xlsx({
        "Sales": [["Month", "Region", "Revenue ($)", "Zip"], [datetime.datetime(2026, 1, 1), "North", 1200.5, "02134"],
                  [datetime.datetime(2026, 2, 1), "South", 980, "10001"], [None, None, None, None]],
        "Notes": [["only a header"]],  # header-only tab is skipped
        "Targets": [["Quarter", "Target"], ["Q1", 5000], ["Q2", 6000]],
    })
    out = ingest_spreadsheet("sheet-forecast.xlsx", data, user_id=users["sheet-ana@acme.test"])
    by_title = {t["title"]: t for t in out}
    assert set(by_title) == {"sheet-forecast.xlsx › Sales", "sheet-forecast.xlsx › Targets"}
    sales = by_title["sheet-forecast.xlsx › Sales"]
    assert sales["table"] == "sheet_forecast_sales" and by_title["sheet-forecast.xlsx › Targets"]["table"] == "sheet_forecast_targets"
    assert sales["columns"] == {"month": "date", "region": "text", "revenue": "double precision", "zip": "text"}
    assert sales["rows"] == 2  # trailing empty row dropped
    r = q("sheet-bo@acme.test", f"SELECT date_trunc('month', month)::date AS m, sum(revenue) AS rev FROM {sales['table']} GROUP BY 1 ORDER BY 1")
    assert r["rows"][0][0] == datetime.date(2026, 1, 1) and float(r["rows"][0][1]) == 1200.5


def test_private_sheet_is_enforced_by_the_database(users):
    ana, bo = "sheet-ana@acme.test", "sheet-bo@acme.test"
    t = ingest_spreadsheet("sheet-salaries.csv", b"name,salary\nAna,150000\nBo,120000\n", user_id=users[ana], allowed=[ana])[0]["table"]
    assert q(ana, f"SELECT sum(salary) AS s FROM {t}")["rows"] == [[270000]]
    assert t not in datasources.list_tables("uploads", bo)
    for sneaky in (f"SELECT * FROM {t}", f"SELECT * FROM data.{t}",
                   f"SELECT query_to_xml('select * from data.{t}', true, true, '')",  # runs SQL inside a function
                   f"SELECT set_config('role', 'rag', false)"):
        with pytest.raises(psycopg.Error):
            out = q(bo, sneaky)
            if "query_to_xml" in sneaky or "set_config" in sneaky:  # must fail, not return data
                raise AssertionError(out)
    with pytest.raises(psycopg.Error):
        q(None, f"SELECT * FROM {t}")  # no identity → public tables only


def test_connection_access_applies_to_synced_sheets(users):
    with connect() as conn:
        cid = conn.execute("INSERT INTO connections (kind, name, allowed) VALUES ('gdrive', 'sheet-drive', %s) RETURNING id",
                           (["sheet-ana@acme.test"],)).fetchone()[0]
    try:
        t = ingest_spreadsheet("sheet-pipeline.xlsx", xlsx({"Deals": [["deal", "amount"], ["Acme", 10]]}),
                               source="gdrive", connection_id=cid, identity="drive-file-1")[0]["table"]
        assert q("sheet-ana@acme.test", f"SELECT amount FROM {t}")["rows"] == [[10]]
        with pytest.raises(psycopg.Error):
            q("sheet-bo@acme.test", f"SELECT amount FROM {t}")
        # re-sync of the same Drive file replaces the table instead of creating a second one
        t2 = ingest_spreadsheet("sheet-pipeline.xlsx", xlsx({"Deals": [["deal", "amount"], ["Acme", 99]]}),
                                source="gdrive", connection_id=cid, identity="drive-file-1")[0]["table"]
        assert t2 == t and q("sheet-ana@acme.test", f"SELECT amount FROM {t}")["rows"] == [[99]]
    finally:
        drop_tables("connection_id = %s", (cid,))
        with connect() as conn:
            conn.execute("DELETE FROM connections WHERE id = %s", (cid,))


def test_same_filename_two_users_two_tables(users):
    a = ingest_spreadsheet("sheet-budget.csv", b"item,cost\nrent,1\n", user_id=users["sheet-ana@acme.test"])[0]["table"]
    b = ingest_spreadsheet("sheet-budget.csv", b"item,cost\nrent,2\n", user_id=users["sheet-bo@acme.test"])[0]["table"]
    assert a != b
    assert q("sheet-ana@acme.test", f"SELECT cost FROM {a}")["rows"] == [[1]]
    assert q("sheet-ana@acme.test", f"SELECT cost FROM {b}")["rows"] == [[2]]  # both public, both intact
    again = ingest_spreadsheet("sheet-budget.csv", b"item,cost\nrent,3\n", user_id=users["sheet-ana@acme.test"])[0]["table"]
    assert again == a  # same uploader re-uploading replaces their own


def test_empty_sheet_rejected():
    with pytest.raises(ValueError, match="No data"):
        ingest_spreadsheet("sheet-empty.csv", b"just,a,header\n")
