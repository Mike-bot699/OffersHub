from fastapi import APIRouter
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.common import DB
from app.core.config import get_settings
from app.core.security import encryption_available
from app.models import Store
from app.models.enums import ExperimentStatus, Objective, OfferType, StrategyType
from app.services import backup
from app.services.audit import audit
from app.services.demo import DEMO_CODE, reset_demo
from app.strategy import llm

router = APIRouter(prefix="/api", tags=["admin"])
VERSION = "0.1.0"


@router.get("/health")
def health():
    return {"ok": True, "version": VERSION}


@router.get("/meta")
def meta():
    s = get_settings()
    return {
        "version": VERSION,
        "env": s.env,
        "auth_mode": s.auth_mode,
        "encryption_configured": encryption_available(),
        "ai_available": llm.available(),
        "ai_model": s.ai_model if llm.available() else None,
        "shopify_api_version": s.shopify_api_version,
        "database": "sqlite" if s.is_sqlite else "postgresql",
        "scheduler_enabled": s.scheduler_enabled,
        "objectives": [o.value for o in Objective],
        "strategy_types": [t.value for t in StrategyType],
        "offer_types": [t.value for t in OfferType],
        "experiment_statuses": [x.value for x in ExperimentStatus],
        "write_actions_enabled": False,
    }


@router.post("/demo/reset")
def demo_reset(db: Session = DB):
    store = reset_demo(db)
    from app.strategy.engine import run_store

    run_store(db, store)
    audit(db, "demo.reset", "store", store.id, store_id=store.id)
    db.commit()
    return {"store_id": store.id, "code": store.code}


@router.delete("/demo")
def demo_delete(db: Session = DB):
    s = db.scalar(select(Store).where(Store.code == DEMO_CODE, Store.is_demo.is_(True)))
    if s:
        db.delete(s)
        audit(db, "demo.deleted", "store", None)
        db.commit()
    return {"deleted": bool(s)}


@router.post("/admin/backups")
def make_backup(db: Session = DB):
    res = backup.create_backup()
    audit(db, "backup.created", "database", None, details=res)
    db.commit()
    return res


@router.get("/admin/backups")
def backups():
    return backup.list_backups()
