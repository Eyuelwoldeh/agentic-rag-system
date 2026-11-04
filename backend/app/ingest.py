"""Pre-processing & indexing: document -> chunks -> embeddings -> pgvector. CSV -> table in schema `data`."""
import csv
import datetime
import io
import re
from functools import lru_cache

import pymupdf
from psycopg import sql

from .db import connect

CHUNK_CHARS = 1200
OVERLAP = 200


@lru_cache(maxsize=1)
def _embedder():
    from fastembed import TextEmbedding  # lazy: loads an ONNX model (~130MB, downloaded once)

    return TextEmbedding("BAAI/bge-small-en-v1.5")


def embed(texts: list[str]) -> list:
    return list(_embedder().embed(texts))


def chunk(text: str, size: int = CHUNK_CHARS, overlap: int = OVERLAP) -> list[str]:
    """Split on paragraph boundaries, packing paragraphs up to `size` chars with `overlap` carry-over."""
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks, cur = [], ""
    for p in paras:
        while len(p) > size:  # a single huge paragraph: hard split
            p_head, p = p[:size], p[size - overlap :]
            if cur:
                chunks.append(cur)
                cur = ""
            chunks.append(p_head)
        if len(cur) + len(p) + 2 > size and cur:
            chunks.append(cur)
            cur = cur[-overlap:] + "\n\n" + p
        else:
            cur = f"{cur}\n\n{p}" if cur else p
    if cur:
        chunks.append(cur)
    return chunks


def extract_pages(filename: str, data: bytes) -> list[tuple[int, str]]:
    if filename.lower().endswith(".pdf"):
        with pymupdf.open(stream=data, filetype="pdf") as doc:
            return [(i + 1, page.get_text()) for i, page in enumerate(doc)]
    return [(1, data.decode("utf-8", errors="replace"))]


def upsert_document(
    name: str,
    pages: list[tuple[int, str]],
    *,
    source: str = "upload",
    external_id: str | None = None,
    url: str | None = None,
    updated_at=None,
    connection_id: int | None = None,
    allowed: list[str] | None = None,
    uploaded_by: int | None = None,
) -> dict:
    """Index (or re-index) one document. Identity is (connection_id, external_id); re-indexing replaces old chunks.

    `allowed`: lowercase emails that may see it (None = everyone signed in)."""
    allowed = sorted({e.strip().lower() for e in allowed if e.strip()}) if allowed is not None else None
    rows = [(page, c) for page, text in pages for c in chunk(text)]
    if not rows:
        raise ValueError("No extractable text (scanned PDF? OCR is not supported)")
    vectors = embed([c for _, c in rows])
    with connect() as conn, conn.transaction():
        doc_id = conn.execute(
            "INSERT INTO documents (name, source, external_id, url, updated_at, connection_id, allowed, uploaded_by) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
            "ON CONFLICT (connection_id, external_id) DO UPDATE SET name = EXCLUDED.name, url = EXCLUDED.url, "
            "updated_at = EXCLUDED.updated_at, allowed = EXCLUDED.allowed RETURNING id",
            (name, source, external_id or name, url, updated_at, connection_id, allowed, uploaded_by),
        ).fetchone()[0]
        conn.execute("DELETE FROM chunks WHERE document_id = %s", (doc_id,))
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO chunks (document_id, page, content, embedding) VALUES (%s, %s, %s, %s)",
                [(doc_id, page, c, v) for (page, c), v in zip(rows, vectors)],
            )
    return {"id": doc_id, "name": name, "chunks": len(rows)}


def ingest_document(filename: str, data: bytes, user_id: int | None = None, allowed: list[str] | None = None) -> dict:
    """Uploads are keyed per uploader, so two people's `notes.pdf` never overwrite each other."""
    return upsert_document(filename, extract_pages(filename, data), external_id=f"{user_id}:{filename}",
                           allowed=allowed, uploaded_by=user_id)


# ---------- spreadsheets → SQL tables ----------

MAX_SHEET_ROWS = 500_000
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_ISO_TS = re.compile(r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(:\d{2}(\.\d+)?)?$")
_US_DATE = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$")
_NUMBER = re.compile(r"^[-+]?[$€£]?\s?[-+]?(\d{1,3}(,\d{3})+|\d+)(\.\d+)?%?$")


def _num(v: str):
    """'$1,234.50' → 1234.5, '12%' → 12.0, '42' → 42. None if it isn't a number."""
    if not _NUMBER.match(v):
        return None
    clean = re.sub(r"[$€£,%\s+]", "", v)
    try:
        return int(clean) if re.fullmatch(r"-?\d+", clean) else float(clean)
    except ValueError:
        return None


def _date(v):
    if isinstance(v, datetime.datetime):
        return v if (v.hour, v.minute, v.second, v.microsecond) != (0, 0, 0, 0) else v.date()
    if isinstance(v, datetime.date):
        return v
    if isinstance(v, str):
        try:
            if _ISO_DATE.match(v):
                return datetime.date.fromisoformat(v)
            if _ISO_TS.match(v):
                return datetime.datetime.fromisoformat(v.replace(" ", "T"))
            if m := _US_DATE.match(v):
                return datetime.date(int(m[3]), int(m[1]), int(m[2]))
        except ValueError:
            return None
    return None


def _column(values: list) -> tuple[str, list]:
    """Pick the narrowest SQL type that fits every non-empty value, and convert the values to it."""
    present = [v for v in values if v is not None]
    if not present or any(isinstance(v, bool) for v in present):
        return "text", [None if v is None else str(v) for v in values]
    if any(isinstance(v, str) and re.match(r"^0\d", v) for v in present):
        return "text", [None if v is None else str(v) for v in values]  # ZIP codes, IDs: keep leading zeros
    as_num = [None if v is None else v if isinstance(v, (int, float)) else _num(v) if isinstance(v, str) else None for v in values]
    if all(n is not None for n, v in zip(as_num, values) if v is not None):
        if all(isinstance(n, int) for n in as_num if n is not None):
            return "bigint", as_num
        return "double precision", [None if n is None else float(n) for n in as_num]
    dates = [_date(v) for v in present]
    if all(d is not None for d in dates):
        typ = "timestamp" if any(isinstance(d, datetime.datetime) for d in dates) else "date"
        return typ, [None if v is None else _date(v) for v in values]
    return "text", [None if v is None else str(v) for v in values]


def _clean_header(header: list) -> list[str]:
    cols, seen = [], {}
    for i, h in enumerate(header):
        c = re.sub(r"\W+", "_", str(h or "").strip().lower()).strip("_")[:60] or f"col{i + 1}"
        if c[0].isdigit():
            c = f"c_{c}"
        seen[c] = seen.get(c, 0) + 1
        cols.append(c if seen[c] == 1 else f"{c}_{seen[c]}")
    return cols


def parse_sheets(filename: str, data: bytes) -> list[tuple[str | None, list[str], list[list]]]:
    """→ [(sheet name or None, columns, rows)] for a .csv or .xlsx file. Rows hold raw Python values."""
    if filename.lower().endswith((".xlsx", ".xlsm")):
        import openpyxl

        wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)  # data_only: formula results
        out = []
        for ws in wb.worksheets:
            rows = [[None if (isinstance(v, str) and not v.strip()) else (v.strip() if isinstance(v, str) else v) for v in r]
                    for r in ws.iter_rows(values_only=True)]
            rows = [r for r in rows if any(v is not None for v in r)]
            if len(rows) < 2:
                continue  # empty tab or header only
            width = max(i + 1 for r in rows for i, v in enumerate(r) if v is not None)
            header, body = rows[0][:width], [list(r[:width]) + [None] * (width - len(r[:width])) for r in rows[1:]]
            out.append((ws.title, _clean_header(header), body[:MAX_SHEET_ROWS]))
        wb.close()
        return out
    text = data.decode("utf-8-sig", errors="replace")
    rows = [r for r in csv.reader(io.StringIO(text)) if any(c.strip() for c in r)]
    if len(rows) < 2:
        return []
    width = len(rows[0])
    body = [[(c.strip() or None) for c in (r + [""] * width)[:width]] for r in rows[1:MAX_SHEET_ROWS + 1]]
    return [(None, _clean_header(rows[0]), body)]


def table_name(filename: str) -> str:
    base = re.sub(r"\W+", "_", filename.rsplit(".", 1)[0].lower()).strip("_")
    return (base if base and not base[0].isdigit() else f"t_{base}")[:55]


def ingest_spreadsheet(filename: str, data: bytes, *, user_id: int | None = None, allowed: list[str] | None = None,
                       source: str = "upload", connection_id: int | None = None, identity: str | None = None) -> list[dict]:
    """Load every tab of a CSV/Excel file into its own table in schema `data`, registered with its access list.

    Re-ingesting the same file (same uploader, or same connection + identity) replaces its tables in place;
    a different person's file with the same name gets a different table, never an overwrite."""
    sheets = parse_sheets(filename, data)
    if not sheets:
        raise ValueError("No data found: the sheet needs a header row and at least one row of values")
    allowed = sorted({e.strip().lower() for e in allowed if e.strip()}) if allowed is not None else None
    identity = identity or f"{user_id}:{filename}"
    results = []
    with connect() as conn:
        for sheet, cols, rows in sheets:
            ext = f"{identity}::{sheet or ''}"
            title = f"{filename} › {sheet}" if sheet and len(sheets) > 1 else filename
            name = _table_for(conn, ext, connection_id, table_name(f"{filename}_{sheet}" if sheet and len(sheets) > 1 else filename))
            typed = [_column([r[i] for r in rows]) for i in range(len(cols))]
            ident = sql.Identifier("data", name)
            with conn.transaction():
                conn.execute(sql.SQL("DROP TABLE IF EXISTS {}").format(ident))
                conn.execute(sql.SQL("CREATE TABLE {} ({})").format(ident, sql.SQL(", ").join(
                    sql.SQL("{} {}").format(sql.Identifier(c), sql.SQL(t)) for c, (t, _) in zip(cols, typed))))
                with conn.cursor().copy(sql.SQL("COPY {} FROM STDIN").format(ident)) as cp:
                    for j in range(len(rows)):
                        cp.write_row([vals[j] for _, vals in typed])
                conn.execute(
                    "INSERT INTO data_tables (name, title, source, connection_id, external_id, uploaded_by, allowed, row_count, updated_at) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, now()) ON CONFLICT (name) DO UPDATE SET title = EXCLUDED.title, "
                    "allowed = EXCLUDED.allowed, row_count = EXCLUDED.row_count, updated_at = now()",
                    (name, title, source, connection_id, ext, user_id, allowed, len(rows)))
            results.append({"table": name, "title": title, "columns": {c: t for c, (t, _) in zip(cols, typed)}, "rows": len(rows)})
    return results


def _table_for(conn, external_id: str, connection_id: int | None, base: str) -> str:
    row = conn.execute("SELECT name FROM data_tables WHERE external_id = %s AND connection_id IS NOT DISTINCT FROM %s",
                       (external_id, connection_id)).fetchone()
    if row:
        return row[0]
    taken = {r[0] for r in conn.execute(
        "SELECT name FROM data_tables UNION SELECT table_name FROM information_schema.tables WHERE table_schema = 'data'").fetchall()}
    name, n = base, 2
    while name in taken:
        name, n = f"{base[:52]}_{n}", n + 1
    return name


def drop_tables(where: str, params: tuple) -> int:
    """Drop data tables matching a data_tables WHERE clause (fixed strings from our code, never user input)."""
    with connect() as conn, conn.transaction():
        names = [r[0] for r in conn.execute(f"DELETE FROM data_tables WHERE {where} RETURNING name", params).fetchall()]
        for n in names:
            conn.execute(sql.SQL("DROP TABLE IF EXISTS {}").format(sql.Identifier("data", n)))
    return len(names)


def ingest_csv(filename: str, data: bytes, **kw) -> dict:
    """Single-table convenience wrapper (first tab)."""
    first = ingest_spreadsheet(filename, data, **kw)[0]
    return {"table": f"data.{first['table']}", "columns": first["columns"], "rows": first["rows"]}
