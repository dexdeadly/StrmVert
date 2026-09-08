"""The recurring catalog-sync job, reconfigurable at runtime from Settings."""

from __future__ import annotations

import logging

from sqlalchemy import select

from app.db import SessionLocal
from app.models import XCServer
from app.settings_store import effective
from app.sync import sync_server

log = logging.getLogger("strmvert.scheduler")

_JOB_ID = "catalog-sync"
_scheduler = None  # AsyncIOScheduler | None


async def _run_all() -> None:
    with SessionLocal() as session:
        ids = list(session.scalars(select(XCServer.id).where(XCServer.is_active.is_(True))))
    log.info("scheduled sync: %d active server(s)", len(ids))
    for server_id in ids:
        await sync_server(server_id)


def start() -> None:
    """Create + start the scheduler (idempotent), then apply the current interval."""
    global _scheduler
    if _scheduler is None:
        try:
            from apscheduler.schedulers.asyncio import AsyncIOScheduler

            _scheduler = AsyncIOScheduler()
            _scheduler.start()
        except Exception as exc:  # noqa: BLE001 — never let this break startup
            log.warning("could not start scheduler: %s", exc)
            _scheduler = None
            return
    apply_interval()


def shutdown() -> None:
    global _scheduler
    if _scheduler is not None:
        try:
            _scheduler.shutdown(wait=False)
        finally:
            _scheduler = None


def apply_interval() -> int:
    """(Re)configure the recurring sync from the current effective settings.

    Returns the interval in hours (0 = disabled). Safe to call any time after
    :func:`start`; called on save from the Settings page.
    """
    if _scheduler is None:
        return 0
    hours = max(0, effective().sync_interval_hours)
    job = _scheduler.get_job(_JOB_ID)
    if hours <= 0:
        if job is not None:
            _scheduler.remove_job(_JOB_ID)
            log.info("auto-sync disabled")
        return 0
    if job is None:
        _scheduler.add_job(_run_all, "interval", hours=hours, id=_JOB_ID)
    else:
        _scheduler.reschedule_job(_JOB_ID, trigger="interval", hours=hours)
    log.info("auto-sync every %d h", hours)
    return hours
