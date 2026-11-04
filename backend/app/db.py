import os
from pathlib import Path

import psycopg
from dotenv import load_dotenv
from pgvector.psycopg import register_vector

load_dotenv(Path(__file__).parents[2] / ".env")

DATABASE_URL = os.environ["DATABASE_URL"]
READER_DATABASE_URL = os.environ["READER_DATABASE_URL"]
MIGRATIONS = Path(__file__).parents[2] / "db" / "migrations"


def connect() -> psycopg.Connection:
    # ponytail: connection per call; add psycopg_pool if request volume grows
    conn = psycopg.connect(DATABASE_URL, autocommit=True)
    register_vector(conn)
    return conn


def connect_reader() -> psycopg.Connection:
    """Least-privilege role: SELECT on schema `data` only, read-only, 5s timeout (see db/migrations/001_init.sql)."""
    return psycopg.connect(READER_DATABASE_URL, autocommit=True)


def migrate(url: str = DATABASE_URL) -> list[str]:
    """Apply db/migrations/*.sql not yet recorded, in order, each in its own transaction."""
    applied = []
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute("SELECT pg_advisory_lock(7212)")  # one migrator at a time across processes
        conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (name text PRIMARY KEY, applied_at timestamptz DEFAULT now())")
        done = {r[0] for r in conn.execute("SELECT name FROM schema_migrations").fetchall()}
        for f in sorted(MIGRATIONS.glob("*.sql")):
            if f.name not in done:
                with conn.transaction():
                    conn.execute(f.read_text())
                    conn.execute("INSERT INTO schema_migrations (name) VALUES (%s)", (f.name,))
                applied.append(f.name)
    return applied
