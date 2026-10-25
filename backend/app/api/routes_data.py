"""Integrations, synchronisation and file imports."""

import logging
import threading
from dataclasses import asdict

from fastapi import APIRouter, File, Form, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.common import DB, get_store, to_dict
from app.core.errors import ConflictError, ValidationError
from app.db.session import session_scope
from app.models import ImportBatch, SyncJob
from app.services import integrations as svc
from app.services import sync as sync_svc
from app.services.audit import audit
from app.services.csv_import import import_costs, import_traffic
from app.services.offers_import import import_offers_workbook

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["data"])
MAX_UPLOAD = 20 * 1024 * 1024


class ShopifyConfigIn(BaseModel):
    shop_domain: str
    auth_method: str = Field(pattern="^(admin_token|client_credentials)$")
    access_token: str | None = None
    client_id: str | None = None
    client_secret: str | None = None


class IntegrationSettingsIn(BaseModel):
    sync_enabled: bool | None = None
    sync_interval_minutes: int | None = Field(default=None, ge=30, le=10080)
    initial_history_days: int | None = Field(default=None, ge=7, le=3650)


class DisconnectIn(BaseModel):
    purge_data: bool = False
    confirm_store_code: str


class SyncIn(BaseModel):
    full: bool = False
    resources: list[str] = list(sync_svc.RESOURCES)


@router.put("/stores/{store_id}/integration")
def configure(store_id: int, body: ShopifyConfigIn, db: Session = DB):
    store = get_store(db, store_id)
    integ = svc.configure_shopify(db, store, shop_domain=body.shop_domain, auth_method=body.auth_method,
                                  access_token=body.access_token, client_id=body.client_id,
                                  client_secret=body.client_secret)
    db.commit()
    # Verify immediately against the live API; the result (success or error) is persisted either way.
    try:
        svc.verify(db, integ)
    finally:
        db.commit()
    return svc.describe(integ)


@router.post("/stores/{store_id}/integration/verify")
def verify(store_id: int, db: Session = DB):
    integ = svc.require_integration(db, store_id)
    try:
        svc.verify(db, integ)
    finally:
        db.commit()
    return svc.describe(integ)


@router.patch("/stores/{store_id}/integration")
def integration_settings(store_id: int, body: IntegrationSettingsIn, db: Session = DB):
    integ = svc.require_integration(db, store_id)
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(integ, k, v)
    audit(db, "integration.settings", "integration", integ.id, store_id=store_id,
          details=body.model_dump(exclude_unset=True))
    db.commit()
    return svc.describe(integ)


@router.post("/stores/{store_id}/integration/disconnect")
def disconnect(store_id: int, body: DisconnectIn, db: Session = DB):
    store = get_store(db, store_id)
    if body.confirm_store_code.strip().upper() != store.code:
        raise ValidationError("Type the store code to confirm.")
    integ = svc.require_integration(db, store_id)
    svc.disconnect(db, integ, purge_data=body.purge_data)
    db.commit()
    return {**svc.describe(integ),
            "next_steps": "Local credentials were deleted. To revoke access completely, uninstall the app in "
                          "Shopify admin (or rotate the client secret in the Dev Dashboard)."}


@router.post("/stores/{store_id}/sync", status_code=202)
def start_sync(store_id: int, body: SyncIn, db: Session = DB):
    store = get_store(db, store_id)
    if store.is_demo:
        raise ValidationError("Demo stores have no live connection to sync.")
    bad = set(body.resources) - set(sync_svc.RESOURCES)
    if bad:
        raise ValidationError(f"Unknown resources: {', '.join(sorted(bad))}")
    svc.require_integration(db, store_id)
    if sync_svc._locks[store_id].locked():
        raise ConflictError("A synchronisation is already running for this store.")

    def work():
        try:
            with session_scope() as s:
                sync_svc.run_sync(s, store_id, trigger="manual", resources=body.resources, full=body.full)
        except Exception:
            log.exception("Manual sync failed for store %s", store_id)

    threading.Thread(target=work, name=f"sync-{store_id}", daemon=True).start()
    return {"started": True}


@router.get("/stores/{store_id}/sync-jobs")
def sync_jobs(store_id: int, limit: int = 20, db: Session = DB):
    get_store(db, store_id)
    jobs = db.scalars(select(SyncJob).where(SyncJob.store_id == store_id)
                      .order_by(SyncJob.started_at.desc()).limit(limit))
    return {"jobs": [to_dict(j) for j in jobs], "freshness": sync_svc.freshness(db, get_store(db, store_id)),
            "running": sync_svc._locks[store_id].locked()}


async def _read(file: UploadFile) -> bytes:
    content = await file.read()
    if len(content) > MAX_UPLOAD:
        raise ValidationError("File is larger than 20 MB.")
    if not content:
        raise ValidationError("File is empty.")
    return content


@router.post("/imports/offers-workbook")
async def import_workbook(file: UploadFile = File(...), dry_run: bool = Form(True), force: bool = Form(False),
                          create_missing_stores: bool = Form(True), db: Session = DB):
    content = await _read(file)
    rep = import_offers_workbook(db, content, file.filename or "upload", dry_run=dry_run, force=force,
                                 create_missing_stores=create_missing_stores)
    out = asdict(rep)
    out["issues"] = out["issues"][:500]
    return out


@router.post("/stores/{store_id}/imports/{kind}")
async def import_csv(store_id: int, kind: str, file: UploadFile = File(...), dry_run: bool = Form(True),
                     force: bool = Form(False), db: Session = DB):
    store = get_store(db, store_id)
    content = await _read(file)
    if kind == "costs":
        rep = import_costs(db, store, content, file.filename or "costs.csv", dry_run=dry_run, force=force)
    elif kind == "traffic":
        rep = import_traffic(db, store, content, file.filename or "traffic.csv", dry_run=dry_run, force=force)
    else:
        raise ValidationError("kind must be 'costs' or 'traffic'.")
    out = asdict(rep)
    out["issues"] = out["issues"][:500]
    return out


@router.get("/imports")
def list_imports(store_id: int | None = None, db: Session = DB):
    q = select(ImportBatch).order_by(ImportBatch.created_at.desc()).limit(100)
    if store_id:
        q = q.where(ImportBatch.store_id == store_id)
    return [to_dict(b, exclude={"warnings"}) | {"warning_count": len(b.warnings or [])} for b in db.scalars(q)]


@router.get("/imports/{batch_id}")
def get_import(batch_id: int, db: Session = DB):
    b = db.get(ImportBatch, batch_id)
    if b is None:
        from app.core.errors import NotFoundError

        raise NotFoundError("Import not found.")
    return to_dict(b)
