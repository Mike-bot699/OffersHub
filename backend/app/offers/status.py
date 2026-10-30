"""Offer status management.

Two statuses are kept apart on every offer (PromoEvent):

* Offer status (operational): in_progress, scheduled, live, paused, completed, cancelled — or
  NULL ("not set"). People set it; for offers detected in the store and flagged
  `sync_status_from_store`, store evidence sets it. Dates never set it: a passed start or end
  date only produces a review prompt.
* Technical status: active, inactive, unavailable, unknown — computed only from synced store
  discounts. An offer with no store evidence is "unknown" (not verified), never assumed active.

When the two disagree the offer carries a `status_conflict` that must be reviewed. Every change
of offer status is appended to `status_history` with its timestamp, source and note.
"""

from collections import defaultdict
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import ValidationError
from app.db.base import utcnow
from app.models import Coupon, Integration, PromoEvent, Store, SyncState
from app.models.enums import (
    OFFER_STATUSES,
    DataSource,
    IntegrationStatus,
    OfferStatus,
    TechnicalStatus,
)
from app.services.audit import audit

STATUS_LABELS = {
    "in_progress": "In Progress", "scheduled": "Scheduled", "live": "Live", "paused": "Paused",
    "completed": "Completed", "cancelled": "Cancelled", None: "Not set",
}
# Offer statuses that contradict a given technical status.
CONFLICTS: dict[str, dict[str, str]] = {
    OfferStatus.LIVE: {
        TechnicalStatus.INACTIVE: "marked Live, but its discount is not active in the store",
        TechnicalStatus.UNAVAILABLE: "marked Live, but its discount was not found in the store",
    },
    OfferStatus.SCHEDULED: {TechnicalStatus.ACTIVE: "marked Scheduled, but its discount is already active in the store"},
    OfferStatus.IN_PROGRESS: {TechnicalStatus.ACTIVE: "marked In Progress, but its discount is already active in the store"},
    OfferStatus.PAUSED: {TechnicalStatus.ACTIVE: "marked Paused, but its discount is still active in the store"},
    OfferStatus.COMPLETED: {TechnicalStatus.ACTIVE: "marked Completed, but its discount is still redeemable in the store"},
    OfferStatus.CANCELLED: {TechnicalStatus.ACTIVE: "marked Cancelled, but its discount is active in the store"},
}


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


# ---- evidence -------------------------------------------------------------------------------
def evidence_source(db: Session, store: Store) -> str | None:
    """Which coupon source counts as store evidence, or None when there is none."""
    if store.is_demo:
        return DataSource.DEMO  # synthetic, labelled as such
    integ = db.scalar(select(Integration).where(Integration.store_id == store.id, Integration.provider == "shopify"))
    if integ is not None and integ.status in (IntegrationStatus.CONNECTED, IntegrationStatus.ERROR):
        st = db.scalar(select(SyncState).where(SyncState.store_id == store.id, SyncState.resource == "discounts"))
        if st and st.last_success_at:
            return DataSource.SHOPIFY
    # Otherwise the Shopify admin Discounts export, when one was imported.
    from app.offers.discount_list import SOURCE as EXPORT

    if db.scalar(select(Coupon.id).where(Coupon.store_id == store.id, Coupon.source == EXPORT).limit(1)):
        return EXPORT
    return None


def technical_status(db: Session, ev: PromoEvent, store: Store, source: str | None) -> tuple[str, str, datetime | None]:
    """(technical status, evidence text, evidence timestamp)."""
    if source is None:
        return TechnicalStatus.UNKNOWN, "Not verified: the store is not connected or its discounts have not been synced.", None
    export = source == "shopify_export"
    label = "demo store data" if source == DataSource.DEMO else "the Shopify discounts export" if export else "Shopify"
    q = select(Coupon).where(Coupon.store_id == store.id, Coupon.source == source)
    if ev.shopify_discount_id:
        coupons = db.scalars(q.where(Coupon.source_id == ev.shopify_discount_id)).all()
        what = "the linked store discount"
    elif ev.coupon_codes:
        codes = [c.upper() for c in ev.coupon_codes]
        coupons = db.scalars(q.where(Coupon.code.in_(codes))).all()
        what = "code " + ", ".join(codes)
        missing = sorted(set(codes) - {c.code for c in coupons})
    else:
        return (TechnicalStatus.UNKNOWN, "Not verified: no coupon code or linked store discount to check "
                                         "(automatic offers need a linked discount).", None)
    checked = max((_aware(c.last_synced_at) for c in coupons if c.last_synced_at), default=None)
    if not coupons and export:
        # An export can be one page of 50: absence from it proves nothing.
        return TechnicalStatus.UNKNOWN, f"Not verified: {what} not in {label} (it may be on another page).", None
    if not coupons:
        return TechnicalStatus.UNAVAILABLE, f"{what.capitalize()} not found in {label}.", checked
    states = {c.code: (c.status or "unknown") for c in coupons}
    detail = "; ".join(f"{k}: {v}" for k, v in sorted(states.items()))
    if not ev.shopify_discount_id and missing:
        detail += f"; {'not in export' if export else 'not found'}: {', '.join(missing)}"
    if any(v == "active" for v in states.values()):
        return TechnicalStatus.ACTIVE, f"Active in {label} ({detail}).", checked
    if all(v == "deleted" for v in states.values()):
        return TechnicalStatus.UNAVAILABLE, f"Deleted from {label} ({detail}).", checked
    return TechnicalStatus.INACTIVE, f"Not active in {label} ({detail}).", checked


def _store_mapping(ev: PromoEvent, tech: str, db: Session, store: Store, source: str) -> str | None:
    """Offer status implied by store evidence, for offers that follow the store."""
    if tech == TechnicalStatus.ACTIVE:
        return OfferStatus.LIVE
    if tech != TechnicalStatus.INACTIVE:
        return None
    q = select(Coupon.status).where(Coupon.store_id == store.id, Coupon.source == source)
    q = q.where(Coupon.source_id == ev.shopify_discount_id) if ev.shopify_discount_id else \
        q.where(Coupon.code.in_([c.upper() for c in ev.coupon_codes or []]))
    states = set(db.scalars(q))
    if states == {"scheduled"}:
        return OfferStatus.SCHEDULED
    if states and states <= {"expired", "deleted"} and "expired" in states:
        return OfferStatus.COMPLETED  # the store itself reports the end
    return None


# ---- changes --------------------------------------------------------------------------------
def set_status(db: Session, ev: PromoEvent, new: str | None, *, source: str, actor: str = "owner",
               note: str | None = None) -> bool:
    if new is not None and new not in OFFER_STATUSES:
        raise ValidationError(f"Unknown offer status '{new}'. Use one of: {', '.join(OFFER_STATUSES)}.")
    if new == ev.status and ev.status_changed_at is not None:
        return False
    now = utcnow()
    ev.status_history = [*(ev.status_history or []), {
        "at": now.isoformat(), "from": ev.status, "to": new, "source": source, "actor": actor, "note": note}]
    old = ev.status
    ev.status = new
    ev.status_source = source
    ev.status_changed_at = now
    if ev.id:
        audit(db, "offer.status_changed", "promo_event", ev.id, store_id=ev.store_id, actor=actor,
              details={"from": old, "to": new, "source": source, "note": note})
    return True


def refresh(db: Session, ev: PromoEvent, store: Store, source: str | None = "__lookup__") -> PromoEvent:
    """Recompute technical status, apply store sync where enabled, recompute the conflict."""
    if source == "__lookup__":
        source = evidence_source(db, store)
    tech, evidence, checked = technical_status(db, ev, store, source)
    ev.technical_status, ev.technical_evidence = tech, evidence
    ev.technical_checked_at = checked or (utcnow() if source else None)
    if ev.sync_status_from_store and source:
        mapped = _store_mapping(ev, tech, db, store, source)
        if mapped and mapped != ev.status:
            set_status(db, ev, mapped, source="store_sync", actor="store sync",
                       note=f"Set from store evidence: {evidence}")
    conflict = CONFLICTS.get(ev.status or "", {}).get(tech)
    ev.status_conflict = f"Offer is {conflict}. {evidence}" if conflict else None
    if ev.status_conflict is None:
        ev.status_conflict_ack = None
    return ev


def acknowledge_conflict(db: Session, ev: PromoEvent, note: str | None = None) -> None:
    if not ev.status_conflict:
        raise ValidationError("There is no status conflict to review.")
    ev.status_conflict_ack = ev.status_conflict
    ev.status_history = [*(ev.status_history or []), {
        "at": utcnow().isoformat(), "from": ev.status, "to": ev.status, "source": "review", "actor": "owner",
        "note": f"Conflict reviewed: {note or 'kept as is'}"}]
    audit(db, "offer.status_conflict_reviewed", "promo_event", ev.id, store_id=ev.store_id,
          details={"conflict": ev.status_conflict, "note": note})


def review_prompts(ev: PromoEvent, today: date | None = None) -> list[str]:
    """Date-based reminders. They ask for a decision; they never change the status."""
    today = today or date.today()
    out = []
    if ev.status is None:
        out.append("Status not set: confirm the offer's stage.")
    if ev.status in (OfferStatus.SCHEDULED, OfferStatus.IN_PROGRESS) and ev.start_date and ev.start_date <= today:
        out.append(f"Start date {ev.start_date} has passed: confirm whether the offer is live.")
    if ev.status in (OfferStatus.LIVE, OfferStatus.PAUSED) and ev.end_date and ev.end_date < today:
        out.append(f"End date {ev.end_date} has passed: confirm it is completed, or extend it.")
    if ev.status == OfferStatus.IN_PROGRESS and ev.start_date and 0 < (ev.start_date - today).days <= 7:
        out.append(f"Starts in {(ev.start_date - today).days} day(s) and is still In Progress.")
    return out


def describe(ev: PromoEvent, today: date | None = None) -> dict:
    prompts = review_prompts(ev, today)
    open_conflict = bool(ev.status_conflict) and ev.status_conflict != ev.status_conflict_ack
    return {"status_label": STATUS_LABELS.get(ev.status, ev.status), "review_prompts": prompts,
            "conflict_open": open_conflict, "needs_review": open_conflict or bool(prompts)}


# ---- store-detected offers ------------------------------------------------------------------
def sync_store_offers(db: Session, store: Store, *, today: date | None = None) -> dict:
    """Create offers for store discounts that no offer documents yet, link them, and refresh every
    offer of the store. Old expired discounts (ended over a year ago) are not added."""
    today = today or date.today()
    source = evidence_source(db, store)
    created = linked = 0
    if source:
        groups: dict[str, list[Coupon]] = defaultdict(list)
        for c in db.scalars(select(Coupon).where(Coupon.store_id == store.id, Coupon.source == source,
                                                 Coupon.source_id.is_not(None))):
            groups[c.source_id].append(c)
        events = db.scalars(select(PromoEvent).where(PromoEvent.store_id == store.id)).all()
        by_discount = {e.shopify_discount_id: e for e in events if e.shopify_discount_id}
        for sid, coupons in groups.items():
            if sid in by_discount:
                continue
            codes = {c.code for c in coupons}
            documented = [e for e in events if not e.shopify_discount_id and codes & {x.upper() for x in e.coupon_codes or []}]
            if len(documented) == 1:
                documented[0].shopify_discount_id = sid
                linked += 1
                continue
            if documented:
                continue  # several documented offers share these codes: leave the match to the user
            ends = [_aware(c.ends_at) for c in coupons if c.ends_at]
            if ends and max(ends).date() < today - timedelta(days=365):
                continue
            first = coupons[0]
            ev = PromoEvent(
                store_id=store.id, name=first.title or first.code.removeprefix("AUTO:"),
                offer_text=_describe_coupon(first), offer_type=_offer_type(first),
                start_date=_aware(first.starts_at).date() if first.starts_at else None,
                end_date=_aware(first.ends_at).date() if first.ends_at else None,
                status=None, activation="automatic" if first.channel == "automatic" else "manual_code",
                coupon_codes=[] if first.channel == "automatic" else sorted(codes)[:10],
                threshold_amount=first.min_subtotal, shopify_discount_id=sid, sync_status_from_store=True,
                source=source, natural_key=f"store:{sid}"[:64], verification_status="verified_in_store",
                notes="Detected in the store; not documented in the offer database.")
            db.add(ev)
            db.flush()
            set_status(db, ev, OfferStatus.IN_PROGRESS, source="store_sync", actor="store sync",
                       note="Detected in the store")
            created += 1
    refreshed = 0
    for ev in db.scalars(select(PromoEvent).where(PromoEvent.store_id == store.id)):
        refresh(db, ev, store, source)
        refreshed += 1
    db.flush()
    return {"detected_offers_created": created, "linked_to_documented": linked, "offers_refreshed": refreshed,
            "evidence": source or "none"}


def _describe_coupon(c: Coupon) -> str:
    if c.value_type == "percent" and c.value is not None:
        base = f"{c.value:g}% off"
    elif c.value_type == "fixed" and c.value is not None:
        base = f"{c.value:g} off"
    elif c.value_type == "free_shipping":
        base = "Free shipping"
    elif c.value_type == "bxgy":
        base = "Buy X get Y"
    else:
        base = c.title or "Discount"
    if c.min_subtotal:
        base += f" over {c.min_subtotal:g}"
    elif c.min_quantity:
        base += f" on {c.min_quantity}+ items"
    return base


def _offer_type(c: Coupon) -> str:
    return {"percent": "percentage_discount", "fixed": "fixed_discount", "free_shipping": "free_shipping",
            "bxgy": "buy_x_get_y"}.get(c.value_type or "", "other")
