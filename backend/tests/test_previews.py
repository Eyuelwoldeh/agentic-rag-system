"""Library previews and downloads: content, access rules, safe headers, cleanup."""
import io

import openpyxl
from fastapi.testclient import TestClient

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


def xlsx_bytes():
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Q3"
    ws.append(["region", "revenue"])
    ws.append(["North", 120])
    ws.append(["South", 80])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_document_preview_download_and_privacy():
    ana, bo = login("prev-ana@acme.test"), login("prev-bo@acme.test")
    md = b"# Refund policy\n\nRefunds within **30 days**.\n"
    r = ana.post("/api/upload", files={"file": ("prev-policy.md", md)}, data={"visibility": "private"})
    doc_id = r.json()["id"]
    p = ana.get(f"/api/documents/{doc_id}/preview").json()
    assert p["kind"] == "markdown" and "30 days" in p["text"] and p["has_file"]
    f = ana.get(f"/api/documents/{doc_id}/file")
    assert f.content == md and f.headers["content-security-policy"] == "sandbox" and f.headers["x-content-type-options"] == "nosniff"
    assert f.headers["content-type"].startswith("text/plain")  # markdown is never served as HTML
    for path in (f"/api/documents/{doc_id}/preview", f"/api/documents/{doc_id}/file"):
        assert bo.get(path).status_code == 404  # private: indistinguishable from not existing
    assert ana.delete(f"/api/documents/{doc_id}").status_code == 200
    with connect() as conn:
        assert not conn.execute("SELECT 1 FROM upload_files WHERE filename = 'prev-policy.md'").fetchone()


def test_pdf_preview_keeps_pages():
    import pymupdf
    doc = pymupdf.open()
    for t in ("First page text", "Second page text"):
        doc.new_page().insert_text((72, 72), t)
    ana = login("prev-ana@acme.test")
    doc_id = ana.post("/api/upload", files={"file": ("prev-report.pdf", doc.tobytes())}).json()["id"]
    p = ana.get(f"/api/documents/{doc_id}/preview").json()
    assert p["kind"] == "pdf" and "[Page 2]" in p["text"] and "Second page text" in p["text"]
    assert ana.get(f"/api/documents/{doc_id}/file").headers["content-type"] == "application/pdf"
    ana.delete(f"/api/documents/{doc_id}")


def test_spreadsheet_preview_and_original():
    ana, bo = login("prev-ana@acme.test"), login("prev-bo@acme.test")
    data = xlsx_bytes()
    t = ana.post("/api/upload", files={"file": ("prev-sales.xlsx", data)}, data={"visibility": "private"}).json()["tables"][0]["table"]
    p = ana.get(f"/api/tables/{t}/preview").json()
    assert p["columns"] == ["region", "revenue"] and p["rows"] == [["North", 120], ["South", 80]]
    assert p["types"]["revenue"] == "bigint" and p["total_rows"] == 2 and p["has_file"]
    assert ana.get(f"/api/tables/{t}/file").content == data
    assert bo.get(f"/api/tables/{t}/preview").status_code == 404
    assert bo.get(f"/api/tables/{t}/file").status_code == 404
    assert ana.delete(f"/api/tables/{t}").status_code == 200
    with connect() as conn:
        assert not conn.execute("SELECT 1 FROM upload_files WHERE filename = 'prev-sales.xlsx'").fetchone()


def test_search_results_carry_document_id():
    from app.ingest import ingest_document
    from app.tools import search_documents
    d = ingest_document("prev-zz.md", b"The quokka protocol requires two badges.")
    hit = search_documents("quokka protocol badges", k=1)[0]
    assert hit["document_id"] == d["id"]
    with connect() as conn:
        conn.execute("DELETE FROM documents WHERE id = %s", (d["id"],))
