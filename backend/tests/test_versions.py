"""Replacing files in place: identity kept, changes described, assistant told quietly."""
import io
import json

import openpyxl
from fastapi.testclient import TestClient

from app import tools
from app.db import connect
from app.main import app
from app.security import hash_password
from app.versions import describe_table_change, describe_text_change

CSRF = {"x-requested-with": "agentic-rag"}


def login(email):
    with connect() as conn:
        conn.execute("INSERT INTO users (email, password_hash, role) VALUES (%s, %s, 'member') "
                     "ON CONFLICT (email) DO UPDATE SET password_hash = EXCLUDED.password_hash", (email, hash_password("pw-1234567890")))
    c = TestClient(app, headers=CSRF)
    assert c.post("/api/auth/login", json={"email": email, "password": "pw-1234567890"}).status_code == 200
    return c


def workbook(sheets):
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for name, rows in sheets.items():
        ws = wb.create_sheet(name)
        for r in rows:
            ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_text_change_description():
    note = describe_text_change("Title\nFull-time staff get 10 days of PTO.\nTips are pooled.",
                                "Title\nFull-time staff get 15 days of PTO.\nTips are pooled.\nParental leave: 6 weeks.")
    assert note.startswith("1 line changed, 1 added")
    assert 'Changed: "Full-time staff get 10 days of PTO." → "Full-time staff get 15 days of PTO."' in note
    assert 'Added: "Parental leave: 6 weeks."' in note
    assert describe_text_change("same", "same").startswith("No text changes")


def test_table_change_description():
    old = {"rows": 10, "columns": ["d", "rev"], "sums": {"rev": 100.0}, "dates": {"d": ("2026-01-01", "2026-10-31")}}
    new = {"rows": 12, "columns": ["d", "rev", "margin"], "sums": {"rev": 130.0, "margin": 5.0}, "dates": {"d": ("2026-01-01", "2026-12-31")}}
    note = describe_table_change(old, new)
    assert "Rows: 10 → 12 (+2)" in note and "Columns added: margin" in note
    assert "rev total: 100 → 130 (+30.0%)" in note and "2026-10-31 → 2026-01-01 to 2026-12-31" in note


def test_replace_document_keeps_identity_and_tells_the_assistant():
    ana, bo = login("ver-ana@acme.test"), login("ver-bo@acme.test")
    v1 = b"# Handbook\n\nFull-time employees receive 10 days of paid time off.\n"
    v2 = b"# Handbook\n\nFull-time employees receive 15 days of paid time off.\n\nParental leave is 6 weeks.\n"
    doc_id = ana.post("/api/upload", files={"file": ("ver-handbook.md", v1)}, data={"visibility": "private"}).json()["id"]

    assert bo.post(f"/api/documents/{doc_id}/replace", files={"file": ("x.md", v2)}).status_code in (403, 404)
    assert ana.post(f"/api/documents/{doc_id}/replace", files={"file": ("x.xlsx", b"nope")}).status_code == 415
    r = ana.post(f"/api/documents/{doc_id}/replace", files={"file": ("ver-handbook-2026.md", v2)})
    assert r.status_code == 200 and r.json()["id"] == doc_id  # same document, new content
    note = r.json()["change_note"]
    assert "Renamed from ver-handbook.md" in note and "10 days" in note and "15 days" in note

    p = ana.get(f"/api/documents/{doc_id}/preview").json()
    assert "15 days" in p["text"] and p["versions"][0]["by"] == "ver-ana@acme.test"
    assert ana.get(f"/api/documents/{doc_id}/file").content == v2  # stored original updated
    assert bo.get(f"/api/documents/{doc_id}/preview").status_code == 404  # still private after replacing

    out = tools.execute("search_documents", {"query": "paid time off days"}, tools.Ctx(email="ver-ana@acme.test"))
    assert "15 days" in out and "10 days of paid" not in out.split("<recent_change")[0]  # old text is gone from search
    assert '<recent_change document="ver-handbook-2026.md"' in out and "→" in out  # assistant gets what changed
    ana.delete(f"/api/documents/{doc_id}")


def test_replace_workbook_keeps_tables_and_charts():
    ana = login("ver-ana@acme.test")
    v1 = workbook({"Sales": [["month", "revenue"], ["2026-01", 100], ["2026-02", 120]], "Notes": [["note"], ["draft"]]})
    v2 = workbook({"Sales": [["month", "revenue", "margin"], ["2026-01", 100, 0.3], ["2026-02", 120, 0.31], ["2026-03", 150, 0.33]]})
    tabs = ana.post("/api/upload", files={"file": ("ver-q1.xlsx", v1)}).json()["tables"]
    sales = next(t["table"] for t in tabs if t["title"].endswith("Sales"))
    notes = next(t["table"] for t in tabs if t["title"].endswith("Notes"))
    tile = ana.post("/api/dashboard/tiles", json={"title": "Revenue", "type": "line", "x": "month", "y": ["revenue"],
                                                  "source": "uploads", "sql": f"SELECT month, revenue FROM {sales} ORDER BY 1"}).json()["id"]

    r = ana.post(f"/api/tables/{sales}/replace", files={"file": ("ver-q1-final.xlsx", v2)})
    assert r.status_code == 200
    note = r.json()["change_note"]
    assert "Rows: 2 → 3 (+1)" in note and "Columns added: margin" in note and "revenue total: 220 → 370" in note
    assert "Sheet removed: ver-q1.xlsx › Notes" in note
    with connect() as conn:
        assert not conn.execute("SELECT 1 FROM data_tables WHERE name = %s", (notes,)).fetchone()  # removed tab dropped

    # same table name, so the pinned chart keeps working and shows the new month
    assert len(ana.get(f"/api/dashboard/tiles/{tile}/data?refresh=true").json()["rows"]) == 3

    ctx = tools.Ctx(email="ver-ana@acme.test")
    listed = json.loads(tools.execute("list_data_sources", {}, ctx))[0]["tables"]
    entry = next(t for t in listed if t["name"] == sales)
    assert "Rows: 2 → 3" in entry["recently_replaced"]["changes"]
    assert "recent_change" in json.loads(tools.execute("analyze_spreadsheet", {"table": sales}, ctx))
    ana.delete(f"/api/dashboard/tiles/{tile}")
    ana.delete(f"/api/tables/{sales}")
