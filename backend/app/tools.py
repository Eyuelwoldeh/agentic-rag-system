"""Tool layer. Client tools run here; web search and code execution run server-side at Anthropic."""
import json

import re
import secrets
from dataclasses import dataclass, field
from decimal import Decimal

from . import datasources, tableacl
from .db import connect
from .groups import principals
from .ingest import embed

PREVIEW_ROWS = 50

CLIENT_TOOLS = [
    {
        "name": "search_documents",
        "description": (
            "Hybrid (semantic + keyword) search over ALL company documents: uploaded files (memos, handbooks, notes, PDFs) "
            "plus anything synced from Slack, Google Drive, Notion, GitHub or websites. Only returns what the current user may see. "
            "Leave `source` empty to search everything; set it only when the user names a specific place. "
            "Returns numbered passages labelled [S1], [S2]... Cite them by label. "
            "Call several times with rephrased queries if results are thin."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "k": {"type": "integer", "description": "Passages to return (default 6, max 15)"},
                "source": {
                    "type": "string",
                    "enum": ["upload", "slack", "gdrive", "notion", "github", "web"],
                    "description": "Optional, usually leave empty. Only when the user asks about one place, e.g. 'slack'. 'upload' = uploaded files.",
                },
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
    {
        "name": "list_data_sources",
        "description": (
            "List the SQL data sources the user can query (uploaded CSVs, and live warehouses such as BigQuery, "
            "Snowflake or Postgres), each with its SQL dialect and table names. Call this first for any question "
            "about numbers, metrics, trends or records."
        ),
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "describe_tables",
        "description": "Columns and types for tables in one data source. Call before writing SQL against them.",
        "input_schema": {
            "type": "object",
            "properties": {
                "source": {"type": "string", "description": "Data source id from list_data_sources"},
                "tables": {"type": "array", "items": {"type": "string"}, "description": "Table names (max 20)"},
            },
            "required": ["source", "tables"],
            "additionalProperties": False,
        },
    },
    {
        "name": "run_sql",
        "description": (
            "Run ONE read-only query in the source's SQL dialect. Returns a result id (like R4f9a1c) and a preview of "
            f"up to {PREVIEW_ROWS} rows. Aggregate in SQL (GROUP BY, date_trunc) instead of fetching raw rows; "
            "keep results under a few hundred rows so they chart well. Date columns are real dates: filter with "
            "date literals (month >= DATE '2025-01-01'); in PostgreSQL use ROUND(x::numeric, 2)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "source": {"type": "string", "description": "Data source id from list_data_sources"},
                "query": {"type": "string", "description": "A single SELECT statement"},
            },
            "required": ["source", "query"],
            "additionalProperties": False,
        },
    },
    {
        "name": "analyze_spreadsheet",
        "description": (
            "Calculate business insights for one spreadsheet table: overall trend, peak and low periods, biggest dips, "
            "leading and fastest-growing segments, related measures, outliers, missing data. Also shows charts. "
            "Use it first whenever someone asks to analyze a sheet or wants insights about it."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"table": {"type": "string", "description": "Table name from list_data_sources (source 'uploads')"}},
            "required": ["table"],
            "additionalProperties": False,
        },
    },
    {
        "name": "create_chart",
        "description": (
            "Show a query result as a chart the user can pin to their dashboard. Pick the form by the job: "
            "'line' for change over time (x = date), 'bar' to compare categories, 'number' for one headline value "
            "(KPI), 'table' when exact values matter. Use one chart per insight; never two measures with different "
            "units in one chart."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "result_id": {"type": "string", "description": "Result id from run_sql in this or an earlier turn, e.g. R4f9a1c"},
                "type": {"type": "string", "enum": ["line", "bar", "number", "table"]},
                "title": {"type": "string", "description": "Plain-language title stating what it shows"},
                "x": {"type": "string", "description": "Column for the x-axis / categories (not needed for number)"},
                "y": {"type": "array", "items": {"type": "string"},
                      "description": "Numeric column(s) to plot, max 4 series. For 'number', the single value column."},
            },
            "required": ["result_id", "type", "title", "y"],
            "additionalProperties": False,
        },
    },
]

SERVER_TOOLS = [
    {"type": "web_search_20250305", "name": "web_search", "max_uses": 5},
    {"type": "code_execution_20260521", "name": "code_execution"},
]

TOOLS = [{**t, "eager_input_streaming": True} for t in CLIENT_TOOLS] + SERVER_TOOLS


HYBRID_SQL = """
WITH scoped AS (
  SELECT c.* FROM chunks c JOIN documents d ON d.id = c.document_id
  LEFT JOIN connections cn ON cn.id = d.connection_id
  WHERE (%(source)s::text IS NULL OR d.source = %(source)s)
    AND (d.allowed IS NULL OR d.allowed && %(p)s::text[])
    AND (cn.allowed IS NULL OR cn.allowed && %(p)s::text[])
), sem AS (
  SELECT id, row_number() OVER (ORDER BY embedding <=> %(v)s) AS r
  FROM scoped ORDER BY embedding <=> %(v)s LIMIT 30
), kw AS (
  SELECT id, row_number() OVER (ORDER BY ts_rank(tsv, q) DESC) AS r
  FROM scoped, websearch_to_tsquery('english', %(q)s) q WHERE tsv @@ q ORDER BY r LIMIT 30
)
SELECT c.id, d.name, c.page, c.content, d.source, d.url, d.updated_at, d.id, d.replaced_at, d.change_note,
       coalesce(1.0/(60+sem.r), 0) + coalesce(1.0/(60+kw.r), 0) AS score
FROM sem FULL JOIN kw USING (id)
JOIN chunks c ON c.id = coalesce(sem.id, kw.id)
JOIN documents d ON d.id = c.document_id
ORDER BY score DESC LIMIT %(k)s
"""


def search_documents(query: str, k: int = 6, source: str | None = None, email: str | None = None) -> list[dict]:
    """Reciprocal-rank fusion of pgvector cosine and Postgres full-text search, limited to what `email` may see.

    email=None sees only content visible to everyone (no restricted documents)."""
    k = max(1, min(int(k), 15))
    with connect() as conn:
        rows = conn.execute(HYBRID_SQL, {"v": embed([query])[0], "q": query, "k": k, "source": source,
                                         "p": principals(email)}).fetchall()
    keys = ["chunk_id", "document", "page", "content", "source", "url", "updated_at", "document_id", "replaced_at", "change_note"]
    return [dict(zip(keys, r)) for r in rows]


def register_source(hit: dict, sources: list[dict]) -> dict:
    """Give a retrieved chunk a stable citation label ([S1], [S2]...) for this run."""
    existing = next((s for s in sources if s.get("chunk_id") == hit["chunk_id"]), None)
    if not existing:
        existing = {**hit, "updated_at": str(hit["updated_at"] or ""), "label": f"S{len(sources) + 1}", "kind": "doc"}
        sources.append(existing)
    return existing


@dataclass
class Ctx:
    """Per-run state shared by tool calls."""
    email: str | None = None
    admin: bool = False
    sources: list[dict] = field(default_factory=list)  # citable passages
    results: dict[str, dict] = field(default_factory=dict)  # R1 -> {source, sql, columns, rows}
    charts: list[dict] = field(default_factory=list)


def _jsonable(v):
    """DB values → JSON. Decimals (SUM/AVG in Postgres, NUMERIC columns) stay numbers so they can be charted."""
    if v is None or isinstance(v, (int, float, str, bool)):
        return v
    if isinstance(v, Decimal):
        return int(v) if v == v.to_integral_value() and abs(v) < 2**53 else float(v)
    if hasattr(v, "isoformat"):  # date / datetime / time
        return v.isoformat()
    return str(v)


def build_chart(result: dict, spec: dict) -> dict:
    """Validate a chart spec against its result. Raises ValueError with a message the model can act on."""
    cols = result["columns"]
    kind, x, ys = spec["type"], spec.get("x"), spec["y"][:4]
    missing = [c for c in [x, *ys] if c and c not in cols]
    if missing:
        raise ValueError(f"Columns {missing} not in result. Available: {cols}")
    if kind in ("line", "bar") and not x:
        raise ValueError("Line and bar charts need an x column")
    if kind == "number" and len(ys) != 1:
        raise ValueError("A number chart shows exactly one y column")
    if kind != "table":
        for y in ys:
            idx = cols.index(y)
            if not all(r[idx] is None or isinstance(r[idx], (int, float)) for r in result["rows"]):
                raise ValueError(f"Column {y!r} isn't numeric; cast it in SQL or pick another column")
    return {"type": kind, "title": spec["title"][:120], "x": x, "y": ys, "source": result["source"], "sql": result["sql"],
            "columns": cols, "rows": result["rows"], "truncated": result.get("truncated", False)}


DATE_TYPES = ("date", "timestamp without time zone", "timestamp with time zone")


def _date_coverage(source: str, tables: dict, ctx: Ctx) -> dict:
    """First and last date in each date column, so comparisons can be like-for-like (the latest year is often partial)."""
    out = {}
    for t, cols in tables.items():
        for c, typ in cols.items():
            if typ in DATE_TYPES:
                q = f'SELECT min("{c}"), max("{c}") FROM "{t}"'
                try:
                    lo, hi = datasources.run(source, q, ctx.email, ctx.admin, max_rows=1)["rows"][0]
                    out[f"{t}.{c}"] = {"from": str(lo)[:10], "to": str(hi)[:10]}
                except Exception:
                    pass
    return out


def _reuse(rid: str, ctx: Ctx) -> dict | None:
    """A query from an earlier turn: re-run its SQL (with the asker's current access) instead of trusting old rows."""
    with connect() as conn:
        row = conn.execute("SELECT source, sql FROM query_results WHERE id = %s AND email IS NOT DISTINCT FROM %s",
                           (rid, ctx.email)).fetchone()
    if not row:
        return None
    result = datasources.run(row[0], row[1], ctx.email, ctx.admin)
    ctx.results[rid] = {**result, "rows": [[_jsonable(v) for v in r] for r in result["rows"]], "source": row[0], "sql": row[1]}
    return ctx.results[rid]


_ID_CITATION = re.compile(r"\s?\[(?:R[0-9a-f]{6}|C\d+)(?:\s*,\s*(?:R[0-9a-f]{6}|C\d+))*\]")


def strip_ids(text: str) -> str:
    """Remove internal query/chart ids the model sometimes cites, e.g. "[Rd7ea5b]"; users can't use them."""
    return _ID_CITATION.sub("", text or "")


def memory_note(ctx: Ctx) -> str:
    """Hidden note appended to the answer in conversation history: the queries behind it, reusable next turn."""
    if not ctx.results:
        return ""
    lines = [f"- {rid}: {' '.join(r['sql'].split())[:400]} (source {r['source']}, {len(r['rows'])} rows)" for rid, r in ctx.results.items()]
    return "[Queries behind this answer, reusable with create_chart or as a starting point:\n" + "\n".join(lines) + "]"


def execute(name: str, args: dict, ctx: Ctx) -> str:
    """Run a client tool for the current user. Raises on bad input; the agent returns errors to the model."""
    if name == "search_documents":
        hits = search_documents(args["query"], args.get("k", 6), args.get("source"), ctx.email)
        widened = ""
        if not hits and args.get("source"):  # a filter on an empty or wrong source shouldn't hide the answer
            hits = search_documents(args["query"], args.get("k", 6), None, ctx.email)
            widened = f"(Nothing matched in {args['source']}, so these results are from all sources.)\n"
        if not hits:
            return "No matching passages in any source. Try a different query, or use web_search."
        parts = [widened] if widened else []
        for h in hits:
            existing = register_source(h, ctx.sources)
            parts.append(
                f'<passage label="{existing["label"]}" source="{h["source"]}" document="{h["document"]}" '
                f'page="{h["page"]}" updated="{existing["updated_at"][:10]}">\n'
                f'{h["content"]}\n</passage>'
            )
        # quiet context: if a document was replaced, say what changed so answers rely on (and can explain) the new version
        noted = {}
        for h in hits:
            if h.get("change_note") and h["document_id"] not in noted:
                noted[h["document_id"]] = (f'<recent_change document="{h["document"]}" replaced="{str(h["replaced_at"])[:10]}">\n'
                                           f'{h["change_note"]}\n</recent_change>')
        return "\n".join(parts + list(noted.values()))

    if name == "list_data_sources":
        out = []
        titles = tableacl.visible_tables(ctx.email)
        for src in datasources.available(ctx.email, ctx.admin):
            try:
                names = list(datasources.list_tables(src["id"], ctx.email, ctx.admin))[:200]
                # spreadsheets: show which file/tab each table came from, so "the Q3 forecast" can be matched
                tables = [{"name": n, "from": titles[n]["title"],
                           **({"recently_replaced": {"on": str(titles[n]["replaced_at"])[:10], "changes": titles[n]["change_note"]}}
                              if titles[n].get("change_note") else {})}
                          for n in names] if src["id"] == datasources.UPLOADS else names
            except Exception as e:  # one broken warehouse shouldn't hide the others
                tables = f"unavailable: {str(e)[:150]}"
            out.append({**src, "tables": tables})
        return json.dumps(out)

    if name == "describe_tables":
        schema = datasources.list_tables(args["source"], ctx.email, ctx.admin)
        wanted = args["tables"][:20]
        found = {t: schema[t] for t in wanted if t in schema}
        unknown = [t for t in wanted if t not in schema]
        coverage = _date_coverage(args["source"], found, ctx) if args["source"] == datasources.UPLOADS else {}
        return json.dumps({"tables": found, **({"date_coverage": coverage} if coverage else {}),
                           **({"unknown": unknown} if unknown else {})}, default=str)

    if name == "run_sql":
        result = datasources.run(args["source"], args["query"], ctx.email, ctx.admin)
        rid = f"R{secrets.token_hex(3)}"  # unique across the conversation, so follow-up turns can reuse it
        with connect() as conn:
            conn.execute("INSERT INTO query_results (id, email, source, sql) VALUES (%s, %s, %s, %s)",
                         (rid, ctx.email, args["source"], args["query"]))
        rows = [[_jsonable(v) for v in r] for r in result["rows"]]
        ctx.results[rid] = {**result, "rows": rows, "source": args["source"], "sql": args["query"]}
        preview = {"result_id": rid, "columns": result["columns"], "row_count": len(rows),
                   "rows": rows[:PREVIEW_ROWS], "truncated": result.get("truncated", False)}
        if result.get("note"):
            preview["note"] = result["note"]
        return json.dumps(preview, default=str)

    if name == "analyze_spreadsheet":
        from . import analysis  # late import: analysis imports this module

        a = analysis.analyze(args["table"], ctx.email)
        for chart in a["charts"]:
            chart["id"] = f"C{len(ctx.charts) + 1}"
            ctx.charts.append(chart)
        meta = tableacl.visible_tables(ctx.email).get(args["table"], {})
        change = {"recent_change": {"replaced": str(meta["replaced_at"])[:10], "changes": meta["change_note"]}} if meta.get("change_note") else {}
        return json.dumps({**change, "spreadsheet": a["title"], "rows": a["rows"], "main_measure": a["metric"], "time_column": a["time"],
                           "segment_column": a["segment"], "findings": a["findings"],
                           "charts_shown": [c["title"] for c in a["charts"]]}, default=str)

    if name == "create_chart":
        result = ctx.results.get(args["result_id"]) or _reuse(args["result_id"], ctx)
        if not result:
            raise ValueError(f"Unknown result_id {args['result_id']}. Run the query again with run_sql and use the id it returns.")
        chart = build_chart(result, args)
        chart["id"] = f"C{len(ctx.charts) + 1}"
        ctx.charts.append(chart)
        return "The chart is now shown to the user. Refer to it as \"the chart\" (never by an id) and explain the key takeaway; don't repeat all its numbers."

    raise ValueError(f"Unknown tool {name}")
