"""Connector registry + sync engine.

Each connector module exposes LABEL, BLURB, CATEGORY (index | business | warehouse | tool; default index), CONFIG, OAUTH (provider spec or None), PASTE (manual-credential spec or None),
whoami(creds) -> account label, and fetch(config, since, creds) -> Iterator[Doc].
"""
import json
import os
import threading
import time
from datetime import datetime, timedelta, timezone

from ..db import connect
from ..ingest import upsert_document
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
    return conn_id in _running


def sync(conn_id: int, full: bool = False) -> None:
    """Pull changed items from one connection into the index. Safe to call concurrently (second call is a no-op)."""
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
        creds = load_credentials(conn_id)
        if needs_auth(kind) and not creds:
            raise RuntimeError("Not connected: no credentials stored. Remove and reconnect this source.")
        started = datetime.now(timezone.utc)
        since = None if full or not last else last - OVERLAP
        n = 0
        for doc in module.fetch(config, since, creds):
            if doc.table_file:
                from ..ingest import ingest_spreadsheet
                try:
                    ingest_spreadsheet(doc.table_file[0], doc.table_file[1], allowed=doc.allowed, source=kind,
                                       connection_id=conn_id, identity=doc.external_id)
                except ValueError:
                    pass  # empty sheet
            pages = doc.pages or [(1, f"{doc.title}\n\n{doc.text}")]
            try:
                upsert_document(doc.title, pages, source=kind, external_id=doc.external_id, url=doc.url,
                                updated_at=doc.updated_at, connection_id=conn_id, allowed=doc.allowed)
                n += 1
            except ValueError:
                pass  # no extractable text
        with connect() as conn:
            conn.execute("UPDATE connections SET status = %s, last_synced_at = %s WHERE id = %s",
                         (f"ok, {n} updated", started, conn_id))
    except Exception as e:
        with connect() as conn:
            conn.execute("UPDATE connections SET status = 'error', error = %s WHERE id = %s", (str(e)[:2000], conn_id))
    finally:
        _running.discard(conn_id)


def sync_in_background(conn_id: int, full: bool = False) -> None:
    threading.Thread(target=sync, args=(conn_id, full), daemon=True, name=f"sync-{conn_id}").start()


def sync_all() -> None:
    with connect() as conn:
        ids = [r[0] for r in conn.execute("SELECT id, kind FROM connections WHERE enabled ORDER BY id").fetchall()
               if r[1] in REGISTRY and not is_live(r[1])]
    for i in ids:
        sync(i)


def start_scheduler() -> None:
    """Background loop: sync every enabled connection every SYNC_INTERVAL_MINUTES (0 disables)."""
    if SYNC_INTERVAL_MIN <= 0:
        return

    def loop():
        while True:
            time.sleep(SYNC_INTERVAL_MIN * 60)
            try:
                sync_all()
            except Exception as e:  # never let the scheduler die
                print(f"scheduler: {e}")

    threading.Thread(target=loop, daemon=True, name="connector-sync").start()


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
    """None/empty = everyone. Otherwise validated, lowercased, de-duplicated emails."""
    if not allowed:
        return None
    out = sorted({e.strip().lower() for e in allowed if e.strip()})
    bad = [e for e in out if "@" not in e or " " in e]
    if bad:
        raise ValueError(f"Not an email: {', '.join(bad[:3])}")
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
