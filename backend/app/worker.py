"""Background worker: runs queued jobs and the once-a-minute periodic work (schedules.tick).

WORKER_MODE=inline (default): the API runs this in background threads, so dev needs no extra process.
WORKER_MODE=external: the API only queues jobs; run `python -m app.worker` (one or more) to process them.
"""
import json
import logging
import os
import signal
import threading

from . import jobs, schedules
from .db import connect

log = logging.getLogger("worker")
WORKER_MODE = os.getenv("WORKER_MODE", "inline")
CONCURRENCY = max(1, int(os.getenv("WORKER_CONCURRENCY", "2")))  # jobs run at the same time per process
POLL_SECONDS = 2
TICK_SECONDS = 60
HEARTBEAT_SECONDS = 15
_stop = threading.Event()


def heartbeat() -> None:
    with connect() as conn:
        conn.execute("INSERT INTO app_settings (key, value) VALUES ('worker_heartbeat', %s) ON CONFLICT (key) DO UPDATE "
                     "SET value = EXCLUDED.value, updated_at = now()", (json.dumps({"id": jobs.WORKER_ID, "mode": WORKER_MODE}),))


def claim_tick() -> bool:
    """True for exactly one worker per minute, however many are running."""
    with connect() as conn:
        return conn.execute(
            "INSERT INTO app_settings (key, value) VALUES ('schedules_tick', %s) ON CONFLICT (key) DO UPDATE "
            "SET value = EXCLUDED.value, updated_at = now() WHERE app_settings.updated_at < now() - make_interval(secs => %s) "
            "RETURNING key", (json.dumps({"id": jobs.WORKER_ID}), TICK_SECONDS - 5)).fetchone() is not None


def _ticker() -> None:
    while not _stop.is_set():
        try:
            heartbeat()
            if os.getenv("SCHEDULES_DISABLED") != "1" and claim_tick():
                schedules.tick()
        except Exception as e:  # database briefly away: try again next round
            log.warning("ticker: %s", e)
        _stop.wait(HEARTBEAT_SECONDS)


def _runner() -> None:
    while not _stop.is_set():
        try:
            busy = jobs.work_once()
        except Exception as e:
            log.warning("worker: %s", e)
            busy = False
        if not busy:
            _stop.wait(POLL_SECONDS)


def _threads(daemon: bool) -> list[threading.Thread]:
    ts = [threading.Thread(target=_ticker, daemon=daemon, name="worker-ticker")]
    ts += [threading.Thread(target=_runner, daemon=daemon, name=f"worker-{i}") for i in range(CONCURRENCY)]
    for t in ts:
        t.start()
    return ts


def start() -> None:
    """Called by the API on startup: in inline mode, process jobs inside the API process."""
    if WORKER_MODE == "inline":
        _threads(daemon=True)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    from .db import migrate
    migrate()
    from . import main as _app  # noqa: F401  imports every feature module, registering their job handlers and schedules
    signal.signal(signal.SIGTERM, lambda *_: _stop.set())
    signal.signal(signal.SIGINT, lambda *_: _stop.set())
    log.info("worker %s started (%s job threads)", jobs.WORKER_ID, CONCURRENCY)
    for t in _threads(daemon=False):
        t.join()  # each thread finishes its current job, then exits
    from .db import pool
    pool().close()


if __name__ == "__main__":
    main()
