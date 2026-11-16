import csv
import io
import re
import time
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone

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
    table_file: tuple[str, bytes] | None = None  # (filename, .xlsx/.csv bytes): also load as queryable tables


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


MAX_ROWS = 200_000  # per synced table


def table_doc(external_id: str, name: str, columns: list[str], rows: Iterable[dict]) -> Doc:
    """A business dataset → one Doc whose CSV is loaded as a queryable table (replaced on every sync).

    `rows` may be a lazy generator over paginated API results: it stops being consumed at MAX_ROWS."""
    buf, n = io.StringIO(), 0
    w = csv.writer(buf)
    w.writerow(columns)
    for r in rows:
        if n >= MAX_ROWS:
            break
        w.writerow(["" if r.get(c) is None else r.get(c) for c in columns])
        n += 1
    return Doc(external_id=external_id, title=name, text=f"{name}: {n:,} rows with columns {', '.join(columns)} (queryable as a table)",
               table_file=(f"{name}.csv", buf.getvalue().encode()), updated_at=datetime.now(timezone.utc))


def check(value: str, pattern: str, what: str) -> str:
    """Hosts/ids (lowercased) interpolated into URLs must match a strict pattern (SSRF / path injection guard)."""
    value = (value or "").strip().lower().removeprefix("https://").rstrip("/")
    if not re.fullmatch(pattern, value):
        raise ValueError(f"{what} doesn't look right: {value[:80]!r}")
    return value


READ_KEYWORDS = ("select", "with", "show", "describe", "desc", "explain", "values")


def assert_read_query(sql: str) -> str:
    """Defense in depth for warehouses: allow one read-only statement. The real guard is a read-only role/credential."""
    body = re.sub(r"--[^\n]*|/\*.*?\*/", " ", sql, flags=re.S).strip().rstrip(";").strip()
    if not body:
        raise ValueError("Empty query")
    if ";" in re.sub(r"'(?:[^']|'')*'|\"(?:[^\"]|\"\")*\"|`[^`]*`", "", body):
        raise ValueError("Run one statement at a time")
    if body.split(None, 1)[0].lower() not in READ_KEYWORDS:
        raise ValueError("Only read-only queries (SELECT / WITH / SHOW / DESCRIBE / EXPLAIN) are allowed")
    return body
