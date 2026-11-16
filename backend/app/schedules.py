"""Periodic work registry. Features register a function; it's called about once a minute.

    from . import schedules
    @schedules.every_minute
    def run_due_reports(): ...

The worker (app/worker.py) calls `tick()` once a minute: inside the API with WORKER_MODE=inline, else in `python -m app.worker`.
Keep these quick (e.g. queue a job with app/jobs.py); slow work belongs in a job.
"""
import logging

log = logging.getLogger("schedules")
PERIODIC: list = []


def every_minute(fn):
    PERIODIC.append(fn)
    return fn


def tick() -> None:
    for fn in list(PERIODIC):
        try:
            fn()
        except Exception as e:  # one failing job must not stop the others
            log.warning("periodic %s failed: %s", getattr(fn, "__name__", fn), e)
