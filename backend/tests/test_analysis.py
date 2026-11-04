"""Computed spreadsheet insights."""
import pytest

from app import analysis, datasources
from app.db import connect
from app.ingest import drop_tables, ingest_spreadsheet


@pytest.fixture(autouse=True)
def cleanup():
    yield
    drop_tables("title LIKE %s", ("an-%",))


def sheet(name, rows, **kw):
    return ingest_spreadsheet(name, "\n".join(rows).encode(), **kw)[0]["table"]


def headlines(a):
    return " | ".join(f["headline"] for f in a["findings"])


def test_finds_trend_leader_growth_and_hidden_dip():
    rows = ["order_id,date,region,revenue,orders"]
    i = 0
    for m in range(1, 11):
        for region, base, g in (("North", 100000, 0.06), ("South", 60000, 0.01), ("West", 40000, 0.12)):
            i += 1
            rev = base * (1 + g) ** m * (0.85 if (m == 4 and region == "North") else 1)
            rows.append(f"{i},2026-{m:02d}-15,{region},{rev:.0f},{int(rev / 450)}")
    a = analysis.analyze(sheet("an-sales.csv", rows), None)
    h = headlines(a)
    assert a["metric"] == "revenue" and a["time"] == "date" and a["segment"] == "region"  # order_id ignored as an ID
    assert "Revenue went from" in h and "between 2026-01 and 2026-10" in h
    assert "North leads region" in h
    assert "Fastest growing region: West" in h
    assert "North fell 9.9% in 2026-04" in h  # dip hidden in the total is surfaced
    assert "Revenue and orders move together" in h
    assert [c["type"] for c in a["charts"]] == ["number", "line", "bar"]
    assert all(c["sql"].startswith("SELECT") and c["source"] == "uploads" for c in a["charts"])  # pinnable


def test_year_month_text_and_missing_values():
    rows = ["month,spend,notes"] + [f"2026-{m:02d},{100 + m * 5},{'x' if m % 2 else ''}" for m in range(1, 9)]
    a = analysis.analyze(sheet("an-spend.csv", rows), None)
    h = headlines(a)
    assert a["time"] == "month" and "Spend went from 105 to 140" in h
    assert any(f["kind"] == "quality" and "notes (50% empty)" in f["detail"] for f in a["findings"])
    assert any(c["type"] == "line" for c in a["charts"])


def test_outliers():
    rows = ["day,amount"] + [f"2026-01-{d:02d},{100 + (d % 5)}" for d in range(1, 29)] + ["2026-01-29,5000"]
    a = analysis.analyze(sheet("an-outlier.csv", rows), None)
    assert any(f["kind"] == "anomaly" and "unusual amount" in f["headline"] for f in a["findings"])


def test_respects_access():
    with connect() as conn:
        uid = conn.execute("INSERT INTO users (email, password_hash, role) VALUES ('an-owner@acme.test', 'x', 'member') "
                           "ON CONFLICT (email) DO UPDATE SET role = 'member' RETURNING id").fetchone()[0]
    t = sheet("an-private.csv", ["k,v", "a,1", "b,2"], user_id=uid, allowed=["an-owner@acme.test"])
    assert analysis.analyze(t, "an-owner@acme.test")["rows"] == 2
    with pytest.raises(datasources.AccessDenied):
        analysis.analyze(t, "someone-else@acme.test")


def test_picks_meaningful_breakdown_and_skips_fake_gaps():
    rows = ["date,product,region,revenue"]
    for m in range(1, 9):
        for region, g in (("North", 0.05), ("Europe", 0.02), ("Asia", 0.10)):
            for product in ("Pro", "Starter"):  # products grow identically: no product "gap" to report
                rev = 1000 * (1 + g) ** m * (0.8 if (m == 6 and region == "Europe") else 1)
                rows.append(f"2026-{m:02d}-01,{product},{region},{rev:.0f}")
    a = analysis.analyze(sheet("an-mix.csv", rows), None)
    h = headlines(a)
    assert a["segment"] == "region"  # 3 regions beats 2 products
    assert "Europe fell" in h and "in 2026-06" in h
    assert "Fastest growing region: Asia" in h
    assert "Fastest growing product" not in h  # Pro and Starter grew the same: not an insight
    assert [c["title"] for c in a["charts"]] == ["Total revenue", "Revenue over time", "Revenue by region"]
