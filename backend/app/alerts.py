"""Alerts: watch a metric (or an ad hoc aggregate) hourly or daily; notify when it starts meeting its condition.

Evaluated with the owner's access. Recipients are only told when they can see the underlying data themselves.
"""
import json
from datetime import date, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from . import audit, datasources, metrics, notify, schedules
from .db import connect
from .reports import check_recipients, send_email, users
from .security import FRONTEND_URL, User, current_user
from .tools import _jsonable

router = APIRouter()

COLS = ("a.id, a.owner_id, a.name, a.metric_id, a.source, a.table_name, a.expression, a.time_column, a.condition, a.threshold, "
        "a.group_by, a.cadence, a.recipients, a.enabled, a.last_state, a.last_checked_at, a.last_triggered_at, m.name")
KEYS = [c.split(".")[1] for c in COLS.split(", ")[:-1]] + ["metric_name"]


def _load(alert_id: int) -> dict | None:
    with connect() as conn:
        row = conn.execute(f"SELECT {COLS} FROM alerts a LEFT JOIN metrics m ON m.id = a.metric_id WHERE a.id = %s", (alert_id,)).fetchone()
    return dict(zip(KEYS, row)) if row else None


def definition(a: dict) -> dict:
    """The aggregate an alert watches, in the shape metrics.build_sql takes."""
    if a.get("metric_id"):
        with connect() as conn:
            row = conn.execute(f"SELECT {metrics.COLS} FROM metrics WHERE id = %s", (a["metric_id"],)).fetchone()
        if not row:
            raise ValueError("The metric this alert watches was deleted")
        return dict(zip(metrics.KEYS, row))
    return {"name": "value", "source": a["source"], "table_name": a["table_name"], "expression": a["expression"],
            "time_column": a.get("time_column") or None, "filters": None}


def _last_year(d: date) -> date:
    return d.replace(year=d.year - 1, day=min(d.day, 28) if d.month == 2 else d.day)


def evaluate(a: dict, email: str | None, today: date | None = None) -> dict:
    """→ {triggered, message, rows: [{group, value, baseline?, change_pct?, hit}]}"""
    m = definition(a)
    kind = metrics.kind_of(m["source"], email)
    cols = metrics.schema_for(m["source"], m["table_name"], email)
    today = today or date.today()
    start, end = today.replace(day=1), today + timedelta(days=1)  # month to date

    def values(lo: date | None, hi: date | None) -> dict[str, float]:
        sql = metrics.build_sql(m, cols, kind, group_by=a.get("group_by"), start=lo and lo.isoformat(), end=hi and hi.isoformat())
        res = datasources.run(m["source"], sql, email)
        return {" ".join(str(v) for v in r[:-1]) or "Total": float(_jsonable(r[-1]) or 0) for r in res["rows"]}

    rows = []
    if a["condition"] == "drop_vs_last_year":
        if not m.get("time_column"):
            raise ValueError("Comparing with last year needs a time column")
        cur, prev = values(start, end), values(_last_year(start), _last_year(end))
        for g in sorted(set(cur) | set(prev)):
            c, p = cur.get(g, 0.0), prev.get(g, 0.0)
            change = round((c - p) / abs(p) * 100, 1) if p else None
            rows.append({"group": g, "value": c, "baseline": p, "change_pct": change, "hit": change is not None and change <= -a["threshold"]})
        hits = [f"{r['group']} {r['change_pct']:+.1f}%" for r in rows if r["hit"]]
        what = f"down more than {a['threshold']:g}% vs the same period last year"
    else:
        cur = values(start, end) if m.get("time_column") else values(None, None)
        for g, v in sorted(cur.items()):
            rows.append({"group": g, "value": v, "hit": v > a["threshold"] if a["condition"] == "above" else v < a["threshold"]})
        hits = [f"{r['group']} {r['value']:,.2f}" for r in rows if r["hit"]]
        what = f"{a['condition']} {a['threshold']:,g}"
    triggered = bool(hits)
    message = f"{a['name']}: {what} ({', '.join(hits[:5])}{', and more' if len(hits) > 5 else ''})" if triggered else f"{a['name']}: not {what}"
    return {"triggered": triggered, "message": message, "rows": rows[:200]}


def _can_see(m: dict, email: str) -> bool:
    try:
        metrics.schema_for(m["source"], m["table_name"], email)
        return True
    except Exception:
        return False


def check(alert_id: int, today: date | None = None) -> str:
    """Evaluate as the owner, record state changes, and notify on the transition into 'triggered'. Returns the state."""
    a = _load(alert_id)
    owner = users([a["owner_id"]])[0]
    try:
        result = evaluate(a, owner.email, today)
        state, message = ("triggered" if result["triggered"] else "ok"), result["message"]
    except Exception as e:
        state, message, result = "error", f"{a['name']}: couldn't check ({str(e)[:200]})", None
    changed = state != (a["last_state"] or "ok")
    with connect() as conn:
        conn.execute("UPDATE alerts SET last_state = %s, last_checked_at = now(), "
                     "last_triggered_at = CASE WHEN %s THEN now() ELSE last_triggered_at END WHERE id = %s",
                     (state, changed and state == "triggered", alert_id))
        if changed:
            conn.execute("INSERT INTO alert_events (alert_id, state, message, detail) VALUES (%s, %s, %s, %s)",
                         (alert_id, state, message, json.dumps(result, default=str) if result else None))
    if changed and state == "triggered":
        m = definition(a)
        people = [p for p in users([a["owner_id"], *a["recipients"]]) if p.id == owner.id or _can_see(m, p.email)]
        notify.notify([p.id for p in people], "alert", f"Alert: {a['name']}", message, "/alerts")
        send_email([p.email for p in people], f"Alert: {a['name']}", f"{message}\n\nDetails: {FRONTEND_URL}/alerts\n")
    elif changed and state == "error":
        notify.notify([owner.id], "alert", f"Alert couldn't be checked: {a['name']}", message, "/alerts")
    return state


@schedules.every_minute
def check_due_alerts() -> None:
    with connect() as conn:  # claim due alerts in one statement, so two processes don't both check them
        due = conn.execute("UPDATE alerts SET last_checked_at = now() WHERE enabled AND (last_checked_at IS NULL OR last_checked_at <= now() - "
                           "CASE cadence WHEN 'hourly' THEN interval '1 hour' ELSE interval '1 day' END) RETURNING id").fetchall()
    for (aid,) in due:
        check(aid)


# ---------- API ----------

class AlertIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    metric_id: int | None = None
    source: str | None = Field(None, max_length=50)
    table_name: str | None = Field(None, max_length=300)
    expression: str | None = Field(None, max_length=1000)
    time_column: str | None = Field(None, max_length=200)
    condition: Literal["drop_vs_last_year", "above", "below"]
    threshold: float
    group_by: str | None = Field(None, max_length=200)
    cadence: Literal["hourly", "daily"] = "daily"
    recipients: list[int] = Field(default_factory=list, max_length=100)
    enabled: bool = True


class Toggle(BaseModel):
    enabled: bool


def _clean(body: AlertIn, user: User) -> dict:
    a = body.model_dump()
    for k in ("source", "table_name", "expression", "time_column", "group_by"):
        a[k] = (a[k] or "").strip() or None
    if a["metric_id"]:
        a.update(source=None, table_name=None, expression=None, time_column=None)
    elif not (a["source"] and a["table_name"] and a["expression"]):
        raise HTTPException(422, "Pick a metric, or a data source, table and expression")
    try:
        m = definition(a)
        if a["condition"] == "drop_vs_last_year" and not m.get("time_column"):
            raise ValueError("Comparing with last year needs a time column")
        metrics.build_sql(m, metrics.schema_for(m["source"], m["table_name"], user.email), metrics.kind_of(m["source"], user.email),
                          group_by=a["group_by"])
    except datasources.AccessDenied as e:
        raise HTTPException(403, str(e))
    except Exception as e:
        raise HTTPException(422, str(e)[:300])
    a["recipients"] = [i for i in check_recipients(a["recipients"]) if i != user.id]
    return a


def _owned(alert_id: int, user: User) -> dict:
    a = _load(alert_id)
    if not a or a["owner_id"] != user.id:
        raise HTTPException(404, "Alert not found")
    return a


FIELDS = ["name", "metric_id", "source", "table_name", "expression", "time_column", "condition", "threshold", "group_by", "cadence",
          "recipients", "enabled"]


@router.get("/api/alerts")
def list_alerts(user: User = Depends(current_user)):
    with connect() as conn:
        rows = conn.execute(f"SELECT {COLS} FROM alerts a LEFT JOIN metrics m ON m.id = a.metric_id WHERE a.owner_id = %s ORDER BY a.id DESC",
                            (user.id,)).fetchall()
        emails = dict(conn.execute("SELECT id, email FROM users").fetchall())
    out = [dict(zip(KEYS, r)) for r in rows]
    return [{**a, "recipient_emails": [emails[i] for i in a["recipients"] if i in emails]} for a in out]


@router.post("/api/alerts")
def create_alert(body: AlertIn, user: User = Depends(current_user)):
    a = _clean(body, user)
    with connect() as conn:
        aid = conn.execute(f"INSERT INTO alerts (owner_id, {', '.join(FIELDS)}) VALUES (%(owner)s, {', '.join(f'%({k})s' for k in FIELDS)}) "
                           "RETURNING id", {**a, "owner": user.id}).fetchone()[0]
    audit.record("alert.create", user, f"alert:{aid}", {"name": a["name"], "recipients": a["recipients"]})
    return {"id": aid}


@router.put("/api/alerts/{alert_id}")
def update_alert(alert_id: int, body: AlertIn, user: User = Depends(current_user)):
    _owned(alert_id, user)
    a = _clean(body, user)
    with connect() as conn:  # a changed definition starts fresh
        conn.execute(f"UPDATE alerts SET {', '.join(f'{k} = %({k})s' for k in FIELDS)}, last_state = NULL, last_checked_at = NULL "
                     "WHERE id = %(id)s", {**a, "id": alert_id})
    audit.record("alert.update", user, f"alert:{alert_id}", {"recipients": a["recipients"]})
    return {"ok": True}


@router.patch("/api/alerts/{alert_id}")
def toggle_alert(alert_id: int, body: Toggle, user: User = Depends(current_user)):
    _owned(alert_id, user)
    with connect() as conn:
        conn.execute("UPDATE alerts SET enabled = %s WHERE id = %s", (body.enabled, alert_id))
    return {"ok": True}


@router.delete("/api/alerts/{alert_id}")
def delete_alert(alert_id: int, user: User = Depends(current_user)):
    _owned(alert_id, user)
    with connect() as conn:
        conn.execute("DELETE FROM alerts WHERE id = %s", (alert_id,))
    audit.record("alert.delete", user, f"alert:{alert_id}")
    return {"ok": True}


@router.post("/api/alerts/{alert_id}/test")
def test_alert(alert_id: int, user: User = Depends(current_user)):
    """Check now without changing the alert's state or notifying anyone."""
    a = _owned(alert_id, user)
    try:
        return evaluate(a, user.email)
    except datasources.AccessDenied as e:
        raise HTTPException(403, str(e))
    except Exception as e:
        raise HTTPException(422, str(e)[:300])


@router.get("/api/alerts/{alert_id}/events")
def alert_events(alert_id: int, user: User = Depends(current_user)):
    _owned(alert_id, user)
    with connect() as conn:
        rows = conn.execute("SELECT id, at, state, message FROM alert_events WHERE alert_id = %s ORDER BY at DESC LIMIT 100",
                            (alert_id,)).fetchall()
    return [{"id": r[0], "at": r[1], "state": r[2], "message": r[3]} for r in rows]
