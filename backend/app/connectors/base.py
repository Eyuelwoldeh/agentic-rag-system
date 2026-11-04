import time
from dataclasses import dataclass
from datetime import datetime

import httpx


@dataclass
class Doc:
    external_id: str
    title: str
    text: str = ""
    url: str | None = None
    updated_at: datetime | None = None
    pages: list[tuple[int, str]] | None = None  # set instead of `text` for paged files (PDFs)
    allowed: list[str] | None = None  # emails that can see this item at the source; None = everyone in the connection


http = httpx.Client(timeout=30, follow_redirects=True)  # tests swap this for a MockTransport client


def request(method: str, url: str, retries: int = 5, **kw) -> httpx.Response:
    """HTTP with backoff on 429/5xx (Slack, GitHub and Notion all rate-limit)."""
    for attempt in range(retries):
        resp = http.request(method, url, **kw)
        if resp.status_code != 429 and resp.status_code < 500:
            if resp.status_code >= 400:  # not raise_for_status(): it also raises on 3xx, which callers may handle
                resp.raise_for_status()
            return resp
        time.sleep(float(resp.headers.get("retry-after", 2**attempt)))
    resp.raise_for_status()
    return resp


READ_KEYWORDS = ("select", "with", "show", "describe", "desc", "explain", "values")


def assert_read_query(sql: str) -> str:
    """Defense in depth for warehouses: allow one read-only statement. The real guard is a read-only role/credential."""
    import re

    body = re.sub(r"--[^\n]*|/\*.*?\*/", " ", sql, flags=re.S).strip().rstrip(";").strip()
    if not body:
        raise ValueError("Empty query")
    if ";" in re.sub(r"'(?:[^']|'')*'|\"(?:[^\"]|\"\")*\"|`[^`]*`", "", body):
        raise ValueError("Run one statement at a time")
    if body.split(None, 1)[0].lower() not in READ_KEYWORDS:
        raise ValueError("Only read-only queries (SELECT / WITH / SHOW / DESCRIBE / EXPLAIN) are allowed")
    return body
