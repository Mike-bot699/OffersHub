"""Offers & discounts overview for one store over a period.

Offer counts come from the offer database; order KPIs come from the store's orders. When the
store has no order data (an Eli Z store today: no orders endpoint is confirmed yet), the KPIs
are null and `orders_missing` says why — they are never shown as zero.
"""

from collections import Counter, defaultdict
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analytics import metrics as M
from app.analytics.dataset import load_dataset
from app.models import Integration, PromoEvent, Store


def _r(v, nd=2):
    return None if v is None else round(v, nd)


def store_overview(db: Session, store: Store, start: date, end: date) -> dict:
    events = db.scalars(select(PromoEvent).where(PromoEvent.store_id == store.id)).all()
    live = [e for e in events if e.status == "live"]
    in_period = [e for e in events if e.start_date and e.start_date <= end and (e.end_date or e.start_date) >= start
                 and e.status != "cancelled"]
    out = {
        "store_id": store.id, "store_code": store.code, "store_name": store.name, "currency": store.currency,
        "is_demo": store.is_demo, "logo": store.logo, "period": {"start": start.isoformat(), "end": end.isoformat()},
        "live_offers": [{"id": e.id, "name": e.name, "offer_text": e.offer_text, "coupon_codes": e.coupon_codes,
                         "start_date": e.start_date.isoformat() if e.start_date else None,
                         "end_date": e.end_date.isoformat() if e.end_date else None,
                         "technical_status": e.technical_status} for e in live],
        "offers_in_period": len(in_period),
        "offers_in_period_by_status": dict(Counter(e.status or "not_set" for e in in_period)),
    }
    orders = load_dataset(db, store, start, end).orders
    if not orders:
        provider = db.scalar(select(Integration.provider).where(Integration.store_id == store.id)
                             .order_by((Integration.provider == "elizeta").desc()).limit(1))
        out["kpis"] = None
        out["top_codes"] = []
        out["orders_missing"] = (
            "No order data: Eli Z provides catalog and product sales, but no orders endpoint is confirmed yet."
            if provider == "elizeta" else "No orders in this period for this store.")
        return out

    gross = sum(o.gross for o in orders)
    disc = sum(o.discounts for o in orders)
    net = gross - disc
    with_disc = [o for o in orders if o.has_discount]
    without = [o for o in orders if not o.has_discount]
    with_code = [o for o in orders if o.codes]
    live_codes = {c.upper() for e in live for c in (e.coupon_codes or [])}
    with_live = [o for o in orders if live_codes & set(o.codes)] if live_codes else []
    out["kpis"] = {
        "orders": len(orders),
        "aov_with_discount": _r(M.aov(sum(o.net for o in with_disc), len(with_disc))),
        "aov_without_discount": _r(M.aov(sum(o.net for o in without), len(without))),
        # Orders that used a code or an automatic offer.
        "orders_with_offer_share": _r(M.safe_div(len(with_disc), len(orders)), 4),
        "orders_with_code_share": _r(M.safe_div(len(with_code), len(orders)), 4),
        "orders_with_live_offer_code": len(with_live) if live_codes else None,
        # What discounted orders weigh in sales, and what the discounts cost against gross sales.
        "sales_share_with_offer": _r(M.safe_div(sum(o.net for o in with_disc), net), 4),
        "discount_rate": _r(M.discount_rate(disc, gross), 4),
        "discounts": round(disc, 2),
    }
    by_code: dict[str, dict] = defaultdict(lambda: {"orders": 0, "net_sales": 0.0, "discounts": 0.0})
    for o in orders:
        for c in o.codes:
            row = by_code[c]
            row["orders"] += 1
            row["net_sales"] += o.net
            row["discounts"] += o.discounts / len(o.codes)
    top = sorted(by_code.items(), key=lambda kv: -kv[1]["orders"])[:10]
    out["top_codes"] = [{"code": c, "orders": v["orders"], "order_share": _r(M.safe_div(v["orders"], len(orders)), 4),
                         "net_sales": round(v["net_sales"], 2), "discounts": round(v["discounts"], 2),
                         "aov": _r(M.aov(v["net_sales"], v["orders"]))} for c, v in top]
    out["orders_missing"] = None
    return out
