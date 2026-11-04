"""Tool layer. Client tools run here; web search and code execution run server-side at Anthropic."""
import json

from dataclasses import dataclass, field
from decimal import Decimal

from . import datasources, tableacl
from .db import connect
from .ingest import embed

PREVIEW_ROWS = 50

CLIENT_TOOLS = [
    {
        "name": "search_documents",
        "description": (
            "Hybrid (semantic + keyword) search over the company knowledge base: uploaded files plus synced "
            "Slack, Google Drive, Notion, GitHub and crawled websites. Only returns what the current user may see. "
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
                    "description": "Optional: only search one source, e.g. 'slack' for team discussions",
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
            "Run ONE read-only query in the source's SQL dialect. Returns a result id (R1, R2...) and a preview of "
            f"up to {PREVIEW_ROWS} rows. Aggregate in SQL (GROUP BY, date_trunc) instead of fetching raw rows; "
            "keep results under a few hundred rows so they chart well."
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
                "result_id": {"type": "string", "description": "Result id from run_sql, e.g. R1"},
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
    AND (d.allowed IS NULL OR %(email)s = ANY(d.allowed))
    AND (cn.allowed IS NULL OR %(email)s = ANY(cn.allowed))
), sem AS (
  SELECT id, row_number() OVER (ORDER BY embedding <=> %(v)s) AS r
  FROM scoped ORDER BY embedding <=> %(v)s LIMIT 30
), kw AS (
  SELECT id, row_number() OVER (ORDER BY ts_rank(tsv, q) DESC) AS r
  FROM scoped, websearch_to_tsquery('english', %(q)s) q WHERE tsv @@ q ORDER BY r LIMIT 30
)
SELECT c.id, d.name, c.page, c.content, d.source, d.url, d.updated_at,
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
                                         "email": email.lower() if email else None}).fetchall()
    keys = ["chunk_id", "document", "page", "content", "source", "url", "updated_at"]
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


def execute(name: str, args: dict, ctx: Ctx) -> str:
    """Run a client tool for the current user. Raises on bad input; the agent returns errors to the model."""
    if name == "search_documents":
        hits = search_documents(args["query"], args.get("k", 6), args.get("source"), ctx.email)
        if not hits:
            return "No matching passages. Try a different query, or use web_search."
        parts = []
        for h in hits:
            existing = register_source(h, ctx.sources)
            parts.append(
                f'<passage label="{existing["label"]}" source="{h["source"]}" document="{h["document"]}" '
                f'page="{h["page"]}" updated="{existing["updated_at"][:10]}">\n'
                f'{h["content"]}\n</passage>'
            )
        return "\n".join(parts)

    if name == "list_data_sources":
        out = []
        titles = tableacl.visible_tables(ctx.email)
        for src in datasources.available(ctx.email, ctx.admin):
            try:
                names = list(datasources.list_tables(src["id"], ctx.email, ctx.admin))[:200]
                # spreadsheets: show which file/tab each table came from, so "the Q3 forecast" can be matched
                tables = [{"name": n, "from": titles[n]["title"]} for n in names] if src["id"] == datasources.UPLOADS else names
            except Exception as e:  # one broken warehouse shouldn't hide the others
                tables = f"unavailable: {str(e)[:150]}"
            out.append({**src, "tables": tables})
        return json.dumps(out)

    if name == "describe_tables":
        schema = datasources.list_tables(args["source"], ctx.email, ctx.admin)
        wanted = args["tables"][:20]
        found = {t: schema[t] for t in wanted if t in schema}
        unknown = [t for t in wanted if t not in schema]
        return json.dumps({"tables": found, **({"unknown": unknown} if unknown else {})})

    if name == "run_sql":
        result = datasources.run(args["source"], args["query"], ctx.email, ctx.admin)
        rid = f"R{len(ctx.results) + 1}"
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
        return json.dumps({"spreadsheet": a["title"], "rows": a["rows"], "main_measure": a["metric"], "time_column": a["time"],
                           "segment_column": a["segment"], "findings": a["findings"],
                           "charts_shown": [c["title"] for c in a["charts"]]}, default=str)

    if name == "create_chart":
        result = ctx.results.get(args["result_id"])
        if not result:
            raise ValueError(f"Unknown result_id {args['result_id']}. Use an id returned by run_sql.")
        chart = build_chart(result, args)
        chart["id"] = f"C{len(ctx.charts) + 1}"
        ctx.charts.append(chart)
        return f"Chart {chart['id']} shown to the user. Refer to it in your answer; don't repeat all its numbers."

    raise ValueError(f"Unknown tool {name}")
