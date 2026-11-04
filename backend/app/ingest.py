"""Pre-processing & indexing: document -> chunks -> embeddings -> pgvector. CSV -> table in schema `data`."""
import csv
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


def _infer_type(values: list[str]) -> str:
    vals = [v for v in values if v != ""]
    for typ, cast in (("bigint", int), ("double precision", float)):
        try:
            for v in vals:
                cast(v)
            return typ if vals else "text"
        except ValueError:
            pass
    return "text"


def table_name(filename: str) -> str:
    base = re.sub(r"\W+", "_", filename.rsplit(".", 1)[0].lower()).strip("_")
    return (base if base and not base[0].isdigit() else f"t_{base}")[:60]


def ingest_csv(filename: str, data: bytes) -> dict:
    reader = csv.reader(io.StringIO(data.decode("utf-8-sig", errors="replace")))
    header, *body = list(reader)
    cols = [re.sub(r"\W+", "_", h.strip().lower()).strip("_") or f"col{i}" for i, h in enumerate(header)]
    types = [_infer_type([r[i] if i < len(r) else "" for r in body]) for i in range(len(cols))]
    name = table_name(filename)
    ident = sql.Identifier("data", name)
    with connect() as conn, conn.transaction():
        conn.execute(sql.SQL("DROP TABLE IF EXISTS {}").format(ident))
        conn.execute(
            sql.SQL("CREATE TABLE {} ({})").format(
                ident,
                sql.SQL(", ").join(sql.SQL("{} {}").format(sql.Identifier(c), sql.SQL(t)) for c, t in zip(cols, types)),
            )
        )
        with conn.cursor().copy(sql.SQL("COPY {} FROM STDIN").format(ident)) as cp:
            for r in body:
                cp.write_row([(r[i] if i < len(r) and r[i] != "" else None) for i in range(len(cols))])
    return {"table": f"data.{name}", "columns": dict(zip(cols, types)), "rows": len(body)}
