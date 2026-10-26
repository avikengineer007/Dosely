"""
dosely/background.py
====================
Background tasks for Dosely: periodic purge of old prescription data.

Usage (via FastAPI lifespan)
----------------------------
    from dosely.background import purge_scheduler_context

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        async with purge_scheduler_context(repo, storage):
            yield

Public API
----------
    run_purge_job(repo, storage, days)
        Synchronous function: run one purge tick.  Swallows all exceptions
        so the scheduler loop is never terminated by a single failure.

    purge_scheduler_context(repo, storage, days, interval_hours)
        Async context manager: starts a background asyncio task that calls
        run_purge_job every `interval_hours`, and cancels it on exit.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# One purge tick
# ---------------------------------------------------------------------------

def run_purge_job(
    repo: Any,
    storage: Any,
    days: int = 30,
) -> int:
    """
    Run one purge tick: delete prescriptions older than `days`.

    Logs only the count of purged records, never their content or IDs.
    Swallows all exceptions so the background scheduler survives transient
    errors (locked DB, I/O failures, etc.) and retries on the next interval.

    Returns the number of records purged (0 on error).
    """
    try:
        purged = repo.purge_older_than(days=days, storage=storage)
        if purged:
            logger.info("Purge job completed: purged=%d records older than %d days.", purged, days)
        else:
            logger.debug("Purge job: no records older than %d days found.", days)
        return purged
    except Exception:  # noqa: BLE001
        logger.exception("Purge job encountered an error; will retry on next interval.")
        return 0


# ---------------------------------------------------------------------------
# Scheduler context manager
# ---------------------------------------------------------------------------

@contextlib.asynccontextmanager
async def purge_scheduler_context(
    repo: Any,
    storage: Any,
    days: int = 30,
    interval_hours: float = 24.0,
):
    """
    Async context manager that runs the purge job periodically.

    Starts an asyncio background task on entry; cancels it cleanly on exit.
    The first tick fires immediately (at startup) and then every
    `interval_hours` thereafter.

    Example::

        async with purge_scheduler_context(repo, storage, days=30):
            ...  # application runs here
    """
    interval_seconds = interval_hours * 3600
    task: Optional[asyncio.Task] = None

    async def _loop() -> None:
        while True:
            # Run immediately on first iteration, then sleep
            await asyncio.get_event_loop().run_in_executor(
                None,
                run_purge_job,
                repo,
                storage,
                days,
            )
            await asyncio.sleep(interval_seconds)

    task = asyncio.create_task(_loop(), name="dosely-purge-job")
    logger.info(
        "Purge background job started: interval_hours=%s days=%d.",
        interval_hours,
        days,
    )
    try:
        yield
    finally:
        if task and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        logger.info("Purge background job stopped.")
