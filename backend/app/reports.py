"""Scheduled reports: re-ask a question, or refresh dashboard charts, on a daily/weekly/monthly cadence.

A question report runs once per person (owner and each recipient) with that person's own access, in a new
saved conversation of theirs, so nobody receives numbers they couldn't look up themselves.
"""
import logging
import os
import smtplib
import threading
from datetime import datetime, timedelta
from email.message import EmailMessage
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from . import agent, audit, conversations, dashboards, notify, schedules
from .db import connect
from .security import FRONTEND_URL, User, current_user

router = APIRouter()
log = logging.getLogger("reports")


# ---------- shared helpers (alerts use these too) ----------

def send_email(to: list[str], subject: str, body: str) -> bool:
    """Plain-text email when SMTP_HOST/SMTP_USER/SMTP_PASSWORD/SMTP_FROM are set. Never raises."""
    host, user, pw, sender = (os.getenv(k) for k in ("SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD", "SMTP_FROM"))
    if not (host and user and pw and sender and to):
        return False
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = subject, sender, ", ".join(to)
    msg.set_content(body)
    try:
        with smtplib.SMTP(host, int(os.getenv("SMTP_PORT", "587")), timeout=20) as s:
            s.starttls()
            s.login(user, pw)
            s.send_message(msg)
        return True
    except Exception as e:
        log.warning("email to %s failed: %s", to, e)
        return False


def users(ids: list[int]) -> list[User]:
    with connect() as conn:
        rows = conn.execute("SELECT id, email, role FROM users WHERE id = ANY(%s) ORDER BY id", (list(ids),)).fetchall()
    return [User(id=r[0], email=r[1], role=r[2]) for r in rows]


def check_recipients(ids: list[int]) -> list[int]:
    ids = sorted(set(ids))
    if len(users(ids)) != len(ids):
        raise HTTPException(422, "Some recipients aren't users of this workspace")
    return ids


@router.get("/api/people")
def people(_: User = Depends(current_user)):
    """Who can receive reports and alerts."""
    with connect() as conn:
        return [{"id": r[0], "email": r[1]} for r in conn.execute("SELECT id, email FROM users ORDER BY email").fetchall()]


# ---------- schedule ----------

def next_run(cadence: str, hour: int, weekday: int | None = None, day: int | None = None, after: datetime | None = None) -> datetime:
    """Next run in server local time (naive arithmetic, so daylight saving changes keep the hour)."""
    now = after or datetime.now()
    t = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    if cadence == "daily":
        cand = t if t > now else t + timedelta(days=1)
    elif cadence == "weekly":
        cand = t + timedelta(days=((weekday or 0) - t.weekday()) % 7)
        cand = cand if cand > now else cand + timedelta(days=7)
    else:
        cand = t.replace(day=day or 1)
        if cand <= now:
            cand = cand.replace(year=cand.year + cand.month // 12, month=cand.month % 12 + 1)
    return cand.astimezone()


# ---------- running ----------

COLS = "id, owner_id, title, question, tile_ids, cadence, weekday, day, hour, recipients, enabled, last_run_at, last_status, last_error, next_run_at"
KEYS = COLS.split(", ")


def _load(report_id: int) -> dict | None:
    with connect() as conn:
        row = conn.execute(f"SELECT {COLS} FROM reports WHERE id = %s", (report_id,)).fetchone()
    return dict(zip(KEYS, row)) if row else None


def run_report(report_id: int) -> str:
    """Run one report now. Returns 'ok' or 'error'."""
    r = _load(report_id)
    if not r:
        return "error"
    people = users([r["owner_id"], *r["recipients"]])
    owner = next((p for p in people if p.id == r["owner_id"]), None)
    errors = []
    try:
        if r["question"]:
            for p in people:  # one conversation per person, each with their own access
                cid = conversations.create(p, f"{r['title']} ({datetime.now():%b %d})")
                answer = ""
                for ev in agent.run(r["question"], [], user_id=p.id, email=p.email, conversation_id=cid):
                    if ev["type"] == "done":
                        answer = ev["answer"]
                    elif ev["type"] == "error":
                        errors.append(ev["message"])
                summary = " ".join(answer.split())[:300]
                notify.notify([p.id], "report", f"Report ready: {r['title']}", summary, f"/?c={cid}")
                send_email([p.email], f"Report: {r['title']}", f"{answer[:3000]}\n\nOpen the full report: {FRONTEND_URL}/?c={cid}\n")
        else:
            titles = {}
            for tid in r["tile_ids"] or []:
                try:
                    d = dashboards.tile_data(tid, refresh=True, user=owner)
                    titles[tid] = (d["title"], not d.get("error") and not d.get("no_access"))
                except HTTPException:
                    errors.append(f"Chart {tid} is no longer available")
            with connect() as conn:
                for p in people:  # each person only hears about charts they can see
                    mine = {row[0] for row in conn.execute("SELECT id FROM dashboard_tiles WHERE id = ANY(%s) AND (owner_id = %s OR shared)",
                                                           (list(titles), p.id)).fetchall()}
                    shown = [titles[t] for t in titles if t in mine]
                    if not shown:
                        continue
                    body = "Refreshed: " + ", ".join(t for t, ok in shown if ok)
                    failed = [t for t, ok in shown if not ok]
                    body += f". Couldn't refresh: {', '.join(failed)}" if failed else ""
                    notify.notify([p.id], "report", f"Dashboard report: {r['title']}", body, "/dashboard")
                    send_email([p.email], f"Dashboard report: {r['title']}", f"{body}\n\nOpen your dashboard: {FRONTEND_URL}/dashboard\n")
            errors += [f"Couldn't refresh {t}" for t, ok in titles.values() if not ok]
    except Exception as e:
        errors.append(str(e)[:300])
    status = "error" if errors else "ok"
    with connect() as conn:
        conn.execute("UPDATE reports SET last_run_at = now(), last_status = %s, last_error = %s WHERE id = %s",
                     (status, "; ".join(errors)[:1000] or None, report_id))
    if errors:
        notify.notify([r["owner_id"]], "report", f"Report had problems: {r['title']}", "; ".join(errors)[:500], "/reports")
    return status


@schedules.every_minute
def run_due_reports() -> None:
    # ponytail: runs reports one after another in the scheduler thread; move to a worker pool if many are due at once
    with connect() as conn:
        due = conn.execute("SELECT id, cadence, hour, weekday, day, next_run_at FROM reports WHERE enabled AND next_run_at <= now() "
                           "ORDER BY next_run_at").fetchall()
    for rid, cadence, hour, weekday, day, at in due:
        with connect() as conn:  # claim it first, so a second process can't run it too
            claimed = conn.execute("UPDATE reports SET next_run_at = %s WHERE id = %s AND next_run_at = %s RETURNING id",
                                   (next_run(cadence, hour, weekday, day), rid, at)).fetchone()
        if claimed:
            run_report(rid)


def _background(fn, *args) -> None:
    threading.Thread(target=fn, args=args, daemon=True).start()


# ---------- API ----------

class ReportIn(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    question: str | None = Field(None, max_length=4000)
    tile_ids: list[int] | None = Field(None, max_length=20)
    cadence: Literal["daily", "weekly", "monthly"]
    weekday: int | None = Field(None, ge=0, le=6)
    day: int | None = Field(None, ge=1, le=28)
    hour: int = Field(8, ge=0, le=23)
    recipients: list[int] = Field(default_factory=list, max_length=100)
    enabled: bool = True


class Toggle(BaseModel):
    enabled: bool


def _clean(body: ReportIn, user: User) -> dict:
    r = body.model_dump()
    r["question"] = (r["question"] or "").strip() or None
    r["tile_ids"] = sorted(set(r["tile_ids"] or [])) or None
    if bool(r["question"]) == bool(r["tile_ids"]):
        raise HTTPException(422, "A report is either a question or a set of dashboard charts")
    if r["tile_ids"]:
        with connect() as conn:
            ok = conn.execute("SELECT count(*) FROM dashboard_tiles WHERE id = ANY(%s) AND (owner_id = %s OR shared)",
                              (r["tile_ids"], user.id)).fetchone()[0]
        if ok != len(r["tile_ids"]):
            raise HTTPException(422, "Some charts aren't on your dashboard")
    r["weekday"] = (r["weekday"] or 0) if r["cadence"] == "weekly" else None
    r["day"] = (r["day"] or 1) if r["cadence"] == "monthly" else None
    r["recipients"] = [i for i in check_recipients(r["recipients"]) if i != user.id]
    r["next_run_at"] = next_run(r["cadence"], r["hour"], r["weekday"], r["day"])
    return r


def _owned(report_id: int, user: User) -> dict:
    r = _load(report_id)
    if not r or r["owner_id"] != user.id:
        raise HTTPException(404, "Report not found")
    return r


@router.get("/api/reports")
def list_reports(user: User = Depends(current_user)):
    with connect() as conn:
        rows = conn.execute(f"SELECT {COLS} FROM reports WHERE owner_id = %s ORDER BY id DESC", (user.id,)).fetchall()
        emails = dict(conn.execute("SELECT id, email FROM users").fetchall())
    out = [dict(zip(KEYS, r)) for r in rows]
    return [{**r, "recipient_emails": [emails[i] for i in r["recipients"] if i in emails]} for r in out]


@router.post("/api/reports")
def create_report(body: ReportIn, user: User = Depends(current_user)):
    r = _clean(body, user)
    with connect() as conn:
        rid = conn.execute("INSERT INTO reports (owner_id, title, question, tile_ids, cadence, weekday, day, hour, recipients, enabled, next_run_at) "
                           "VALUES (%(owner)s, %(title)s, %(question)s, %(tile_ids)s, %(cadence)s, %(weekday)s, %(day)s, %(hour)s, "
                           "%(recipients)s, %(enabled)s, %(next_run_at)s) RETURNING id", {**r, "owner": user.id}).fetchone()[0]
    audit.record("report.create", user, f"report:{rid}", {"title": r["title"], "recipients": r["recipients"]})
    return {"id": rid, "next_run_at": r["next_run_at"]}


@router.put("/api/reports/{report_id}")
def update_report(report_id: int, body: ReportIn, user: User = Depends(current_user)):
    _owned(report_id, user)
    r = _clean(body, user)
    with connect() as conn:
        conn.execute("UPDATE reports SET title = %(title)s, question = %(question)s, tile_ids = %(tile_ids)s, cadence = %(cadence)s, "
                     "weekday = %(weekday)s, day = %(day)s, hour = %(hour)s, recipients = %(recipients)s, enabled = %(enabled)s, "
                     "next_run_at = %(next_run_at)s WHERE id = %(id)s", {**r, "id": report_id})
    audit.record("report.update", user, f"report:{report_id}", {"recipients": r["recipients"]})
    return {"ok": True}


@router.patch("/api/reports/{report_id}")
def toggle_report(report_id: int, body: Toggle, user: User = Depends(current_user)):
    r = _owned(report_id, user)
    with connect() as conn:
        conn.execute("UPDATE reports SET enabled = %s, next_run_at = %s WHERE id = %s",
                     (body.enabled, next_run(r["cadence"], r["hour"], r["weekday"], r["day"]), report_id))
    return {"ok": True}


@router.post("/api/reports/{report_id}/run")
def run_now(report_id: int, user: User = Depends(current_user)):
    _owned(report_id, user)
    _background(run_report, report_id)
    return {"ok": True}


@router.delete("/api/reports/{report_id}")
def delete_report(report_id: int, user: User = Depends(current_user)):
    _owned(report_id, user)
    with connect() as conn:
        conn.execute("DELETE FROM reports WHERE id = %s", (report_id,))
    audit.record("report.delete", user, f"report:{report_id}")
    return {"ok": True}
