"""Operations: health check, background jobs view, data retention, and spend-limit settings."""
import json
import os
import tomllib
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field

from . import audit, jobs, limits, llm, schedules, worker
from .db import connect, pool
from .security import User, require_admin

router = APIRouter()


def _version() -> str:
    try:
        return tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text())["project"]["version"]
    except (OSError, KeyError, tomllib.TOMLDecodeError):
        return "dev"


VERSION = os.getenv("APP_VERSION") or _version()
WORKER_STALE_SECONDS = 120


# ---------- health (no sign-in: for load balancers and uptime checks) ----------

def worker_status(conn) -> dict:
    row = conn.execute("SELECT extract(epoch FROM now() - updated_at) FROM app_settings WHERE key = 'worker_heartbeat'").fetchone()
    age = round(float(row[0])) if row else None
    return {"mode": worker.WORKER_MODE, "heartbeat_age_seconds": age, "healthy": age is not None and age < WORKER_STALE_SECONDS}


@router.get("/api/health")
def health(response: Response):
    try:
        with pool().connection(timeout=5) as conn:
            migrations = conn.execute("SELECT count(*) FROM schema_migrations").fetchone()[0]
            w = worker_status(conn)
    except Exception:
        response.status_code = 503
        return {"ok": False, "db": False, "version": VERSION}
    return {"ok": True, "db": True, "migrations": migrations, "worker": w, "version": VERSION}


# ---------- background jobs (admin) ----------

@router.get("/api/jobs")
def list_jobs(limit: int = 50, _: User = Depends(require_admin)):
    with connect() as conn:
        w = worker_status(conn)
    return {"worker": w, "jobs": jobs.recent(max(1, min(limit, 200)))}


# ---------- retention ----------

class Retention(BaseModel):
    questions_days: int = Field(0, ge=0, le=36500)  # questions and answers (runs), and conversations left empty
    notifications_days: int = Field(0, ge=0, le=36500)
    audit_days: int = Field(0, ge=0, le=36500)


JOBS_KEEP_DAYS = 30  # finished background jobs are housekeeping, not records


def retention_settings() -> Retention:
    with connect() as conn:
        row = conn.execute("SELECT value FROM app_settings WHERE key = 'retention'").fetchone()
    return Retention(**{k: v for k, v in ((row[0] if row else None) or {}).items() if k in Retention.model_fields})


def purge() -> dict:
    """Delete rows older than the retention settings (0 = keep forever). Returns counts by kind."""
    s = retention_settings()
    out = {}
    with connect() as conn:
        if s.questions_days:
            out["questions"] = conn.execute("DELETE FROM runs WHERE created_at < now() - make_interval(days => %s)",
                                            (s.questions_days,)).rowcount
            out["conversations"] = conn.execute(
                "DELETE FROM conversations c WHERE c.updated_at < now() - make_interval(days => %s) "
                "AND NOT EXISTS (SELECT 1 FROM runs r WHERE r.conversation_id = c.id)", (s.questions_days,)).rowcount
        if s.notifications_days:
            out["notifications"] = conn.execute("DELETE FROM notifications WHERE created_at < now() - make_interval(days => %s)",
                                                (s.notifications_days,)).rowcount
        if s.audit_days:
            out["audit_entries"] = conn.execute("DELETE FROM audit_log WHERE at < now() - make_interval(days => %s)",
                                                (s.audit_days,)).rowcount
        out["jobs"] = conn.execute("DELETE FROM jobs WHERE status IN ('done', 'failed') AND finished_at < now() - make_interval(days => %s)",
                                   (JOBS_KEEP_DAYS,)).rowcount
    deleted = {k: v for k, v in out.items() if v}
    if deleted:
        audit.record("retention.purge", None, "retention", {"deleted": deleted, "settings": s.model_dump()})
    return deleted


@jobs.handler("retention.purge")
def _purge_job(_payload: dict) -> None:
    purge()


@schedules.every_minute
def enqueue_daily_purge() -> None:
    with connect() as conn:
        recent = conn.execute("SELECT 1 FROM jobs WHERE kind = 'retention.purge' AND created_at > now() - interval '1 day'").fetchone()
    if not recent:
        jobs.enqueue("retention.purge", dedupe_key="retention.purge")


@router.get("/api/settings/retention")
def get_retention(_: User = Depends(require_admin)):
    return retention_settings()


@router.put("/api/settings/retention")
def put_retention(body: Retention, user: User = Depends(require_admin)):
    with connect() as conn:
        conn.execute("INSERT INTO app_settings (key, value) VALUES ('retention', %s) ON CONFLICT (key) DO UPDATE "
                     "SET value = EXCLUDED.value, updated_at = now()", (json.dumps(body.model_dump()),))
    audit.record("settings.retention", user, "settings:retention", body.model_dump())
    return body


# ---------- spend controls and rate limits ----------

class Limits(BaseModel):
    daily_tokens: int = Field(0, ge=0, le=10**10)
    per_minute: int = Field(0, ge=0, le=1000)
    allowed_models: dict[Literal["anthropic", "openai"], list[str]] = Field(default_factory=dict)


@router.get("/api/settings/limits")
def get_limits(_: User = Depends(require_admin)):
    return limits.settings()


@router.put("/api/settings/limits")
def put_limits(body: Limits, user: User = Depends(require_admin)):
    ai = llm.ai_settings()
    in_use = {p: ai["models"][p] for p in limits.PROVIDERS if ai["keys"][p]}
    try:
        saved = limits.save(body.daily_tokens, body.per_minute, body.allowed_models, in_use)
    except ValueError as e:
        raise HTTPException(422, str(e))
    audit.record("settings.limits", user, "settings:limits", saved)
    return saved
