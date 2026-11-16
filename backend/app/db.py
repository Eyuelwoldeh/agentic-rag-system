import os
import threading
from contextlib import AbstractContextManager
from pathlib import Path

import psycopg
from dotenv import load_dotenv
from pgvector.psycopg import register_vector
from psycopg_pool import ConnectionPool

load_dotenv(Path(__file__).parents[2] / ".env")

DATABASE_URL = os.environ["DATABASE_URL"]
READER_DATABASE_URL = os.environ["READER_DATABASE_URL"]
MIGRATIONS = Path(__file__).parents[2] / "db" / "migrations"


POOL_MIN = int(os.getenv("DB_POOL_MIN", "1"))
POOL_MAX = int(os.getenv("DB_POOL_MAX", "10"))
_pool: ConnectionPool | None = None
_pool_lock = threading.Lock()


def _reset(conn: psycopg.Connection) -> None:
    conn.autocommit = True  # a caller may have flipped it; every pooled connection starts autocommit


def pool() -> ConnectionPool:
    """Created on first use (after migrations, so the vector type exists for register_vector)."""
    global _pool
    with _pool_lock:
        if _pool is None:
            _pool = ConnectionPool(DATABASE_URL, min_size=POOL_MIN, max_size=POOL_MAX, kwargs={"autocommit": True},
                                   configure=register_vector, reset=_reset, check=ConnectionPool.check_connection,
                                   name="app", open=True)
        return _pool


def connect() -> AbstractContextManager[psycopg.Connection]:
    """`with connect() as conn:` borrows an autocommit connection from the pool and returns it afterwards."""
    return pool().connection()


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
