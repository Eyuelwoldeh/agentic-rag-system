"""Answer feedback and the admin Insights view: usage, quality, cost, and knowledge gaps."""
import os

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .db import connect
from .security import User, current_user, require_admin

router = APIRouter()

# USD per million tokens (input, output). Used only for the estimate shown to admins.
PRICES = {"claude-opus-5-5": (4.0, 20.0), "claude-sonnet-5-5": (2.0, 10.0), "claude-haiku-5-5": (0.10, 0.50)}


class Feedback(BaseModel):
    value: int = Field(ge=-1, le=1)
    note: str = Field("", max_length=1000)


@router.post("/api/runs/{run_id}/feedback")
def feedback(run_id: int, body: Feedback, user: User = Depends(current_user)):
    if body.value == 0:
        raise HTTPException(422, "value must be 1 or -1")
    with connect() as conn:
        updated = conn.execute("UPDATE runs SET feedback = %s, feedback_note = %s WHERE id = %s AND user_id = %s RETURNING id",
                               (body.value, body.note.strip() or None, run_id, user.id)).fetchone()
    if not updated:
        raise HTTPException(404, "Run not found")
    return {"ok": True}


@router.get("/api/insights")
def insights(days: int = 30, _: User = Depends(require_admin)):
    days = max(1, min(days, 365))
    with connect() as conn:
        totals = conn.execute(
            "SELECT count(*), count(DISTINCT user_id), "
            "count(*) FILTER (WHERE (critic->>'grounded')::bool AND (critic->>'complete')::bool "
            "                   AND critic->>'hallucination_risk' <> 'high'), "
            "count(*) FILTER (WHERE critic IS NOT NULL), "
            "count(*) FILTER (WHERE feedback = 1), count(*) FILTER (WHERE feedback = -1), "
            "coalesce(percentile_cont(0.5) WITHIN GROUP (ORDER BY latency_ms), 0) "
            "FROM runs WHERE created_at > now() - make_interval(days => %s)", (days,)).fetchone()
        daily = conn.execute(
            "SELECT date_trunc('day', created_at)::date, count(*) FROM runs "
            "WHERE created_at > now() - make_interval(days => %s) GROUP BY 1 ORDER BY 1", (days,)).fetchall()
        cost_rows = conn.execute(
            "SELECT coalesce(model, %s), sum(input_tokens), sum(output_tokens) FROM runs "
            "WHERE created_at > now() - make_interval(days => %s) GROUP BY 1", (os.getenv("AGENT_MODEL", "claude-opus-5-5"), days)).fetchall()
        # Knowledge gaps: questions the system couldn't answer well, the to-do list for whoever owns the docs.
        gaps = conn.execute(
            "SELECT r.id, r.question, r.created_at, u.email, "
            "CASE WHEN r.feedback = -1 THEN 'thumbs down' "
            "     WHEN r.events::text LIKE '%%\"fallback\"%%' AND r.answer LIKE '%%No matching passages%%' THEN 'nothing found' "
            "     ELSE 'not verified' END, r.feedback_note "
            "FROM runs r LEFT JOIN users u ON u.id = r.user_id "
            "WHERE r.created_at > now() - make_interval(days => %s) AND (r.feedback = -1 "
            "   OR r.answer LIKE '%%No matching passages%%' "
            "   OR (r.critic IS NOT NULL AND NOT ((r.critic->>'grounded')::bool AND (r.critic->>'complete')::bool))) "
            "ORDER BY r.id DESC LIMIT 50", (days,)).fetchall()
    runs, users, verified, judged, up, down, p50 = totals
    cost = sum((i or 0) / 1e6 * PRICES.get(m, (0, 0))[0] + (o or 0) / 1e6 * PRICES.get(m, (0, 0))[1] for m, i, o in cost_rows)
    return {
        "days": days, "questions": runs, "active_users": users, "verified_rate": verified / judged if judged else None,
        "thumbs_up": up, "thumbs_down": down, "median_latency_ms": int(p50), "estimated_cost_usd": round(cost, 2),
        "daily": [{"day": str(d), "questions": n} for d, n in daily],
        "gaps": [{"id": g[0], "question": g[1], "created_at": g[2], "user": g[3], "reason": g[4], "note": g[5]} for g in gaps],
    }
