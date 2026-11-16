"""Connector registry + sync engine.

Each connector module exposes LABEL, BLURB, CATEGORY (index | business | warehouse | tool; default index), CONFIG, OAUTH (provider spec or None), PASTE (manual-credential spec or None),
whoami(creds) -> account label, and fetch(config, since, creds) -> Iterator[Doc].
"""
import json
import os
import threading
from datetime import datetime, timedelta, timezone

from .. import jobs, schedules
from ..db import connect
from ..ingest import drop_tables, upsert_document
from ..security import encrypt
from . import (bigquery, confluence, gdrive, github, hubspot, jira, mcp, microsoft365, notion, postgres, quickbooks, salesforce,
               shopify, slack, snowflake, square, stripe, web, xero)

REGISTRY = {"slack": slack, "gdrive": gdrive, "microsoft365": microsoft365, "notion": notion, "confluence": confluence,
            "jira": jira, "github": github, "web": web,
            "quickbooks": quickbooks, "xero": xero, "stripe": stripe, "shopify": shopify, "square": square,
            "hubspot": hubspot, "salesforce": salesforce,
            "bigquery": bigquery, "snowflake": snowflake, "postgres": postgres, "mcp": mcp}


def is_warehouse(kind: str) -> bool:
    """Warehouses are queried live by the agent; everything else is synced into the search index."""
    return getattr(REGISTRY[kind], "CATEGORY", "index") == "warehouse"
SYNC_INTERVAL_MIN = int(os.getenv("SYNC_INTERVAL_MINUTES", "30"))
OVERLAP = timedelta(minutes=10)  # re-read a little before the last sync so edits made mid-sync aren't missed

_running: set[int] = set()
_lock = threading.Lock()


def is_live(kind: str) -> bool:
    """Warehouses and MCP servers are used at question time; nothing is copied. Business systems are synced as tables."""
    return getattr(REGISTRY[kind], "CATEGORY", "index") in ("warehouse", "tool")


def needs_auth(kind: str) -> bool:
    m = REGISTRY[kind]
    paste = getattr(m, "PASTE", None)
    return bool(getattr(m, "OAUTH", None) or (paste and not paste.get("optional")))


def catalog(oauth_ready: set[str]) -> list[dict]:
    return [
        {"kind": k, "label": m.LABEL, "config": m.CONFIG, "doc": (m.__doc__ or "").strip(), "blurb": m.BLURB,
         "category": getattr(m, "CATEGORY", "index"), "guide": getattr(m, "GUIDE", []),
         "oauth": bool(getattr(m, "OAUTH", None)), "oauth_ready": k in oauth_ready, "paste": getattr(m, "PASTE", None)}
        for k, m in REGISTRY.items()
    ]


def parse_paste(kind: str, text: str) -> dict:
    m = REGISTRY[kind]
    if hasattr(m, "parse_paste"):
        return m.parse_paste(text)
    token = text.strip()
    if not token or any(c.isspace() for c in token):
        raise ValueError("Paste just the token, with no spaces")
    return {"access_token": token}


def is_running(conn_id: int) -> bool:
    """Queued or syncing (in this process or any worker)."""
    return conn_id in _running or jobs.is_active(f"connector.sync:{conn_id}")


def sync(conn_id: int, full: bool = False) -> None:
    """Pull changed items from one connection into the index. Safe to call concurrently (second call is a no-op).

    A full sync (or the first one) also removes documents and tables the source no longer has."""
    with _lock:
        if conn_id in _running:
            return
        _running.add(conn_id)
    try:
        from ..oauth import load_credentials  # late import: oauth imports this package

        with connect() as conn:
            row = conn.execute("SELECT kind, config, last_synced_at FROM connections WHERE id = %s", (conn_id,)).fetchone()
            if not row:
                return
            conn.execute("UPDATE connections SET status = 'syncing', error = NULL WHERE id = %s", (conn_id,))
        kind, config, last = row
        module = REGISTRY[kind]
        creds = load_credentials(conn_id)
        if kind == "mcp":
            account = module.verify(config, load_credentials(conn_id))
            with connect() as conn:
                conn.execute("UPDATE connections SET status = 'live', account = coalesce(nullif(%s, ''), account), "
                             "last_synced_at = now() WHERE id = %s", (account, conn_id))
            return
        if is_warehouse(kind):  # nothing to copy: verify it still answers, so the status is honest
            from ..datasources import invalidate_schema, list_tables
            invalidate_schema(conn_id)
            n = len(list_tables(f"conn-{conn_id}", email=None, admin=True))
            with connect() as conn:
                conn.execute("UPDATE connections SET status = %s, last_synced_at = now() WHERE id = %s",
                             (f"live · {n} tables", conn_id))
            return
        if needs_auth(kind) and not creds:
            raise RuntimeError("Not connected: no credentials stored. Remove and reconnect this source.")
        started = datetime.now(timezone.utc)
        since = None if full or not last else last - OVERLAP
        n = 0
        seen_docs, seen_tables = [], []
        for doc in module.fetch(config, since, creds):
            seen_docs.append(doc.external_id)
            if doc.table_file:
                from ..ingest import ingest_spreadsheet
                try:
                    seen_tables += [t["external_id"] for t in ingest_spreadsheet(
                        doc.table_file[0], doc.table_file[1], allowed=doc.allowed, source=kind,
                        connection_id=conn_id, identity=doc.external_id)]
                except ValueError:
                    pass  # empty sheet
            pages = doc.pages or [(1, f"{doc.title}\n\n{doc.text}")]
            try:
                upsert_document(doc.title, pages, source=kind, external_id=doc.external_id, url=doc.url,
                                updated_at=doc.updated_at, connection_id=conn_id, allowed=doc.allowed)
                n += 1
            except ValueError:
                pass  # no extractable text
        removed = remove_unseen(conn_id, seen_docs, seen_tables) if since is None else 0
        with connect() as conn:
            conn.execute("UPDATE connections SET status = %s, last_synced_at = %s WHERE id = %s",
                         (f"ok, {n} updated" + (f", {removed} removed" if removed else ""), started, conn_id))
    except Exception as e:
        with connect() as conn:
            conn.execute("UPDATE connections SET status = 'error', error = %s WHERE id = %s", (str(e)[:2000], conn_id))
    finally:
        _running.discard(conn_id)


def remove_unseen(conn_id: int, seen_docs: list[str], seen_tables: list[str]) -> int:
    """After a full sync: delete this connection's documents and tables the source didn't return."""
    if not seen_docs:  # an empty listing is more likely an API hiccup or lost access than a source emptied out
        return 0
    with connect() as conn:
        n = conn.execute("DELETE FROM documents WHERE connection_id = %s AND NOT (external_id = ANY(%s))",
                         (conn_id, seen_docs)).rowcount
    return n + drop_tables("connection_id = %s AND NOT (external_id = ANY(%s))", (conn_id, seen_tables))


def sync_in_background(conn_id: int, full: bool = False) -> None:
    """Queue a sync job (one per connection at a time; a request while one is queued or running is dropped)."""
    jobs.enqueue("connector.sync", {"id": conn_id, "full": full}, dedupe_key=f"connector.sync:{conn_id}", max_attempts=1)


@jobs.handler("connector.sync")
def _sync_job(payload: dict) -> None:
    sync(payload["id"], payload.get("full", False))
    with connect() as conn:
        row = conn.execute("SELECT status, error FROM connections WHERE id = %s", (payload["id"],)).fetchone()
    if row and row[0] == "error":  # sync() records errors on the connection; also fail the job so it shows in Settings
        raise RuntimeError(row[1])


@schedules.every_minute
def enqueue_due_syncs() -> None:
    """Queue a sync for every enabled indexed connection not synced (or attempted) in SYNC_INTERVAL_MINUTES (0 disables)."""
    if SYNC_INTERVAL_MIN <= 0:
        return
    with connect() as conn:
        rows = conn.execute(
            "SELECT c.id, c.kind FROM connections c WHERE c.enabled "
            "AND (c.last_synced_at IS NULL OR c.last_synced_at < now() - make_interval(mins => %s)) "
            "AND NOT EXISTS (SELECT 1 FROM jobs j WHERE j.dedupe_key = 'connector.sync:' || c.id "
            "  AND j.created_at > now() - make_interval(mins => %s)) ORDER BY c.id",
            (SYNC_INTERVAL_MIN, SYNC_INTERVAL_MIN)).fetchall()
    for conn_id, kind in rows:
        if kind in REGISTRY and not is_live(kind):
            sync_in_background(conn_id)


def clean_config(kind: str, config: dict) -> dict:
    if kind not in REGISTRY:
        raise ValueError(f"Unknown connector {kind}")
    allowed = {c["key"] for c in REGISTRY[kind].CONFIG}
    cfg = {k: str(v).strip()[:2000] for k, v in config.items() if k in allowed and str(v).strip()}
    for c in REGISTRY[kind].CONFIG:
        if c.get("required") and not cfg.get(c["key"]):
            raise ValueError(f"{c['label']} is required")
    return cfg


def clean_allowed(allowed: list[str] | None) -> list[str] | None:
    """None/empty = everyone. Otherwise validated, lowercased, de-duplicated emails and `group:<name>` entries."""
    from ..groups import PREFIX, existing

    if not allowed:
        return None
    out = sorted({" ".join(e.split()).lower() for e in allowed if e.strip()})
    groups = [e[len(PREFIX):] for e in out if e.startswith(PREFIX)]
    bad = [e for e in out if not e.startswith(PREFIX) and ("@" not in e or " " in e)]
    if bad:
        raise ValueError(f"Not an email: {', '.join(bad[:3])}")
    if missing := sorted(set(groups) - existing(groups)):
        raise ValueError(f"No such group: {', '.join(missing[:3])}")
    return out or None


def create(kind: str, name: str, config: dict, creds: dict | None = None, user_id: int | None = None,
           allowed: list[str] | None = None) -> int:
    """Validate config, verify credentials against the provider (whoami), store them encrypted."""
    cfg = clean_config(kind, config)
    allowed = clean_allowed(allowed)
    account = None
    if needs_auth(kind):
        if not creds:
            raise ValueError("Credentials are required for this connector")
    if needs_auth(kind) or hasattr(REGISTRY[kind], "verify"):
        try:
            account = REGISTRY[kind].verify(cfg, creds) if hasattr(REGISTRY[kind], "verify") else REGISTRY[kind].whoami(creds)
            if is_warehouse(kind):  # proves credentials *and* config (project, datasets, account...) work
                n = len(REGISTRY[kind].list_tables(cfg, creds))
                account = ", ".join(filter(None, [account, f"{n} tables"]))
        except Exception as e:
            raise ValueError(f"{REGISTRY[kind].LABEL} rejected these settings: {str(e)[:300]}")
    with connect() as conn:
        return conn.execute(
            "INSERT INTO connections (kind, name, config, credentials, account, created_by, allowed) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
            (kind, (name or REGISTRY[kind].LABEL)[:100], json.dumps(cfg), encrypt(creds) if creds else None, account,
             user_id, allowed),
        ).fetchone()[0]
