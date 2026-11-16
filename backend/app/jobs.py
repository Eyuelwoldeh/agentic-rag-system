"""Postgres job queue. Any feature can queue work; a worker runs it with retries.

    from . import jobs
    @jobs.handler("report.send")
    def send_report(payload): ...
    jobs.enqueue("report.send", {"id": 5}, dedupe_key="report.send:5")

Workers claim jobs with SELECT ... FOR UPDATE SKIP LOCKED, so any number of them can run side by side.
"""
import json
import logging
import os
import socket
from datetime import datetime

from .db import connect

log = logging.getLogger("jobs")
HANDLERS: dict = {}
BACKOFF_SECONDS = 30  # retry delays: 30s, 60s, 120s, ...
STALE_MINUTES = 120  # a job "running" this long belonged to a worker that died: hand it out again
WORKER_ID = f"{socket.gethostname()}:{os.getpid()}"


def handler(kind: str):
    def register(fn):
        HANDLERS[kind] = fn
        return fn
    return register


def enqueue(kind: str, payload: dict | None = None, run_after: datetime | None = None, dedupe_key: str | None = None,
            max_attempts: int = 3) -> int | None:
    """Queue a job. With `dedupe_key`, returns None (and queues nothing) while a job with that key is queued or running."""
    with connect() as conn:
        row = conn.execute(
            "INSERT INTO jobs (kind, payload, run_after, dedupe_key, max_attempts) VALUES (%s, %s, coalesce(%s, now()), %s, %s) "
            "ON CONFLICT (dedupe_key) WHERE status IN ('queued', 'running') DO NOTHING RETURNING id",
            (kind, json.dumps(payload or {}), run_after, dedupe_key, max_attempts)).fetchone()
    return row[0] if row else None


def is_active(dedupe_key: str) -> bool:
    with connect() as conn:
        return conn.execute("SELECT 1 FROM jobs WHERE dedupe_key = %s AND status IN ('queued', 'running')",
                            (dedupe_key,)).fetchone() is not None


def work_once(worker_id: str = WORKER_ID) -> bool:
    """Claim and run one due job. Returns False when there was nothing to do."""
    with connect() as conn:
        job = conn.execute(
            "UPDATE jobs SET status = 'running', attempts = attempts + 1, locked_at = now(), locked_by = %s "
            "WHERE id = (SELECT id FROM jobs WHERE (status = 'queued' AND run_after <= now()) "
            "  OR (status = 'running' AND locked_at < now() - make_interval(mins => %s)) "
            "  ORDER BY run_after, id FOR UPDATE SKIP LOCKED LIMIT 1) "
            "RETURNING id, kind, payload, attempts, max_attempts", (worker_id, STALE_MINUTES)).fetchone()
    if not job:
        return False
    job_id, kind, payload, attempts, max_attempts = job
    try:
        fn = HANDLERS.get(kind)
        if not fn:
            raise LookupError(f"No handler for job kind {kind!r}")
        fn(payload)
    except Exception as e:
        final = isinstance(e, LookupError) or attempts >= max_attempts
        log.warning("job %s (%s) attempt %s failed: %s", job_id, kind, attempts, e)
        with connect() as conn:
            conn.execute(
                "UPDATE jobs SET status = %s, error = %s, locked_at = NULL, locked_by = NULL, "
                "run_after = now() + make_interval(secs => %s), finished_at = CASE WHEN %s THEN now() END WHERE id = %s",
                ("failed" if final else "queued", str(e)[:2000], BACKOFF_SECONDS * 2 ** (attempts - 1), final, job_id))
        return True
    with connect() as conn:
        conn.execute("UPDATE jobs SET status = 'done', error = NULL, locked_at = NULL, finished_at = now() WHERE id = %s", (job_id,))
    return True


def recent(limit: int = 50) -> list[dict]:
    with connect() as conn:
        rows = conn.execute("SELECT id, kind, payload, status, attempts, max_attempts, error, created_at, finished_at, run_after "
                            "FROM jobs ORDER BY id DESC LIMIT %s", (limit,)).fetchall()
    keys = ["id", "kind", "payload", "status", "attempts", "max_attempts", "error", "created_at", "finished_at", "run_after"]
    return [dict(zip(keys, r)) for r in rows]
