"""Forecast estimate for an offer nobody has forecast by hand.

Orders: median orders per day of the store's past offers shown the same way (on the site, or by
email only), times the offer's length. AOV: median AOV of those offers. Measured from each past
offer's codes in the discount report. Left out: cancelled offers, offers without measured orders,
and codes shared by several offers (their figures cannot be split). Fewer than MIN_BASIS
comparable offers: no estimate.
"""

from datetime import date
from statistics import median

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models import PromoEvent
from app.offers.discount_report import offer_results

MIN_BASIS = 3


def _kind(display: str | None) -> str:
    return "email" if display == "email_only" else "site"


def baselines(db: Session, store_ids: list[int], today: date) -> dict[tuple[int, str], dict]:
    past = db.scalars(select(PromoEvent).where(PromoEvent.store_id.in_(store_ids), PromoEvent.end_date < today,
                                               PromoEvent.start_date.is_not(None),
                                               # status not set counts: most imported history has none
                                               or_(PromoEvent.status.is_(None), PromoEvent.status != "cancelled")))
    rows = [{"id": e.id, "store_id": e.store_id, "coupon_codes": e.coupon_codes or [], "linked_discounts": e.linked_discounts or [],
             "start_date": e.start_date.isoformat(), "end_date": e.end_date.isoformat(), "_kind": _kind(e.display),
             "_days": (e.end_date - e.start_date).days + 1} for e in past if e.coupon_codes or e.linked_discounts]
    offer_results(db, rows)
    per: dict[tuple[int, str], list[tuple[float, float | None]]] = {}
    for r in rows:
        res = r.get("discount_results") or {}
        if res.get("status") != "ok" or not res.get("orders") or res.get("shared_with"):
            continue
        per.setdefault((r["store_id"], r["_kind"]), []).append((res["orders"] / r["_days"], res.get("aov")))
    out = {}
    for key, vals in per.items():
        if len(vals) >= MIN_BASIS:
            aovs = [a for _, a in vals if a]
            out[key] = {"per_day": median(v for v, _ in vals), "aov": median(aovs) if aovs else None, "n": len(vals)}
    return out


def estimate(base: dict[tuple[int, str], dict], e: PromoEvent) -> dict | None:
    b = base.get((e.store_id, _kind(e.display)))
    if not b or not e.start_date or not e.end_date:
        return None
    days = (e.end_date - e.start_date).days + 1
    return {"orders": max(1, round(b["per_day"] * days)), "aov": b["aov"], "basis": b["n"]}
