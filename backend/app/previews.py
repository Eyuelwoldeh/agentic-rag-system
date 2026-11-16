"""Previews and downloads for Library items. Same access rules as search and queries."""
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from psycopg import sql

from . import datasources, tableacl
from .db import connect
from .security import User, current_user
from .tools import _jsonable

router = APIRouter()
PREVIEW_ROWS = 200
MIME = {".pdf": "application/pdf", ".csv": "text/csv", ".txt": "text/plain", ".md": "text/plain",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", ".xlsm": "application/vnd.ms-excel.sheet.macroEnabled.12"}


def mime_for(filename: str) -> str:
    return next((m for ext, m in MIME.items() if filename.lower().endswith(ext)), "application/octet-stream")


def save_original(key: str, filename: str, data: bytes, user_id: int) -> None:
    with connect() as conn:
        conn.execute("INSERT INTO upload_files (key, filename, mime, size, data, uploaded_by) VALUES (%s, %s, %s, %s, %s, %s) "
                     "ON CONFLICT (key) DO UPDATE SET data = EXCLUDED.data, size = EXCLUDED.size, mime = EXCLUDED.mime, created_at = now()",
                     (key, filename, mime_for(filename), len(data), data, user_id))


def delete_original(key: str) -> None:
    """Remove a stored original once nothing (document or any sheet of the file) still uses it."""
    with connect() as conn:
        conn.execute("DELETE FROM upload_files f WHERE key = %s AND NOT EXISTS (SELECT 1 FROM documents WHERE external_id = f.key) "
                     "AND NOT EXISTS (SELECT 1 FROM data_tables WHERE external_id LIKE f.key || '::%%')", (key,))


def _visible_document(doc_id: int, user: User):
    with connect() as conn:
        row = conn.execute(
            "SELECT d.id, d.name, d.source, d.url, d.content, d.external_id, d.updated_at, d.created_at, d.replaced_at, d.change_note FROM documents d "
            "LEFT JOIN connections c ON c.id = d.connection_id WHERE d.id = %(id)s "
            "AND (d.allowed IS NULL OR %(e)s = ANY(d.allowed)) AND (c.allowed IS NULL OR %(e)s = ANY(c.allowed))",
            {"id": doc_id, "e": user.email}).fetchone()
    if not row:
        raise HTTPException(404, "Document not found")  # same answer whether it doesn't exist or you can't see it
    return row


def _file_response(key: str | None) -> Response:
    with connect() as conn:
        row = conn.execute("SELECT filename, mime, data FROM upload_files WHERE key = %s", (key,)).fetchone() if key else None
    if not row:
        raise HTTPException(404, "The original file isn't stored (it was added before previews existed). Re-upload it to enable this.")
    filename, mime, data = row
    return Response(bytes(data), media_type=mime, headers={
        "Content-Disposition": f"inline; filename*=UTF-8''{quote(filename)}",
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "sandbox",  # user files never run scripts on our origin
        "Cache-Control": "private, no-store",
    })


@router.get("/api/documents/{doc_id}/preview")
def document_preview(doc_id: int, user: User = Depends(current_user)):
    did, name, source, url, content, ext, updated, created, replaced_at, change_note = _visible_document(doc_id, user)
    if not content:  # documents indexed before full text was kept: rebuild from passages
        with connect() as conn:
            parts = [r[0] for r in conn.execute("SELECT content FROM chunks WHERE document_id = %s ORDER BY page, id", (did,)).fetchall()]
        content = "\n\n…\n\n".join(parts)
    with connect() as conn:
        has_file = bool(conn.execute("SELECT 1 FROM upload_files WHERE key = %s", (ext,)).fetchone()) if source == "upload" else False
    kind = "pdf" if name.lower().endswith(".pdf") else "markdown" if name.lower().endswith(".md") else "text"
    from .versions import history
    return {"id": did, "name": name, "source": source, "url": url, "kind": kind, "text": content, "has_file": has_file,
            "updated_at": replaced_at or updated or created, "versions": history(ext) if source == "upload" else []}


@router.get("/api/documents/{doc_id}/file")
def document_file(doc_id: int, user: User = Depends(current_user)):
    row = _visible_document(doc_id, user)
    return _file_response(row[5] if row[2] == "upload" else None)


def _visible_table(name: str, user: User) -> dict:
    t = tableacl.visible_tables(user.email).get(name)
    if not t:
        raise HTTPException(404, "Spreadsheet not found")
    return t


@router.get("/api/tables/{name}/preview")
def table_preview(name: str, user: User = Depends(current_user)):
    from .versions import history  # late import: versions imports this module
    meta = _visible_table(name, user)
    types = datasources.list_tables(datasources.UPLOADS, user.email).get(name, {})
    query = sql.SQL("SELECT * FROM {} LIMIT {}").format(sql.Identifier(name), sql.Literal(PREVIEW_ROWS)).as_string(None)
    res = datasources.run(datasources.UPLOADS, query, user.email, max_rows=PREVIEW_ROWS)  # runs as the user's DB role
    with connect() as conn:
        total, ext, change_note, replaced_at = conn.execute(
            "SELECT row_count, external_id, change_note, replaced_at FROM data_tables WHERE name = %s", (name,)).fetchone()
        key = ext.split("::")[0] if ext and meta["source"] == "upload" else None
        has_file = bool(key and conn.execute("SELECT 1 FROM upload_files WHERE key = %s", (key,)).fetchone())
    return {"name": name, "title": meta["title"], "source": meta["source"], "columns": res["columns"], "types": types,
            "rows": [[_jsonable(v) for v in r] for r in res["rows"]], "total_rows": total, "shown": len(res["rows"]), "has_file": has_file,
            "change_note": change_note, "replaced_at": replaced_at, "versions": history(key) if key else []}


@router.get("/api/tables/{name}/file")
def table_file(name: str, user: User = Depends(current_user)):
    meta = _visible_table(name, user)
    with connect() as conn:
        ext = conn.execute("SELECT external_id FROM data_tables WHERE name = %s", (name,)).fetchone()[0]
    return _file_response(ext.split("::")[0] if ext and meta["source"] == "upload" else None)
