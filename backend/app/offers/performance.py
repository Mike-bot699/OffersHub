"""Discount, AOV and per-offer performance analysis.

`discount_analysis` describes how discounting and order value behave over a period.
`offer_performance` measures each promotional event against the period just before it.
Both describe what happened; neither proves what the discount caused. Every before/after
comparison is labelled as such and rated weak evidence.
"""

from collections import defaultdict
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analytics import metrics as M
from app.analytics.dataset import OrderRec, load_dataset
from app.models import PromoEvent, Store

DEPTH_BUCKETS = [(0.0, 0.0, "No discount"), (0.0, 0.10, "Up to 10%"), (0.10, 0.20, "10–20%"),
                 (0.20, 0.30, "20–30%"), (0.30, 1.01, "Over 30%")]


def _r(v, nd=2):
    return None if v is None else round(v, nd)


def _week(d: date) -> date:
    return d - timedelta(days=d.weekday())


def _depth(o: OrderRec) -> float:
    return o.discounts / o.gross if o.gross > 0 else 0.0


def discount_analysis(db: Session, store: Store, start: date, end: date) -> dict:
    ds = load_dataset(db, store, start, end)
    orders = ds.orders
    weeks: dict[date, list[OrderRec]] = defaultdict(list)
    for o in orders:
        weeks[_week(o.local_date)].append(o)
    weekly = []
    for wk, os_ in sorted(weeks.items()):
        gross = sum(o.gross for o in os_)
        disc = sum(o.discounts for o in os_)
        net = gross - disc
        d_orders = [o for o in os_ if o.has_discount]
        weekly.append({
            "week": wk.isoformat(), "orders": len(os_), "net_sales": round(net, 2), "discounts": round(disc, 2),
            "discount_rate": _r(M.discount_rate(disc, gross), 4),
            "discounted_order_share": _r(M.safe_div(len(d_orders), len(os_)), 4),
            "aov": _r(M.aov(net, len(os_))), "units_per_order": _r(M.units_per_order(sum(o.units for o in os_), len(os_)), 3),
        })

    # By code (an order with two codes counts for both; its discount is split evenly)
    by_code: dict[str, dict] = defaultdict(lambda: {"orders": 0, "net_sales": 0.0, "discounts": 0.0, "units": 0,
                                                     "new_customers": 0, "known_customers": 0})
    for o in orders:
        keys = o.codes or (["(automatic discount)"] if o.has_discount else [])
        for k in keys:
            row = by_code[k]
            row["orders"] += 1
            row["net_sales"] += o.net
            row["discounts"] += o.discounts / len(keys)
            row["units"] += o.units
            if o.customer_order_index:
                row["known_customers"] += 1
                row["new_customers"] += 1 if o.customer_order_index == 1 else 0
    total_orders = len(orders)
    codes = []
    for k, v in by_code.items():
        codes.append({
            "code": k, "orders": v["orders"], "order_share": _r(M.safe_div(v["orders"], total_orders), 4),
            "net_sales": round(v["net_sales"], 2), "discounts": round(v["discounts"], 2),
            "discount_per_order": _r(M.safe_div(v["discounts"], v["orders"])),
            "aov": _r(M.aov(v["net_sales"], v["orders"])), "units_per_order": _r(M.safe_div(v["units"], v["orders"]), 2),
            "new_customer_share": _r(M.safe_div(v["new_customers"], v["known_customers"]), 4),
        })
    codes.sort(key=lambda r: -r["orders"])

    depth = []
    for lo, hi, lab in DEPTH_BUCKETS:
        sel = [o for o in orders if (not o.has_discount if hi == 0 else (o.has_discount and lo < _depth(o) <= hi))]
        depth.append({"bucket": lab, "orders": len(sel), "order_share": _r(M.safe_div(len(sel), total_orders), 4),
                      "aov": _r(M.aov(sum(o.net for o in sel), len(sel))),
                      "units_per_order": _r(M.safe_div(sum(o.units for o in sel), len(sel)), 2),
                      "discounts": round(sum(o.discounts for o in sel), 2)})

    disc = [o for o in orders if o.has_discount]
    full = [o for o in orders if not o.has_discount]
    new = [o for o in orders if o.customer_order_index == 1]
    ret = [o for o in orders if o.customer_order_index and o.customer_order_index > 1]
    gross = sum(o.gross for o in orders)
    discounts = sum(o.discounts for o in orders)
    return {
        "period": {"start": start.isoformat(), "end": end.isoformat()},
        "totals": {
            "orders": total_orders, "discounts": round(discounts, 2), "net_sales": round(gross - discounts, 2),
            "discount_rate": _r(M.discount_rate(discounts, gross), 4),
            "discounted_order_share": _r(M.safe_div(len(disc), total_orders), 4),
            "discount_per_discounted_order": _r(M.safe_div(sum(o.discounts for o in disc), len(disc))),
            "aov": _r(M.aov(gross - discounts, total_orders)),
            "units_per_order": _r(M.units_per_order(sum(o.units for o in orders), total_orders), 3),
        },
        "aov_split": [
            {"segment": "Full price", "orders": len(full), "aov": _r(M.aov(sum(o.net for o in full), len(full))),
             "units_per_order": _r(M.safe_div(sum(o.units for o in full), len(full)), 2)},
            {"segment": "With a discount", "orders": len(disc), "aov": _r(M.aov(sum(o.net for o in disc), len(disc))),
             "units_per_order": _r(M.safe_div(sum(o.units for o in disc), len(disc)), 2)},
            {"segment": "New customers", "orders": len(new), "aov": _r(M.aov(sum(o.net for o in new), len(new))),
             "units_per_order": _r(M.safe_div(sum(o.units for o in new), len(new)), 2)},
            {"segment": "Returning customers", "orders": len(ret), "aov": _r(M.aov(sum(o.net for o in ret), len(ret))),
             "units_per_order": _r(M.safe_div(sum(o.units for o in ret), len(ret)), 2)},
        ],
        "weekly": weekly,
        "by_code": codes[:30],
        "by_depth": depth,
        "note": "Orders that used a discount are not all caused by it. Compare segments with care: discounted "
                "orders often have a minimum spend, which raises their AOV by construction.",
    }


def _window_stats(orders: list[OrderRec], days: int) -> dict:
    gross = sum(o.gross for o in orders)
    disc = sum(o.discounts for o in orders)
    return {
        "orders_per_day": _r(len(orders) / days, 2) if days else None,
        "net_sales_per_day": _r((gross - disc) / days) if days else None,
        "aov": _r(M.aov(gross - disc, len(orders))),
        "units_per_order": _r(M.units_per_order(sum(o.units for o in orders), len(orders)), 3),
        "discount_rate": _r(M.discount_rate(disc, gross), 4),
        "discounts": round(disc, 2),
    }


def offer_performance(db: Session, store: Store, *, since: date | None = None, today: date | None = None) -> dict:
    """Each event with dates, measured from orders where they exist, compared with the equally long
    window just before it (capped at 28 days). Events without synced orders show documented results only."""
    today = today or date.today()
    q = select(PromoEvent).where(PromoEvent.store_id == store.id, PromoEvent.start_date.is_not(None),
                                 PromoEvent.start_date <= today,
                                 PromoEvent.status.not_in(["cancelled", "not_launched", "undocumented"]))
    if since:
        q = q.where(PromoEvent.end_date >= since)
    events = db.scalars(q.order_by(PromoEvent.start_date.desc())).all()
    rows = []
    for ev in events:
        end = min(ev.end_date or today, today)
        span = (end - ev.start_date).days + 1
        base_days = min(28, max(7, span))
        base_end = ev.start_date - timedelta(days=1)
        base_start = base_end - timedelta(days=base_days - 1)
        during = load_dataset(db, store, ev.start_date, end).orders
        before = load_dataset(db, store, base_start, base_end).orders
        documented = {"offer_orders": ev.offer_orders, "total_orders": ev.total_orders, "offer_sales": ev.offer_sales,
                      "offer_order_share": _r(M.safe_div(ev.offer_orders, ev.total_orders), 4)
                      if ev.offer_orders is not None and ev.total_orders else None, "source": ev.results_source}
        row = {"event_id": ev.id, "name": ev.name, "offer_text": ev.offer_text, "offer_type": ev.offer_type,
               "start": ev.start_date.isoformat(), "end": end.isoformat(), "days": span, "status": ev.status,
               "coupon_codes": ev.coupon_codes, "threshold_amount": ev.threshold_amount,
               "verification_status": ev.verification_status, "documented": documented, "data_issues": ev.data_issues,
               "measured": None}
        if during:
            codes = {c.upper() for c in ev.coupon_codes or []}
            offer_orders = [o for o in during if codes & set(o.codes)] if codes else []
            d, b = _window_stats(during, span), (_window_stats(before, base_days) if before else None)
            row["measured"] = {
                "during": d, "before": b,
                "baseline_window": {"start": base_start.isoformat(), "end": base_end.isoformat()},
                "change": {k: _r(M.growth(d[k], b[k]), 4) for k in ("orders_per_day", "net_sales_per_day", "aov",
                                                                      "units_per_order")} if b else None,
                "offer_orders": len(offer_orders) if codes else None,
                "offer_order_share": _r(M.safe_div(len(offer_orders), len(during)), 4) if codes else None,
                "offer_discounts": round(sum(o.discounts for o in offer_orders), 2) if codes else None,
                "offer_aov": _r(M.aov(sum(o.net for o in offer_orders), len(offer_orders))) if codes else None,
                "discount_per_offer_order": _r(M.safe_div(sum(o.discounts for o in offer_orders), len(offer_orders)))
                if codes else None,
                "evidence": "weak (before/after)",
            }
        rows.append(row)

    by_type: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        if r["measured"] and r["measured"]["change"]:
            by_type[r["offer_type"] or "unknown"].append(r)
    summary = []
    for t, rs in by_type.items():
        changes = sorted(r["measured"]["change"]["net_sales_per_day"] for r in rs
                         if r["measured"]["change"]["net_sales_per_day"] is not None)
        summary.append({"offer_type": t, "events": len(rs),
                        "median_net_sales_per_day_change": _r(M.percentile(changes, 0.5), 4) if changes else None,
                        "discounts": round(sum(r["measured"]["during"]["discounts"] for r in rs), 2)})
    summary.sort(key=lambda s: -s["events"])
    return {"events": rows, "by_type": summary,
            "note": "Each offer is compared with the period just before it (same length, at most 28 days). "
                    "Seasonality, traffic and other campaigns are not removed, so changes are descriptive. "
                    "Use an experiment to measure what an offer actually causes."}
