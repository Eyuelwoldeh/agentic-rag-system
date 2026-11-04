"""Table-level access control for spreadsheets, enforced by Postgres.

Every app user gets a dedicated LOGIN role (plus one "public" role for callers with no identity). A role is
GRANTed SELECT only on the `data` tables its user may see, per `data_tables.allowed` and, for synced tables,
the connection's access list. Queries connect *as* that role, so even SQL tricks such as
`query_to_xml('select * from data.secret')` run with the user's own privileges. Nobody can escalate, because these
roles are not members of each other, and the shared `rag_reader` role no longer has SELECT on `data`.

Role passwords are derived from SECRETS_KEY (HMAC), so they never need storing.
"""
import hashlib
import hmac
import os
import re
import threading

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from .db import DATABASE_URL, connect

_DB = conninfo_to_dict(DATABASE_URL)
_PREFIX = "rag_" + re.sub(r"\W", "", _DB.get("dbname", "rag"))[:20] + "_u_"  # roles are cluster-wide: scope by database
_lock = threading.Lock()


def role_name(user_id: int | None) -> str:
    return f"{_PREFIX}{user_id if user_id is not None else 'public'}"


def _password(role: str) -> str:
    key = os.environ["SECRETS_KEY"].encode()
    return hmac.new(key, f"table-role:{role}".encode(), hashlib.sha256).hexdigest()


def _visible_tables(conn, email: str | None) -> list[str]:
    rows = conn.execute(
        "SELECT t.name FROM data_tables t LEFT JOIN connections c ON c.id = t.connection_id "
        "JOIN information_schema.tables it ON it.table_schema = 'data' AND it.table_name = t.name "
        "WHERE (t.allowed IS NULL OR %(e)s = ANY(t.allowed)) AND (c.allowed IS NULL OR %(e)s = ANY(c.allowed))",
        {"e": (email or "").lower() or None}).fetchall()
    return [r[0] for r in rows]


def sync_role(user_id: int | None, email: str | None) -> str:
    """Create/refresh the user's role and set its grants to exactly the tables they may see. Returns the role name."""
    role = role_name(user_id)
    ident = sql.Identifier(role)
    with _lock, connect() as conn, conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(7213)")
        exists = conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)).fetchone()
        if not exists:
            conn.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD {} NOINHERIT").format(ident, sql.Literal(_password(role))))
            conn.execute(sql.SQL("ALTER ROLE {} SET search_path = data").format(ident))
            conn.execute(sql.SQL("ALTER ROLE {} SET statement_timeout = '10s'").format(ident))
            conn.execute(sql.SQL("ALTER ROLE {} SET default_transaction_read_only = on").format(ident))
        else:  # keep the derived password in step with SECRETS_KEY
            conn.execute(sql.SQL("ALTER ROLE {} PASSWORD {}").format(ident, sql.Literal(_password(role))))
        conn.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(sql.Identifier(_DB["dbname"]), ident))
        conn.execute(sql.SQL("GRANT USAGE ON SCHEMA data TO {}").format(ident))
        conn.execute(sql.SQL("REVOKE SELECT ON ALL TABLES IN SCHEMA data FROM {}").format(ident))
        tables = _visible_tables(conn, email)
        if tables:
            conn.execute(sql.SQL("GRANT SELECT ON {} TO {}").format(
                sql.SQL(", ").join(sql.Identifier("data", t) for t in tables), ident))
    return role


def connect_as(user_id: int | None, email: str | None) -> psycopg.Connection:
    """A read-only connection that can only see this user's permitted tables."""
    role = sync_role(user_id, email)
    params = {**_DB, "user": role, "password": _password(role)}
    return psycopg.connect(make_conninfo(**params), autocommit=True)


def user_id_for(email: str | None) -> int | None:
    if not email:
        return None
    with connect() as conn:
        row = conn.execute("SELECT id FROM users WHERE email = %s", (email.lower(),)).fetchone()
    return row[0] if row else None


def visible_tables(email: str | None) -> dict[str, dict]:
    """name → {title, source} for tables this person may query."""
    with connect() as conn:
        names = _visible_tables(conn, email)
        rows = conn.execute("SELECT name, title, source, allowed IS NOT NULL FROM data_tables WHERE name = ANY(%s)",
                            (names,)).fetchall()
    return {r[0]: {"title": r[1], "source": r[2], "restricted": r[3]} for r in rows}
