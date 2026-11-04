"""Every SQL-queryable source behind one interface: uploaded CSVs plus live warehouse connections.

Access rules match search: a warehouse connection with an `allowed` list is only queryable by those people.
"""
import threading
import time

from . import connectors
from .db import connect, connect_reader

UPLOADS = "uploads"
MAX_ROWS = 2000  # kept for charts/dashboards; the model sees a smaller preview
SCHEMA_TTL = 600

_schema_cache: dict[int, tuple[float, dict]] = {}
_cache_lock = threading.Lock()


class AccessDenied(Exception):
    pass


def available(email: str | None, admin: bool = False) -> list[dict]:
    """Sources this person may query."""
    out = [{"id": UPLOADS, "name": "Uploaded spreadsheets (CSV)", "kind": "uploads", "dialect": "PostgreSQL (schema `data`)"}]
    with connect() as conn:
        rows = conn.execute(
            "SELECT id, kind, name, account FROM connections WHERE enabled AND "
            "(%s OR allowed IS NULL OR %s = ANY(allowed)) ORDER BY id",
            (admin, (email or "").lower())).fetchall()
    for cid, kind, name, account in rows:
        if kind in connectors.REGISTRY and connectors.is_warehouse(kind):
            m = connectors.REGISTRY[kind]
            out.append({"id": f"conn-{cid}", "name": name, "kind": kind, "dialect": m.DIALECT, "account": account})
    return out


def _resolve(source: str, email: str | None, admin: bool = False):
    """→ (module, config, creds, connection_id) after an access check. Uploads → (None, ...)."""
    if source == UPLOADS:
        return None, None, None, None
    if not source.startswith("conn-") or not source[5:].isdigit():
        raise AccessDenied(f"Unknown data source {source!r}. Call list_data_sources first.")
    cid = int(source[5:])
    if not any(s["id"] == source for s in available(email, admin)):
        raise AccessDenied(f"No access to data source {source}")
    from .oauth import load_credentials

    with connect() as conn:
        kind, config = conn.execute("SELECT kind, config FROM connections WHERE id = %s", (cid,)).fetchone()
    return connectors.REGISTRY[kind], config, load_credentials(cid), cid


def invalidate_schema(conn_id: int) -> None:
    with _cache_lock:
        _schema_cache.pop(conn_id, None)


def list_tables(source: str, email: str | None, admin: bool = False) -> dict[str, dict[str, str]]:
    module, config, creds, cid = _resolve(source, email, admin)
    if module is None:
        with connect_reader() as conn:
            rows = conn.execute("SELECT table_name, column_name, data_type FROM information_schema.columns "
                                "WHERE table_schema = 'data' ORDER BY table_name, ordinal_position").fetchall()
        out: dict[str, dict[str, str]] = {}
        for t, c, typ in rows:
            out.setdefault(t, {})[c] = typ
        return out
    with _cache_lock:
        hit = _schema_cache.get(cid)
    if hit and time.time() - hit[0] < SCHEMA_TTL:
        return hit[1]
    tables = module.list_tables(config, creds)
    with _cache_lock:
        _schema_cache[cid] = (time.time(), tables)
    return tables


def run(source: str, sql: str, email: str | None, admin: bool = False, max_rows: int = MAX_ROWS) -> dict:
    module, config, creds, _ = _resolve(source, email, admin)
    if module is None:
        # uploads: least-privilege role (SELECT on `data` only) + read-only txn + one prepared statement
        with connect_reader() as conn:
            conn.autocommit = False
            conn.read_only = True
            cur = conn.execute(sql, prepare=True)
            cols = [d.name for d in cur.description or []]
            rows = cur.fetchmany(max_rows + 1) if cur.description else []
            conn.rollback()
        return {"columns": cols, "rows": [list(r) for r in rows[:max_rows]], "truncated": len(rows) > max_rows}
    return module.query(config, creds, sql, max_rows)
