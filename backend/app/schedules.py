"""Periodic work registry. Features register a function; it's called about once a minute.

    from . import schedules
    @schedules.every_minute
    def run_due_reports(): ...

Today a background thread in the API process calls them; a dedicated worker can call `tick()` instead.
"""
import logging
import os
import threading
import time

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


def start() -> None:
    if os.getenv("SCHEDULES_DISABLED") == "1":
        return

    def loop():
        while True:
            time.sleep(60)
            tick()

    threading.Thread(target=loop, daemon=True, name="schedules").start()
