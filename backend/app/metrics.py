"""Metric definitions (semantic layer): named aggregates the assistant, alerts and people reuse.

Expressions and filters are tokenized and rebuilt: only the table's real columns, a few functions, numbers,
arithmetic (and, in filters, comparisons and string literals) get through. Queries always run as the asker.
"""
import re
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from . import audit, datasources
from .db import connect
from .security import User, current_user, require_admin

router = APIRouter()

AGGREGATES = {"sum", "avg", "count", "min", "max"}
EXPR_FUNCS = AGGREGATES | {"round", "coalesce", "nullif"}
FILTER_FUNCS = {"round", "coalesce", "nullif", "lower", "upper"}
FILTER_WORDS = {"and", "or", "not", "in", "is", "null", "between", "like", "ilike", "true", "false", "date"}
PERIODS = ("day", "week", "month", "quarter", "year")
TOKEN = re.compile(r"""\s*(?:(?P<num>\d+(?:\.\d+)?)|(?P<str>'(?:[^']|'')*')|(?P<qid>"(?:[^"]|"")+")|"""
                   r"""(?P<id>[A-Za-z_][A-Za-z0-9_]*)|(?P<op><=|>=|<>|!=|[-+*/(),<>=%]))""")
COMPARE = {"<=", ">=", "<>", "!=", "<", ">", "="}


def _tokens(text: str) -> list[tuple[str, str]]:
    if "--" in text or "/*" in text or ";" in text:
        raise ValueError("Comments and semicolons aren't allowed")
    out, pos, text = [], 0, text.strip()
    while pos < len(text):
        m = TOKEN.match(text, pos)
        if not m or m.end() == pos:
            raise ValueError(f"Unexpected text near {text[pos:pos + 20]!r}")
        out.append((m.lastgroup, m.group(m.lastgroup)))
        pos = m.end()
    return out


def _column(name: str, columns: list[str]) -> str:
    if name in columns:
        return name
    hits = [c for c in columns if c.lower() == name.lower()]
    if len(hits) != 1:
        raise ValueError(f"Unknown column {name!r}. Columns: {', '.join(columns[:40])}")
    return hits[0]


def quote(name: str, kind: str) -> str:
    if kind == "bigquery":
        if "`" in name:
            raise ValueError("Invalid column name")
        return f"`{name}`"
    return '"' + name.replace('"', '""') + '"'


def _rebuild(text: str, columns: list[str], kind: str, funcs: set, words: set, allow_strings: bool) -> str:
    toks, out, depth = _tokens(text), [], 0
    for i, (typ, val) in enumerate(toks):
        nxt_typ, nxt = toks[i + 1] if i + 1 < len(toks) else ("", "")
        if typ == "id" and val.lower() in funcs and nxt == "(":
            out.append(val.lower())
        elif typ == "id" and val.lower() == "date" and "date" in words:  # DATE '2026-01-01', else a column named date
            out.append("DATE" if nxt_typ == "str" else quote(_column(val, columns), kind))
        elif typ == "id" and val.lower() in words | {"distinct"}:
            out.append(val.upper())
        elif typ in ("id", "qid"):
            name = val[1:-1].replace('""', '"') if typ == "qid" else val
            if nxt == "(":
                raise ValueError(f"Function {name!r} isn't allowed. Use {', '.join(sorted(funcs))}")
            out.append(quote(_column(name, columns), kind))
        elif typ == "str":
            if not allow_strings:
                raise ValueError("Text values aren't allowed in a metric expression")
            out.append(val)
        elif typ == "op":
            if val in COMPARE and not allow_strings:
                raise ValueError("Comparisons aren't allowed in a metric expression; put them in the filter")
            depth += {"(": 1, ")": -1}.get(val, 0)
            if depth < 0:
                raise ValueError("Unbalanced parentheses")
            out.append(val)
        else:
            out.append(val)
    if depth:
        raise ValueError("Unbalanced parentheses")
    return " ".join(out)


def clean_expression(expr: str, columns: list[str], kind: str = "postgres") -> str:
    """`sum(revenue) - sum(cost)` → safe SQL, or ValueError."""
    if not any(t == "id" and v.lower() in AGGREGATES for t, v in _tokens(expr)):
        raise ValueError("A metric needs an aggregate such as sum(...), avg(...) or count(*)")
    return _rebuild(expr, columns, kind, EXPR_FUNCS, set(), allow_strings=False)


def clean_filter(pred: str, columns: list[str], kind: str = "postgres") -> str:
    return _rebuild(pred, columns, kind, FILTER_FUNCS, FILTER_WORDS, allow_strings=True)


def kind_of(source: str, email: str | None) -> str:
    if source == datasources.UPLOADS:
        return "postgres"
    src = next((s for s in datasources.available(email) if s["id"] == source), None)
    if not src:
        raise datasources.AccessDenied(f"No access to data source {source}")
    return src["kind"]


def _table(name: str, source: str, kind: str) -> str:
    if source == datasources.UPLOADS:
        return quote(name, kind)
    if kind == "bigquery":
        return quote(name, kind)  # `project.dataset.table`
    return ".".join(quote(p, kind) for p in name.split("."))


def _bucket(col: str, period: str, kind: str) -> str:
    if kind == "bigquery":
        return f"DATE_TRUNC(DATE({col}), {'WEEK(MONDAY)' if period == 'week' else period.upper()})"
    return f"date_trunc('{period}', {col})::date"


def schema_for(source: str, table: str, email: str | None) -> list[str]:
    tables = datasources.list_tables(source, email)
    if table not in tables:
        raise ValueError(f"Table {table!r} isn't in that data source (or you can't see it)")
    return list(tables[table])


def alias(name: str) -> str:
    return re.sub(r"\W+", "_", name.lower()).strip("_")[:50] or "value"


def build_sql(m: dict, columns: list[str], kind: str, group_by: str | None = None, period: str | None = None,
              start: str | None = None, end: str | None = None, extra_where: list[str] | None = None) -> str:
    """m: {name, source, table_name, expression, time_column, filters}. Every piece is validated or rebuilt."""
    expr = clean_expression(m["expression"], columns, kind)
    time = quote(_column(m["time_column"], columns), kind) if m.get("time_column") else None
    select, where = [], [f"({clean_filter(m['filters'], columns, kind)})"] if m.get("filters") else []
    if period:
        if period not in PERIODS:
            raise ValueError(f"period must be one of {', '.join(PERIODS)}")
        if not time:
            raise ValueError("This metric has no time column, so it can't be broken down by period")
        select.append(f"{_bucket(time, period, kind)} AS period")
    if group_by:
        select.append(quote(_column(group_by, columns), kind))
    for op, d in ((">=", start), ("<", end)):
        if d:
            if not time:
                raise ValueError("This metric has no time column, so it can't be limited to dates")
            where.append(f"{time} {op} DATE '{date.fromisoformat(d.strip()).isoformat()}'")
    where += extra_where or []
    keys = ", ".join(str(i + 1) for i in range(len(select)))
    sql = f"SELECT {', '.join([*select, f'{expr} AS {quote(alias(m['name']), kind)}'])} FROM {_table(m['table_name'], m['source'], kind)}"
    if where:
        sql += " WHERE " + " AND ".join(where)
    if select:
        sql += f" GROUP BY {keys} ORDER BY {'1' if period else f'{len(select) + 1} DESC'}"
    return sql


def get(name: str) -> dict | None:
    with connect() as conn:
        row = conn.execute(f"SELECT {COLS} FROM metrics WHERE lower(name) = lower(%s) ORDER BY id LIMIT 1", (name,)).fetchone()
    return _row(row) if row else None


COLS = "id, name, description, source, table_name, expression, time_column, filters, format, updated_at"
KEYS = COLS.split(", ")


def _row(r) -> dict:
    return dict(zip(KEYS, r))


def visible(email: str | None) -> list[dict]:
    """Metrics on data sources this person can query."""
    ids = {s["id"] for s in datasources.available(email)}
    with connect() as conn:
        rows = conn.execute(f"SELECT {COLS} FROM metrics ORDER BY name").fetchall()
    return [m for m in map(_row, rows) if m["source"] in ids]


def query(m: dict, email: str | None, **kw) -> tuple[str, dict]:
    """Build and run a metric as `email` → (sql, result)."""
    kind = kind_of(m["source"], email)
    sql = build_sql(m, schema_for(m["source"], m["table_name"], email), kind, **kw)
    return sql, datasources.run(m["source"], sql, email)


# ---------- API ----------

class MetricIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    description: str = Field("", max_length=1000)
    source: str = Field(max_length=50)
    table_name: str = Field(min_length=1, max_length=300)
    expression: str = Field(min_length=1, max_length=1000)
    time_column: str | None = Field(None, max_length=200)
    filters: str | None = Field(None, max_length=1000)
    format: str = Field("number", pattern="^(currency|number|percent)$")


def _validate(body: MetricIn, user: User) -> dict:
    m = body.model_dump()
    m["time_column"], m["filters"] = m["time_column"] or None, (m["filters"] or "").strip() or None
    try:
        build_sql(m, schema_for(m["source"], m["table_name"], user.email), kind_of(m["source"], user.email))
    except datasources.AccessDenied as e:
        raise HTTPException(403, str(e))
    except Exception as e:
        raise HTTPException(422, str(e)[:300])
    return m


@router.get("/api/metrics/catalog")
def catalog(user: User = Depends(current_user)):
    """Data sources, tables and columns this person can see, for building metrics and alerts."""
    out = []
    for src in datasources.available(user.email):
        try:
            tables = {t: list(c) for t, c in datasources.list_tables(src["id"], user.email).items()}
        except Exception:
            continue  # a broken warehouse shouldn't break the form
        out.append({"id": src["id"], "name": src["name"], "tables": tables})
    return out


@router.get("/api/metrics")
def list_metrics(user: User = Depends(current_user)):
    with connect() as conn:
        rows = conn.execute(f"SELECT {', '.join('m.' + c for c in KEYS)}, u.email FROM metrics m "
                            "LEFT JOIN users u ON u.id = m.owner_id ORDER BY m.name").fetchall()
    return [{**_row(r[:-1]), "owner": r[-1]} for r in rows]


@router.post("/api/metrics")
def create_metric(body: MetricIn, user: User = Depends(require_admin)):
    m = _validate(body, user)
    with connect() as conn:
        if conn.execute("SELECT 1 FROM metrics WHERE lower(name) = lower(%s)", (m["name"],)).fetchone():
            raise HTTPException(409, "A metric with that name already exists")
        mid = conn.execute("INSERT INTO metrics (name, description, source, table_name, expression, time_column, filters, format, owner_id) "
                           "VALUES (%(name)s, %(description)s, %(source)s, %(table_name)s, %(expression)s, %(time_column)s, %(filters)s, "
                           "%(format)s, %(owner)s) RETURNING id", {**m, "owner": user.id}).fetchone()[0]
    audit.record("metric.create", user, f"metric:{mid}", {"name": m["name"]})
    return {"id": mid}


@router.put("/api/metrics/{metric_id}")
def update_metric(metric_id: int, body: MetricIn, user: User = Depends(require_admin)):
    m = _validate(body, user)
    with connect() as conn:
        if conn.execute("SELECT 1 FROM metrics WHERE lower(name) = lower(%s) AND id <> %s", (m["name"], metric_id)).fetchone():
            raise HTTPException(409, "A metric with that name already exists")
        done = conn.execute("UPDATE metrics SET name = %(name)s, description = %(description)s, source = %(source)s, "
                            "table_name = %(table_name)s, expression = %(expression)s, time_column = %(time_column)s, "
                            "filters = %(filters)s, format = %(format)s, updated_at = now() WHERE id = %(id)s RETURNING id",
                            {**m, "id": metric_id}).fetchone()
    if not done:
        raise HTTPException(404, "Metric not found")
    audit.record("metric.update", user, f"metric:{metric_id}", {"name": m["name"]})
    return {"ok": True}


@router.delete("/api/metrics/{metric_id}")
def delete_metric(metric_id: int, user: User = Depends(require_admin)):
    with connect() as conn:
        done = conn.execute("DELETE FROM metrics WHERE id = %s RETURNING name", (metric_id,)).fetchone()
    if not done:
        raise HTTPException(404, "Metric not found")
    audit.record("metric.delete", user, f"metric:{metric_id}", {"name": done[0]})
    return {"ok": True}


@router.post("/api/metrics/{metric_id}/test")
def test_metric(metric_id: int, user: User = Depends(current_user)):
    """Current value, computed with the viewer's own access."""
    with connect() as conn:
        row = conn.execute(f"SELECT {COLS} FROM metrics WHERE id = %s", (metric_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Metric not found")
    try:
        sql, res = query(_row(row), user.email)
    except datasources.AccessDenied as e:
        raise HTTPException(403, str(e))
    except Exception as e:
        raise HTTPException(422, str(e)[:300])
    from .tools import _jsonable

    return {"value": _jsonable(res["rows"][0][0]) if res["rows"] else None, "sql": sql}
