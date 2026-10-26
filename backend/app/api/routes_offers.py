from datetime import date
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.common import DB, Period, get_store, to_dict
from app.core.errors import NotFoundError, ValidationError
from app.models import Coupon, Offer, PromoEvent, Store
from app.models.enums import DataSource, OfferType
from app.offers.monitoring import alerts, measure_event, offer_history_stats, verify_event
from app.offers.performance import discount_analysis, offer_performance
from app.services.audit import audit

router = APIRouter(prefix="/api", tags=["offers"])

EVENT_STATUSES = "^(planned|live|ended|cancelled|not_launched|undocumented)$"


class OfferIn(BaseModel):
    store_id: int | None = None
    name: str = Field(min_length=1, max_length=300)
    offer_type: OfferType
    mechanic: dict[str, Any] = {}
    description: str | None = None
    objective: str | None = None
    target_audience: str | None = None
    eligible_scope: dict[str, Any] = {"type": "all"}
    est_incentive_cost: float | None = None
    status: str = Field(default="active", pattern="^(draft|active|archived)$")


class EventIn(BaseModel):
    store_id: int
    offer_id: int | None = None
    name: str = Field(min_length=1, max_length=300)
    offer_text: str | None = None
    offer_type: OfferType | None = None
    start_date: date | None = None
    end_date: date | None = None
    status: str = Field(default="planned", pattern=EVENT_STATUSES)
    objective: str | None = None
    target_audience: str | None = None
    channels: list[str] = []
    display: str | None = None
    activation: str | None = Field(default=None, pattern="^(manual_code|automatic)$")
    coupon_codes: list[str] = []
    threshold_amount: float | None = None
    bonus: str | None = None
    eligible_products: list[Any] = []
    est_incentive_cost: float | None = None
    targets: dict[str, Any] = {}
    offer_orders: int | None = None
    total_orders: int | None = None
    offer_sales: float | None = None
    analysis: str | None = None
    conclusions: str | None = None
    notes: str | None = None


class EventPatch(BaseModel):
    model_config = {"extra": "forbid"}
    offer_id: int | None = None
    name: str | None = None
    offer_text: str | None = None
    offer_type: OfferType | None = None
    start_date: date | None = None
    end_date: date | None = None
    status: str | None = Field(default=None, pattern=EVENT_STATUSES)
    objective: str | None = None
    target_audience: str | None = None
    channels: list[str] | None = None
    display: str | None = None
    activation: str | None = None
    coupon_codes: list[str] | None = None
    threshold_amount: float | None = None
    bonus: str | None = None
    est_incentive_cost: float | None = None
    targets: dict[str, Any] | None = None
    offer_orders: int | None = None
    total_orders: int | None = None
    offer_sales: float | None = None
    analysis: str | None = None
    conclusions: str | None = None
    notes: str | None = None


@router.get("/offer-types")
def offer_types():
    return [t.value for t in OfferType]


@router.get("/offers")
def list_offers(store_id: int | None = None, db: Session = DB):
    q = select(Offer).order_by(Offer.name)
    if store_id:
        q = q.where((Offer.store_id == store_id) | Offer.store_id.is_(None))
    offers = db.scalars(q).all()
    counts: dict[int, int] = {}
    for oid in db.scalars(select(PromoEvent.offer_id).where(PromoEvent.offer_id.is_not(None))):
        counts[oid] = counts.get(oid, 0) + 1
    return [to_dict(o) | {"event_count": counts.get(o.id, 0)} for o in offers]


@router.post("/offers", status_code=201)
def create_offer(body: OfferIn, db: Session = DB):
    if body.store_id:
        get_store(db, body.store_id)
    o = Offer(**body.model_dump(), source=DataSource.MANUAL)
    db.add(o)
    db.flush()
    audit(db, "offer.created", "offer", o.id, store_id=o.store_id)
    db.commit()
    return to_dict(o)


@router.patch("/offers/{offer_id}")
def update_offer(offer_id: int, body: OfferIn, db: Session = DB):
    o = db.get(Offer, offer_id)
    if o is None:
        raise NotFoundError("Offer not found.")
    for k, v in body.model_dump().items():
        setattr(o, k, v)
    audit(db, "offer.updated", "offer", o.id, store_id=o.store_id)
    db.commit()
    return to_dict(o)


@router.get("/events")
def list_events(store_id: int | None = None, status: str | None = None, start: date | None = None,
                end: date | None = None, include_demo: bool = False, db: Session = DB):
    q = select(PromoEvent, Store.code, Store.currency, Store.is_demo).join(Store, Store.id == PromoEvent.store_id)
    if store_id:
        q = q.where(PromoEvent.store_id == store_id)
    elif not include_demo:
        q = q.where(Store.is_demo.is_(False))
    if status:
        q = q.where(PromoEvent.status == status)
    if start:
        q = q.where((PromoEvent.end_date.is_(None)) | (PromoEvent.end_date >= start))
    if end:
        q = q.where((PromoEvent.start_date.is_(None)) | (PromoEvent.start_date <= end))
    q = q.order_by(PromoEvent.start_date.desc().nulls_last())
    return [to_dict(e) | {"store_code": code, "currency": cur, "is_demo": demo,
                          "offer_order_share": (e.offer_orders / e.total_orders)
                          if e.offer_orders is not None and e.total_orders else None}
            for e, code, cur, demo in db.execute(q)]


@router.post("/events", status_code=201)
def create_event(body: EventIn, db: Session = DB):
    get_store(db, body.store_id)
    _check_dates(body.start_date, body.end_date)
    data = body.model_dump()
    data["coupon_codes"] = [c.strip().upper() for c in data["coupon_codes"] if c.strip()]
    e = PromoEvent(**data, source=DataSource.MANUAL,
                   results_source=DataSource.MANUAL if body.offer_orders is not None else None)
    db.add(e)
    db.flush()
    audit(db, "event.created", "promo_event", e.id, store_id=e.store_id)
    db.commit()
    return to_dict(e)


@router.patch("/events/{event_id}")
def update_event(event_id: int, body: EventPatch, db: Session = DB):
    e = _event(db, event_id)
    changes = body.model_dump(exclude_unset=True)
    if "coupon_codes" in changes and changes["coupon_codes"] is not None:
        changes["coupon_codes"] = [c.strip().upper() for c in changes["coupon_codes"] if c.strip()]
    for k, v in changes.items():
        setattr(e, k, v)
    _check_dates(e.start_date, e.end_date)
    if {"offer_orders", "total_orders", "offer_sales"} & changes.keys():
        e.results_source = DataSource.MANUAL
    if {"coupon_codes", "threshold_amount", "offer_text", "start_date", "end_date"} & changes.keys():
        e.verification_status = "documented_only"
    audit(db, "event.updated", "promo_event", e.id, store_id=e.store_id, details={"fields": sorted(changes)})
    db.commit()
    return to_dict(e)


@router.delete("/events/{event_id}")
def delete_event(event_id: int, db: Session = DB):
    e = _event(db, event_id)
    audit(db, "event.deleted", "promo_event", e.id, store_id=e.store_id, details={"name": e.name})
    db.delete(e)
    db.commit()
    return {"deleted": True}


@router.post("/events/{event_id}/verify")
def verify(event_id: int, db: Session = DB):
    e = _event(db, event_id)
    result = verify_event(db, e, db.get(Store, e.store_id))
    db.commit()
    return result


@router.get("/events/{event_id}/results")
def results(event_id: int, db: Session = DB):
    e = _event(db, event_id)
    return {"documented": {"offer_orders": e.offer_orders, "total_orders": e.total_orders,
                           "offer_sales": e.offer_sales, "source": e.results_source, "data_issues": e.data_issues},
            "measured": measure_event(db, e, db.get(Store, e.store_id))}


@router.get("/alerts")
def all_alerts(store_id: int | None = None, include_demo: bool = False, db: Session = DB):
    if store_id:
        return alerts(db, get_store(db, store_id))
    q = select(Store).where(Store.status == "active")
    if not include_demo:
        q = q.where(Store.is_demo.is_(False))
    out = []
    for s in db.scalars(q):
        out.extend(alerts(db, s))
    return sorted(out, key=lambda a: {"critical": 0, "warning": 1, "info": 2}[a["severity"]])


@router.get("/coupons")
def coupons(store_id: int | None = None, source: str | None = None, db: Session = DB):
    q = select(Coupon, Store.code).join(Store, Store.id == Coupon.store_id).order_by(Coupon.code)
    if store_id:
        q = q.where(Coupon.store_id == store_id)
    if source:
        q = q.where(Coupon.source == source)
    return [to_dict(c, exclude={"raw"}) | {"store_code": code} for c, code in db.execute(q.limit(2000))]


@router.get("/offer-history")
def history(store_id: int | None = None, include_demo: bool = False, db: Session = DB):
    if store_id:
        ids = [store_id]
    else:
        q = select(Store.id)
        if not include_demo:
            q = q.where(Store.is_demo.is_(False))
        ids = list(db.scalars(q))
    return offer_history_stats(db, ids)


@router.get("/stores/{store_id}/discount-analysis")
def discount_analysis_route(store_id: int, period: tuple[date, date] = Period, db: Session = DB):
    return discount_analysis(db, get_store(db, store_id), *period)


@router.get("/stores/{store_id}/offer-performance")
def offer_performance_route(store_id: int, days: int = 365, db: Session = DB):
    from datetime import timedelta

    return offer_performance(db, get_store(db, store_id), since=date.today() - timedelta(days=days))


def _event(db: Session, event_id: int) -> PromoEvent:
    e = db.get(PromoEvent, event_id)
    if e is None:
        raise NotFoundError("Event not found.")
    return e


def _check_dates(start, end):
    if start and end and end < start:
        raise ValidationError("End date is before start date.")
