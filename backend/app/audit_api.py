"""Admin view of the audit trail: filter, paginate, export to CSV."""
import csv
import io
import json
from datetime import date, timedelta

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response

from .db import connect
from .security import User, require_admin

router = APIRouter()
COLS = ["id", "at", "actor_email", "action", "target", "detail", "ip"]


def _where(action: str | None, person: str | None, start: date | None, end: date | None) -> tuple[str, dict]:
    clauses, params = [], {}
    if action:
        clauses.append("action = %(action)s")
        params["action"] = action
    if person:
        clauses.append("actor_email ILIKE %(person)s")
        params["person"] = "%" + person.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    if start:
        clauses.append("at >= %(start)s")
        params["start"] = start
    if end:  # inclusive: the whole end day
        clauses.append("at < %(end)s")
        params["end"] = end + timedelta(days=1)
    return ("WHERE " + " AND ".join(clauses)) if clauses else "", params


@router.get("/api/audit")
def list_audit(action: str | None = None, person: str | None = None, start: date | None = None, end: date | None = None,
               page: int = Query(1, ge=1), size: int = Query(50, ge=1, le=200), _: User = Depends(require_admin)):
    where, params = _where(action, person, start, end)
    with connect() as conn:
        total = conn.execute(f"SELECT count(*) FROM audit_log {where}", params).fetchone()[0]
        rows = conn.execute(f"SELECT {', '.join(COLS)} FROM audit_log {where} ORDER BY at DESC, id DESC "
                            "LIMIT %(lim)s OFFSET %(off)s", {**params, "lim": size, "off": (page - 1) * size}).fetchall()
        actions = [r[0] for r in conn.execute("SELECT DISTINCT action FROM audit_log ORDER BY 1").fetchall()]
    return {"total": total, "page": page, "size": size, "actions": actions, "items": [dict(zip(COLS, r)) for r in rows]}


@router.get("/api/audit/export")
def export_audit(action: str | None = None, person: str | None = None, start: date | None = None, end: date | None = None,
                 _: User = Depends(require_admin)):
    where, params = _where(action, person, start, end)
    with connect() as conn:
        # ponytail: built in memory, capped at 100k rows; stream with a server-side cursor if exports get bigger
        rows = conn.execute(f"SELECT {', '.join(COLS)} FROM audit_log {where} ORDER BY at DESC, id DESC LIMIT 100000", params).fetchall()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["id", "time", "person", "action", "target", "detail", "ip"])
    for r in rows:
        cells = [r[0], r[1].isoformat(), r[2] or "", r[3], r[4] or "", json.dumps(r[5]) if r[5] else "", r[6] or ""]
        w.writerow(["'" + c if isinstance(c, str) and c[:1] in ("=", "+", "-", "@") else c for c in cells])  # no spreadsheet formulas
    return Response(buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="audit-log-{date.today()}.csv"', "Cache-Control": "no-store"})
