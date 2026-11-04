"""PostgreSQL (or any Postgres-compatible DB: Supabase, Neon, RDS, Redshift-style). Queried live, never copied."""
from collections.abc import Iterator

import psycopg

from .base import assert_read_query

LABEL = "PostgreSQL"
BLURB = "Ask questions about a Postgres database (Supabase, Neon, RDS…) and get charts. Read-only."
CATEGORY = "warehouse"
DIALECT = "PostgreSQL"
OAUTH = None
PASTE = {"label": "Connection URL", "placeholder": "postgresql://readonly_user:password@host:5432/dbname",
         "help": "Use a read-only database user"}
CONFIG = [{"key": "schemas", "label": "Schemas", "help": "Comma-separated schemas to expose. Default: public."}]
GUIDE = [
    "Create a read-only user: CREATE ROLE rag_ro LOGIN PASSWORD '…'; GRANT USAGE ON SCHEMA public TO rag_ro; "
    "GRANT SELECT ON ALL TABLES IN SCHEMA public TO rag_ro;",
    "Paste its connection URL below. Queries also run in a read-only transaction with a 15-second limit.",
]
TIMEOUT_MS = 15000


def _connect(creds: dict) -> psycopg.Connection:
    url = creds["access_token"]
    conn = psycopg.connect(url, connect_timeout=10, options=f"-c statement_timeout={TIMEOUT_MS} -c default_transaction_read_only=on")
    conn.read_only = True  # every transaction: BEGIN READ ONLY
    return conn


def parse_paste(text: str) -> dict:
    url = text.strip()
    if not url.startswith(("postgres://", "postgresql://")):
        raise ValueError("Paste a postgresql:// connection URL")
    return {"access_token": url}


def whoami(creds: dict) -> str:
    with _connect(creds) as conn:
        user, db = conn.execute("SELECT current_user, current_database()").fetchone()
    return f"{user}@{db}"


def _schemas(config: dict) -> list[str]:
    return [s.strip() for s in (config.get("schemas") or "public").split(",") if s.strip()]


def list_tables(config: dict, creds: dict) -> dict[str, dict[str, str]]:
    with _connect(creds) as conn:
        rows = conn.execute(
            "SELECT table_schema || '.' || table_name, column_name, data_type FROM information_schema.columns "
            "WHERE table_schema = ANY(%s) ORDER BY 1, ordinal_position", (_schemas(config),)).fetchall()
    out: dict[str, dict[str, str]] = {}
    for t, c, typ in rows:
        out.setdefault(t, {})[c] = typ
    return out


def query(config: dict, creds: dict, sql: str, max_rows: int) -> dict:
    sql = assert_read_query(sql)
    with _connect(creds) as conn:
        cur = conn.execute(sql, prepare=True)  # prepared = exactly one statement, so the read-only txn can't be escaped
        cols = [d.name for d in cur.description or []]
        rows = cur.fetchmany(max_rows + 1) if cur.description else []
        conn.rollback()
    return {"columns": cols, "rows": [list(r) for r in rows[:max_rows]], "truncated": len(rows) > max_rows}


def fetch(*_a, **_k) -> Iterator:
    return iter(())  # live source: nothing to index
