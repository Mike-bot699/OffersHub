"""Settings → Stores: identity, storefront host and the Eli Z connection of each store.

The Eli Z API key is write-only: responses carry only its last four characters."""

import re
import threading

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.common import DB, get_store
from app.core.errors import AuthError, ConflictError, ValidationError
from app.db.session import session_scope
from app.models import Product, Store
from app.services import elizeta as ez_svc
from app.services.audit import audit

router = APIRouter(prefix="/api", tags=["settings"])
MAX_LOGO_BYTES = 200_000


class StoreSettingsIn(BaseModel):
    model_config = {"extra": "forbid"}
    name: str | None = Field(default=None, min_length=1, max_length=200)
    code: str | None = Field(default=None, min_length=2, max_length=32, pattern=r"^[A-Za-z0-9_\-]+$")
    logo: str | None = None  # data URL; "" removes it
    product_host: str | None = None
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    elizeta_shop_id: str | None = None
    elizeta_api_key: str | None = None  # blank keeps the stored key
    tag_min_ranked_products: int | None = Field(default=None, ge=0, le=1000)


def _host(v: str) -> str | None:
    v = v.strip().lower()
    if not v:
        return None
    v = re.sub(r"^https?://", "", v).split("/")[0]
    if not re.fullmatch(r"[a-z0-9.-]+\.[a-z]{2,}", v):
        raise ValidationError("Product host must be a domain such as thelittlelearnerstoys.com.")
    return v


def settings_out(db: Session, s: Store) -> dict:
    integ = ez_svc.get(db, s.id)
    return {"id": s.id, "name": s.name, "code": s.code, "logo": s.logo, "product_host": s.product_host,
            "currency": s.currency, "is_demo": s.is_demo,
            "tag_min_ranked_products": (s.constraints or {}).get("tag_min_ranked_products"),
            "elizeta": ez_svc.describe(integ)}


@router.get("/settings/stores")
def list_store_settings(db: Session = DB):
    return [settings_out(db, s) for s in db.scalars(select(Store).order_by(Store.is_demo, Store.code))]


@router.put("/stores/{store_id}/settings")
def save_store_settings(store_id: int, body: StoreSettingsIn, db: Session = DB):
    s = get_store(db, store_id)
    ch = body.model_dump(exclude_unset=True)
    if s.is_demo and ({"elizeta_shop_id", "elizeta_api_key"} & ch.keys()):
        raise ValidationError("The demo store cannot be connected to a platform.")
    if "code" in ch and ch["code"] and ch["code"].upper() != s.code:
        code = ch["code"].upper()
        if db.scalar(select(Store).where(Store.code == code, Store.id != s.id)):
            raise ConflictError(f"Another store already uses the code {code}.")
        s.code = code
    if ch.get("name"):
        s.name = ch["name"].strip()
    if ch.get("currency"):
        s.currency = ch["currency"].upper()
    if "logo" in ch:
        logo = ch["logo"] or None
        if logo and (not logo.startswith("data:image/") or len(logo) > MAX_LOGO_BYTES * 4 // 3 + 100):
            raise ValidationError("The icon must be an image of at most 200 KB.")
        s.logo = logo
    if "product_host" in ch:
        s.product_host = _host(ch["product_host"] or "")
    if "tag_min_ranked_products" in ch:
        s.constraints = {**(s.constraints or {}), "tag_min_ranked_products": ch["tag_min_ranked_products"]}
    if "elizeta_shop_id" in ch or ch.get("elizeta_api_key"):
        ez_svc.configure(db, s, shop_id=ch.get("elizeta_shop_id"), api_key=ch.get("elizeta_api_key"))
    audit(db, "store.settings_updated", "store", s.id, store_id=s.id,
          details={"fields": sorted(k for k in ch if k != "elizeta_api_key"),
                   "api_key_changed": bool(ch.get("elizeta_api_key"))})
    db.commit()
    return settings_out(db, s)


@router.post("/stores/{store_id}/elizeta/verify")
def verify_elizeta(store_id: int, db: Session = DB):
    s = get_store(db, store_id)
    integ = ez_svc.get(db, s.id)
    if not integ:
        raise ValidationError("Save the Eli Z shop id and API key first.")
    try:
        ez_svc.verify(db, integ)
    finally:
        db.commit()
    return settings_out(db, s)


def _bg_sync(store_id: int) -> None:
    with session_scope() as db:
        ez_svc.sync(db, db.get(Store, store_id))


@router.post("/stores/{store_id}/elizeta/sync", status_code=202)
def sync_elizeta(store_id: int, db: Session = DB):
    s = get_store(db, store_id)
    integ = ez_svc.get(db, s.id)
    if not integ or not integ.credentials_encrypted:
        raise ValidationError("Save the Eli Z shop id and API key first.")
    from app.core.config import get_settings

    if get_settings().serverless:  # no background threads on Vercel: run it within the request
        job = ez_svc.sync(db, s)
        return {"started": True, "finished": True, "status": job.status, "stats": job.stats}
    threading.Thread(target=_bg_sync, args=(s.id,), daemon=True).start()
    return {"started": True}


@router.get("/cron/sync")
def cron_sync(request: Request, db: Session = DB):
    """Daily sync of every Eli Z store, called by Vercel Cron with `Authorization: Bearer <CRON_SECRET>`."""
    import hmac
    import os

    secret = os.environ.get("CRON_SECRET")
    if not secret or not hmac.compare_digest(request.headers.get("authorization", ""), f"Bearer {secret}"):
        raise AuthError("Not authorised.")
    from app.models import Integration

    out = {}
    for integ in db.scalars(select(Integration).where(Integration.provider == "elizeta",
                                                      Integration.credentials_encrypted.is_not(None),
                                                      Integration.sync_enabled.is_(True))):
        store = db.get(Store, integ.store_id)
        if store and not store.is_demo:
            out[store.code] = ez_svc.sync(db, store, trigger="scheduled").status
    return {"synced": out}


@router.post("/stores/{store_id}/elizeta/disconnect")
def disconnect_elizeta(store_id: int, db: Session = DB):
    s = get_store(db, store_id)
    integ = ez_svc.get(db, s.id)
    if integ:
        ez_svc.disconnect(db, integ)
        db.commit()
    return settings_out(db, s)


class ProductTagsIn(BaseModel):
    manual_tags: list[str] = Field(max_length=20)


@router.put("/products/{product_id}/tags")
def set_product_tags(product_id: int, body: ProductTagsIn, db: Session = DB):
    p = db.get(Product, product_id)
    if not p:
        raise ValidationError("Product not found.")
    tags = []
    for t in body.manual_tags:
        t = t.strip()[:40]
        if t and t.lower() not in {x.lower() for x in tags}:
            tags.append(t)
    p.manual_tags = tags
    audit(db, "product.tags_updated", "product", p.id, store_id=p.store_id, details={"manual_tags": tags})
    db.commit()
    return {"id": p.id, "manual_tags": p.manual_tags}


@router.post("/stores/{store_id}/product-tags/refresh")
def refresh_product_tags(store_id: int, db: Session = DB):
    s = get_store(db, store_id)
    res = ez_svc.refresh_tags(db, s)
    db.commit()
    return res


@router.get("/stores/{store_id}/catalog")
def live_catalog(store_id: int, db: Session = DB):
    """Live products only (active in the catalog), with performance and manual tags."""
    from app.analytics.product_tags import TAGS
    from app.models import Variant

    s = get_store(db, store_id)
    prices: dict[int, list[float]] = {}
    for v in db.scalars(select(Variant).where(Variant.store_id == s.id)):
        if v.price is not None:
            prices.setdefault(v.product_id, []).append(v.price)
    rows = []
    for p in db.scalars(select(Product).where(Product.store_id == s.id, Product.status == "ACTIVE")):
        pr = prices.get(p.id, [])
        rows.append({"id": p.id, "title": p.title, "handle": p.handle, "product_type": p.product_type,
                     "url": f"https://{s.product_host}/products/{p.handle}" if s.product_host and p.handle else None,
                     "price_min": min(pr) if pr else None, "price_max": max(pr) if pr else None,
                     "perf_tag": p.perf_tag, "perf_label": TAGS.get(p.perf_tag) if p.perf_tag else None,
                     "perf_rank": p.perf_rank, "units_30d": p.perf_units_30d, "revenue_30d": p.perf_revenue_30d,
                     "manual_tags": p.manual_tags or [], "source": p.source})
    rows.sort(key=lambda r: (r["perf_rank"] is None, r["perf_rank"] or 0, r["title"]))
    c = s.constraints or {}
    return {"products": rows, "currency": s.currency, "tags_basis": c.get("tags_basis"),
            "tags_computed_at": c.get("tags_computed_at"),
            "min_ranked": c.get("tag_min_ranked_products", 10),
            "rule": "Last 30 days of sales. Revenue = current price × units (report revenue if no price). Products with "
                    "revenue > 0 ranked by revenue, then units: top 10% Best Seller, 10–25% Top Seller, 25–50% Popular. "
                    "Free = sold at $0. No bands when fewer products than the minimum have sales."}
