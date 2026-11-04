"""Checks for the non-LLM logic. Run: uv run python -m pytest tests  (DB tests need `docker compose up`)."""
import psycopg
import pytest

from app.db import connect
from app.guardrails import redact
from app.ingest import _column, chunk, ingest_csv, ingest_document, table_name
from app.datasources import run as run_query
from app.tools import search_documents


def run_sql(q):
    return run_query("uploads", q, email=None)


def test_chunk_respects_size_and_overlaps():
    text = "\n\n".join(f"Paragraph {i} " + "word " * 60 for i in range(20))
    chunks = chunk(text, size=800, overlap=100)
    assert len(chunks) > 1
    assert all(len(c) <= 800 + 100 + 2 for c in chunks)
    assert chunks[0][-50:] in chunks[1]  # overlap carried over
    assert chunk("x" * 3000, size=1000, overlap=100)[0] == "x" * 1000


def test_redact():
    out = redact("mail bob@x.com, ssn 123-45-6789, call (555) 123-4567, card 4111 1111 1111 1111")
    assert "bob@x.com" not in out and "123-45-6789" not in out and "4567" not in out and "4111" not in out


def test_infer_type_and_table_name():
    assert _column(["1", "2", None])[0] == "bigint"
    assert _column(["1.5", "2"])[0] == "double precision"
    assert _column(["a", "2"])[0] == "text"
    assert table_name("Q3 Sales-2024.csv") == "q3_sales_2024"
    assert _column(["00123", "02134"])[0] == "text"  # ZIP codes keep their leading zeros
    assert table_name("2024.csv") == "t_2024"


def test_csv_ingest_and_sql_is_read_only():
    ingest_csv("pytest_sales.csv", b"region,amount\nnorth,10\nsouth,32\n")
    assert run_sql("SELECT sum(amount) AS s FROM pytest_sales")["rows"] == [[42]]
    for bad in ("DROP TABLE pytest_sales", "SELECT * FROM public.runs", "SELECT pg_read_file('/etc/passwd')",
                "SET TRANSACTION READ WRITE; CREATE TABLE data.x (a int); COMMIT", "SELECT 1; SELECT 2"):
        with pytest.raises(psycopg.Error):
            run_sql(bad)
    with connect() as conn:
        conn.execute("DROP TABLE data.pytest_sales")


def test_document_ingest_and_hybrid_search():
    ingest_document("pytest_doc.md", b"The zorblax reactor runs at 42 kelvin.\n\nBananas are yellow.")
    hits = search_documents("zorblax reactor temperature", k=3)
    assert "zorblax" in hits[0]["content"]
    with connect() as conn:
        conn.execute("DELETE FROM documents WHERE name = 'pytest_doc.md'")


def test_search_only_mode_answers_without_llm(monkeypatch):
    from app import agent

    monkeypatch.setattr(agent, "SEARCH_ONLY", True)
    ingest_document("pytest_fallback.md", b"The glimmerfox cache is flushed every 15 minutes.")
    try:
        events = list(agent.run("How often is the glimmerfox cache flushed?", []))
        done = events[-1]
        assert done["type"] == "done" and "Search-only mode" in done["answer"]
        assert "15 minutes" in done["answer"] and "[S1]" in done["answer"]
        assert not any(e["type"] == "error" for e in events)
    finally:
        with connect() as conn:
            conn.execute("DELETE FROM documents WHERE name = 'pytest_fallback.md'")
