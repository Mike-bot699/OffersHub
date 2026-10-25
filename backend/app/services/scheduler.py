"""In-process scheduler for automatic synchronisation.

One APScheduler job ticks every few minutes and starts a sync for each store whose
interval has elapsed. Running inside the API process keeps local use simple; a hosted
deployment with several workers should set EGI_SCHEDULER_ENABLED=false on all but one.
"""

import logging
from datetime import timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy import select

from app.core.errors import ConflictError
from app.db.base import utcnow
from app.db.session import session_scope
from app.models import Integration, Store, SyncJob
from app.models.enums import IntegrationStatus
from app.services.sync import _aware, run_sync

log = logging.getLogger(__name__)
_scheduler: BackgroundScheduler | None = None
TICK_MINUTES = 5


def due_store_ids() -> list[int]:
    now = utcnow()
    due = []
    with session_scope() as db:
        rows = db.execute(
            select(Integration, Store).join(Store, Store.id == Integration.store_id).where(
                Integration.sync_enabled.is_(True),
                Integration.status.in_([IntegrationStatus.CONNECTED, IntegrationStatus.ERROR]),
                Store.is_demo.is_(False),
                Store.status == "active",
            )
        ).all()
        for integ, store in rows:
            last = db.scalar(select(SyncJob.started_at).where(SyncJob.store_id == store.id)
                             .order_by(SyncJob.started_at.desc()).limit(1))
            if last is None or now - _aware(last) >= timedelta(minutes=integ.sync_interval_minutes):
                due.append(store.id)
    return due


def tick() -> None:
    for store_id in due_store_ids():
        try:
            with session_scope() as db:
                job = run_sync(db, store_id, trigger="scheduled")
                log.info("Scheduled sync store=%s status=%s", store_id, job.status)
        except ConflictError:
            log.info("Store %s already syncing; skipped this tick", store_id)
        except Exception:
            log.exception("Scheduled sync crashed for store %s", store_id)


def start() -> None:
    global _scheduler
    if _scheduler is not None:
        return
    _scheduler = BackgroundScheduler(timezone="UTC")
    _scheduler.add_job(tick, "interval", minutes=TICK_MINUTES, id="sync-tick", max_instances=1, coalesce=True)
    _scheduler.start()
    log.info("Sync scheduler started (tick every %s min)", TICK_MINUTES)


def shutdown() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
