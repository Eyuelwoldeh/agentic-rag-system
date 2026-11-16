"""Spreadsheet analysis: computed findings + charts, no AI needed.

The numbers are calculated here (exact, reproducible); an AI model, when available, only turns them into a
narrative and digs further. That also means "Analyze" still works when no AI provider is reachable.
"""
import math
import re
import statistics
from collections import defaultdict
from datetime import date, datetime

from psycopg import sql as pgsql

from . import datasources, tableacl
from .tools import _jsonable, build_chart

MAX_ROWS = 50_000
NUMERIC = ("bigint", "integer", "smallint", "double precision", "numeric", "real")  # spreadsheets use bigint / numeric
METRIC_HINT = re.compile(r"revenue|sales|amount|total|profit|cost|price|value|spend|income|margin", re.I)  # money first
COUNT_HINT = re.compile(r"units|qty|quantity|orders|count|transactions", re.I)
TIME_HINT = re.compile(r"month|date|day|week|period|time|created|year|quarter", re.I)
YEAR_MONTH = re.compile(r"^\d{4}-\d{2}$")
SEGMENT_HINT = re.compile(r"region|country|state|city|market|segment|channel|category|team|department|customer|client|"
                          r"product|plan|tier|store|location|source|campaign|rep|owner|type", re.I)


def compact(v: float) -> str:
    a = abs(v)
    for div, suf in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if a >= div:
            return f"{v / div:.1f}".rstrip("0").rstrip(".") + suf
    return f"{v:,.2f}".rstrip("0").rstrip(".") if isinstance(v, float) and not v.is_integer() else f"{v:,.0f}"


def pct(a: float, b: float) -> str | None:
    if not a:
        return None
    return f"{(b - a) / abs(a) * 100:+.1f}%"


def _q(name: str) -> str:
    return pgsql.Identifier(name).as_string(None)


def _id_like(name: str, values: list) -> bool:
    if re.search(r"(^id$|_id$|^id_|number$|_no$|^no$|zip|phone|year$)", name, re.I):
        return True
    ints = [v for v in values if isinstance(v, int)]
    return len(ints) == len(values) > 10 and len(set(ints)) == len(ints) and sorted(ints) == list(range(min(ints), min(ints) + len(ints)))


def analyze(table: str, email: str | None) -> dict:
    """→ {title, rows, columns, findings: [{headline, detail, kind}], charts: [chart], metric, time, segment}"""
    visible = tableacl.visible_tables(email)
    if table not in visible:
        raise datasources.AccessDenied(f"No spreadsheet named {table!r} that you can see")
    schema = datasources.list_tables(datasources.UPLOADS, email)[table]
    res = datasources.run(datasources.UPLOADS, f"SELECT * FROM {_q(table)} LIMIT {MAX_ROWS}", email, max_rows=MAX_ROWS)
    cols, rows = res["columns"], res["rows"]
    col = {c: [r[i] for r in rows] for i, c in enumerate(cols)}
    n = len(rows)
    title = visible[table]["title"]
    findings: list[dict] = []
    if not n:
        return {"title": title, "rows": 0, "columns": cols, "findings": [{"headline": "The sheet is empty", "detail": "", "kind": "quality"}], "charts": []}

    def nums(c):
        return [float(v) for v in col[c] if v is not None]

    numeric = [c for c in cols if schema.get(c) in NUMERIC and not _id_like(c, [v for v in col[c] if v is not None])]
    timecols = [c for c in cols if schema.get(c) in ("date", "timestamp without time zone", "timestamp with time zone")
                or (schema.get(c) == "text" and all(YEAR_MONTH.match(str(v)) for v in col[c] if v is not None) and any(col[c]))]
    texts = [c for c in cols if schema.get(c) == "text" and c not in timecols]

    metric = (next((c for c in numeric if METRIC_HINT.search(c)), None) or next((c for c in numeric if COUNT_HINT.search(c)), None)
              or max(numeric, key=lambda c: abs(sum(nums(c))), default=None))
    time = next((c for c in timecols if TIME_HINT.search(c)), timecols[0] if timecols else None)
    def seg_score(c):  # business-meaningful names first, then a readable number of groups (3-12 is ideal)
        k = len({v for v in col[c] if v is not None})
        return (0 if SEGMENT_HINT.search(c) else 1, 0 if 3 <= k <= 12 else 1, -k)
    segs = sorted([c for c in texts if 2 <= len({v for v in col[c] if v is not None}) <= 25], key=seg_score)
    segment = segs[0] if segs else None

    # --- overview
    span = ""
    if time:
        tv = sorted(v for v in col[time] if v is not None)
        span = f", covering {_label(tv[0])} to {_label(tv[-1])}"
    findings.append({"headline": f"{n:,} rows × {len(cols)} columns{span}", "kind": "overview",
                     "detail": f"Main measure: {metric or 'none found'}" + (f"; broken down by {segment}" if segment else "")})

    # --- trend over time
    series: list[tuple] = []
    if time and metric:
        by = defaultdict(float)
        monthly = _span_days(col[time]) > 62
        for t, v in zip(col[time], col[metric]):
            if t is not None and v is not None:
                by[_period(t, monthly)] += float(v)
        series = sorted(by.items())
        if len(series) >= 2:
            (p0, v0), (p1, v1) = series[0], series[-1]
            best, worst = max(series, key=lambda x: x[1]), min(series, key=lambda x: x[1])
            ups = sum(1 for a, b in zip(series, series[1:]) if b[1] > a[1])
            findings.append({"kind": "trend",
                             "headline": f"{metric} went from {compact(v0)} to {compact(v1)} ({pct(v0, v1) or 'n/a'}) between {p0} and {p1}",
                             "detail": f"Peak: {best[0]} ({compact(best[1])}). Low: {worst[0]} ({compact(worst[1])}). "
                                       f"Up in {ups} of {len(series) - 1} periods."})
            worst_drop = min(_drops(dict(series)), key=lambda d: d[2], default=None)
            if worst_drop and worst_drop[2] < -0.02:
                findings.append({"kind": "anomaly", "headline": f"Biggest drop: {worst_drop[0]} ({worst_drop[2] * 100:+.1f}% vs {worst_drop[1]})",
                                 "detail": "Worth checking what happened then: a pricing change, an outage, a lost customer?"})
            if len(series) >= 3:
                last, prev = series[-1][1], series[-2][1]
                findings.append({"kind": "trend", "headline": f"Latest period ({series[-1][0]}): {compact(last)}, {pct(prev, last) or 'n/a'} vs the period before",
                                 "detail": ""})

    # --- segments: who contributes most, who grows fastest, where totals hide a dip (top 2 breakdowns)
    chosen = []
    for seg in segs:  # top 2 breakdowns, skipping one that just regroups another (e.g. channel derived from location)
        if len(chosen) == 2 or not metric:
            break
        if any(_determines(col[c], col[seg]) for c in chosen):
            continue
        chosen.append(seg)
        findings += _segment_findings(seg, metric, time, series, col)

    # --- no time or category column: still give the basics, and say what would unlock more
    if metric and not time and not segs:
        vals = nums(metric)
        if len(vals) >= 2:
            first, last = vals[0], vals[-1]
            findings.append({"kind": "trend", "headline": f"{metric} ranges from {compact(min(vals))} to {compact(max(vals))} (average {compact(statistics.fmean(vals))})",
                             "detail": f"From the first row to the last it changes {pct(first, last) or 'n/a'} ({compact(first)} to {compact(last)}), assuming rows are in time order."})
        findings.append({"kind": "quality", "headline": "No date or category column",
                         "detail": "Add a date column (for example observation_date) to see trends over time, or a category such as region or product to see breakdowns."})

    # --- relationships between measures
    for i, a in enumerate(numeric):
        for b in numeric[i + 1:]:
            pairs = [(float(x), float(y)) for x, y in zip(col[a], col[b]) if x is not None and y is not None]
            if len(pairs) >= 6:
                r = _pearson(pairs)
                if r is not None and abs(r) >= 0.7:
                    findings.append({"kind": "relationship", "headline": f"{a} and {b} move {'together' if r > 0 else 'in opposite directions'} (r = {r:.2f})",
                                     "detail": "A relationship, not proof that one causes the other."})

    # --- outliers
    if metric:
        vals = nums(metric)
        if len(vals) >= 20:
            mu, sd = statistics.fmean(vals), statistics.pstdev(vals)
            if sd:
                idx = cols.index(metric)
                outs = [r for r in rows if r[idx] is not None and abs((float(r[idx]) - mu) / sd) > 3]
                if outs:
                    ex = max(outs, key=lambda r: abs(float(r[idx]) - mu))
                    label = next((str(v) for c, v in zip(cols, ex) if c in (segment, time) and v is not None), "")
                    findings.append({"kind": "anomaly", "headline": f"{len(outs)} unusual {metric} value{'s' if len(outs) > 1 else ''}",
                                     "detail": f"Most extreme: {compact(float(ex[idx]))}{f' ({label})' if label else ''}, vs a typical {compact(mu)}."})

    # --- data quality
    gaps = [(c, sum(v is None for v in col[c]) / n) for c in cols]
    bad = [f"{c} ({share:.0%} empty)" for c, share in gaps if share > 0.1]
    if bad:
        findings.append({"kind": "quality", "headline": "Missing values", "detail": ", ".join(bad[:5])})
    if res.get("truncated"):
        findings.append({"kind": "quality", "headline": f"Analyzed the first {MAX_ROWS:,} rows", "detail": ""})

    for f in findings:
        f["headline"] = f["headline"][:1].upper() + f["headline"][1:]
    return {"title": title, "rows": n, "columns": cols, "findings": findings, "metric": metric, "time": time, "segment": segment,
            "charts": _charts(table, title, metric, time, segment, _span_days(col[time]) > 62 if time else False, schema, email)}


def _segment_findings(segment, metric, time, series, col) -> list[dict]:
    out = []
    tot = defaultdict(float)
    for s_, v in zip(col[segment], col[metric]):
        if s_ is not None and v is not None:
            tot[s_] += float(v)
    ranked = sorted(tot.items(), key=lambda x: -x[1])
    grand = sum(v for _, v in ranked) or 1
    if len(ranked) >= 2:
        top, bottom = ranked[0], ranked[-1]
        out.append({"kind": "segment", "headline": f"{top[0]} leads {segment} with {compact(top[1])} ({top[1] / grand:.0%} of {metric})",
                    "detail": f"Smallest: {bottom[0]} ({compact(bottom[1])}, {bottom[1] / grand:.0%})."
                              + (f" Top 3 make up {sum(v for _, v in ranked[:3]) / grand:.0%}." if len(ranked) > 4 else "")})
    if not (time and len(series) >= 2):
        return out
    monthly = _span_days(col[time]) > 62
    per = defaultdict(lambda: defaultdict(float))
    for s_, t_, v_ in zip(col[segment], col[time], col[metric]):
        if s_ is not None and t_ is not None and v_ is not None:
            per[s_][_period(t_, monthly)] += float(v_)
    first_p, last_p = series[0][0], series[-1][0]
    growth = [(s_, (ser[last_p] - ser[first_p]) / abs(ser[first_p])) for s_, ser in per.items() if ser.get(first_p) and last_p in ser]
    if len(growth) >= 2:
        fast, slow = max(growth, key=lambda x: x[1]), min(growth, key=lambda x: x[1])
        if fast[1] - slow[1] >= 0.05:  # only a real gap is an insight
            out.append({"kind": "segment", "headline": f"Fastest growing {segment}: {fast[0]} ({fast[1] * 100:+.0f}% from {first_p} to {last_p})",
                        "detail": f"Slowest: {slow[0]} ({slow[1] * 100:+.0f}%)."})
    # lasting level shifts (a price increase, a lost customer): the cleanest before/after step within a group
    steps = sorted(filter(None, (_step(s_, ser) for s_, ser in per.items())), key=lambda x: -x[0])
    for _, g, period, before, after in steps[:2]:
        change = (after - before) / abs(before)
        out.append({"kind": "shift", "headline": f"{g} {'stepped up' if change > 0 else 'stepped down'} {abs(change) * 100:.0f}% from {period} and stayed there",
                    "detail": f"Average {compact(before)} per period before, {compact(after)} after. Usually a pricing, contract or customer change."})
    drops = sorted(((s_, *d) for s_, ser in per.items() for d in _drops(ser)), key=lambda d: d[3])  # dips totals hide
    reported = []
    for g, period, vs, change in drops:  # the two sharpest dips, from different groups
        if change > -0.05 or len(reported) == 2:
            break
        if g in reported:
            continue
        reported.append(g)
        out.append({"kind": "anomaly", "headline": f"{g} fell {change * -100:.1f}% in {period} (vs {vs})",
                    "detail": f"The overall total hides this. Worth asking what changed for {g} then."})
    return out


def _step(group, ser: dict[str, float]):
    """Best single step change in a series: (score, group, first period after, mean before, mean after) or None.
    Score = jump size relative to the noise on either side, so a clean +18% step beats a noisy or gradual trend."""
    pts = [v for _, v in sorted(ser.items())]
    keys = [k for k, _ in sorted(ser.items())]
    if len(pts) < 6:
        return None
    best = None
    for i in range(3, len(pts) - 2):  # at least 3 periods on each side
        a, b = pts[:i], pts[i:]
        ma, mb = statistics.fmean(a), statistics.fmean(b)
        if not ma:
            continue
        noise = (statistics.pstdev(a) + statistics.pstdev(b)) / 2 or abs(ma) * 0.01
        # a ramp has a big mean gap but also big spread on each side; a step has a gap with flat sides
        score = abs(mb - ma) / noise
        if abs(mb - ma) / abs(ma) >= 0.08 and score >= 3 and (best is None or score > best[0]):
            best = (score, group, keys[i], ma, mb)
    return best


def _determines(a: list, b: list) -> bool:
    """True if each value of column a always maps to the same value of b (b is just a regrouping of a)."""
    seen = {}
    for x, y in zip(a, b):
        if x is not None and seen.setdefault(x, y) != y:
            return False
    return True


def _drops(ser: dict[str, float]) -> list[tuple[str, str, float]]:
    """(period, compared_to, change) for each period. With 13+ months of monthly data, compare with the same month
    last year so normal seasonality (holiday peaks, summer slumps) isn't mistaken for a problem; else vs the prior period."""
    pts = sorted(ser.items())
    if len(pts) >= 13 and all(YEAR_MONTH.match(p) for p, _ in pts):
        out = []
        for p, v in pts:
            prev = f"{int(p[:4]) - 1}{p[4:]}"
            if ser.get(prev):
                out.append((p, f"{prev}, same month last year", (v - ser[prev]) / abs(ser[prev])))
        return out
    return [(b[0], a[0], (b[1] - a[1]) / abs(a[1])) for a, b in zip(pts, pts[1:]) if a[1]]


def _charts(table, title, metric, time, segment, monthly, schema, email) -> list[dict]:
    """Charts carry their SQL, so they can be pinned and stay live."""
    t, specs = _q(table), []
    if metric:
        m = _q(metric)
        nice = metric.replace("_", " ")
        specs.append(({"type": "number", "title": f"Total {nice}", "y": [f"total_{metric}"[:60]]},
                      f"SELECT sum({m}) AS {_q(f'total_{metric}'[:60])} FROM {t}"))
        if time:
            tc = _q(time)
            if schema.get(time) == "text":  # YYYY-MM strings
                period = tc
            else:
                period = f"date_trunc('{'month' if monthly else 'day'}', {tc})::date"
            specs.append(({"type": "line", "title": f"{nice.capitalize()} over time", "x": "period", "y": [metric]},
                          f"SELECT {period} AS period, sum({m}) AS {m} FROM {t} WHERE {tc} IS NOT NULL GROUP BY 1 ORDER BY 1"))
        if segment:
            s = _q(segment)
            specs.append(({"type": "bar", "title": f"{nice.capitalize()} by {segment.replace('_', ' ')}", "x": segment, "y": [metric]},
                          f"SELECT {s}, sum({m}) AS {m} FROM {t} WHERE {s} IS NOT NULL GROUP BY 1 ORDER BY 2 DESC LIMIT 12"))
    charts = []
    for spec, query in specs:
        try:
            res = datasources.run(datasources.UPLOADS, query, email)
            result = {**res, "rows": [[_jsonable(v) for v in r] for r in res["rows"]], "source": datasources.UPLOADS, "sql": query}
            charts.append(build_chart(result, spec))
        except Exception:
            continue  # a chart that can't be built is skipped, never fatal
    return charts


def to_markdown(a: dict) -> str:
    """The no-AI write-up: computed findings, plainly stated."""
    lines = [f"**Analysis of {a['title']}**", ""]
    order = {"trend": 0, "segment": 1, "shift": 2, "anomaly": 3, "relationship": 4, "overview": 5, "quality": 6}
    for f in sorted(a["findings"], key=lambda f: order.get(f["kind"], 9)):
        lines.append(f"- **{f['headline']}**" + (f". {f['detail']}" if f["detail"] else ""))
    lines += ["", "_These figures are calculated directly from the sheet. Ask a follow-up to dig into any of them._"]
    return "\n".join(lines)


def _label(v) -> str:
    return v.strftime("%b %Y") if isinstance(v, (date, datetime)) else str(v)


def _period(v, monthly: bool) -> str:
    if isinstance(v, (date, datetime)):
        return v.strftime("%Y-%m") if monthly else v.strftime("%Y-%m-%d")
    return str(v)


def _span_days(values) -> int:
    ds = [v for v in values if isinstance(v, (date, datetime))]
    if ds:
        lo, hi = min(ds), max(ds)
        return (hi - lo).days
    ym = sorted(str(v) for v in values if v is not None and YEAR_MONTH.match(str(v)))
    return 31 * len(set(ym)) if ym else 0


def _pearson(pairs: list[tuple[float, float]]) -> float | None:
    xs, ys = zip(*pairs)
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    sx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    sy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if not sx or not sy:
        return None
    return sum((x - mx) * (y - my) for x, y in pairs) / (sx * sy)


# ---------- forecasting and what-if ----------

def _next_periods(periods: list[str], horizon: int) -> tuple[list[str], bool]:
    """Future labels in the same shape as `periods` → (labels, monthly?)."""
    try:
        ds = [date.fromisoformat(p[:10] if len(p) >= 10 else p[:7] + "-01") for p in periods]
    except ValueError:
        return [f"+{h}" for h in range(1, horizon + 1)], False
    monthly = len(ds) > 1 and all(d.day == 1 for d in ds) and all(
        (b.year - a.year) * 12 + b.month - a.month == 1 for a, b in zip(ds, ds[1:]))
    last, short = ds[-1], all(len(p) == 7 for p in periods)
    if monthly:
        out = [date(last.year + (last.month - 1 + h) // 12, (last.month - 1 + h) % 12 + 1, 1) for h in range(1, horizon + 1)]
        return [d.isoformat()[:7] if short else d.isoformat() for d in out], True
    step = statistics.median((b - a).days for a, b in zip(ds, ds[1:])) if len(ds) > 1 else 1
    return [date.fromordinal(last.toordinal() + int(step * h)).isoformat() for h in range(1, horizon + 1)], False


def forecast(periods: list[str], values: list[float], horizon: int) -> dict:
    """Seasonal-naive with trend for ≥ 24 monthly points, else a linear trend. Range ≈ 95% (±1.96 × residual spread).

    → {periods, forecast, lower, upper, method}"""
    if len(values) < 3:
        raise ValueError("Need at least 3 data points to forecast")
    horizon = max(1, min(int(horizon), 36))
    labels, monthly = _next_periods(periods, horizon)
    n = len(values)
    if monthly and n >= 24:
        drift = (sum(values[-12:]) - sum(values[-24:-12])) / 12  # average year-over-year change per month
        resid = [values[t] - values[t - 12] - drift for t in range(12, n)]
        s = statistics.pstdev(resid)
        point, spread = [], []
        for h in range(1, horizon + 1):
            years = (h - 1) // 12 + 1
            point.append(values[n - 12 + (h - 1) % 12] + drift * years)
            spread.append(1.96 * s * math.sqrt(years))
        method = "seasonal (same month last year plus the yearly trend)"
    else:
        mx, my = (n - 1) / 2, sum(values) / n
        slope = sum((x - mx) * (y - my) for x, y in enumerate(values)) / sum((x - mx) ** 2 for x in range(n))
        icpt = my - slope * mx
        s = math.sqrt(sum((y - icpt - slope * x) ** 2 for x, y in enumerate(values)) / max(n - 2, 1))
        point = [icpt + slope * (n - 1 + h) for h in range(1, horizon + 1)]
        spread = [1.96 * s * math.sqrt(1 + h / n) for h in range(1, horizon + 1)]
        method = "linear trend"
    return {"periods": labels, "forecast": [round(p, 2) for p in point], "lower": [round(p - d, 2) for p, d in zip(point, spread)],
            "upper": [round(p + d, 2) for p, d in zip(point, spread)], "method": method}


def what_if(values: list, change_pct: float) -> dict:
    total = sum(v for v in values if isinstance(v, (int, float)))
    adjusted = total * (1 + change_pct / 100)
    return {"original_total": round(total, 2), "adjusted_total": round(adjusted, 2), "difference": round(adjusted - total, 2),
            "change_pct": change_pct}
