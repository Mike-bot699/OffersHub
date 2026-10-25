"""Store-level performance: KPIs, comparisons, customers, discount dependency, seasonality."""

from collections import defaultdict
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.analytics import metrics as M
from app.analytics.dataset import Dataset, load_dataset, previous_period, year_ago
from app.models import Order, Store


def kpis(ds: Dataset) -> dict:
    s = ds.store.commercial_settings or {}
    orders = ds.orders
    n = len(orders)
    gross = sum(o.gross for o in orders)
    disc = sum(o.discounts for o in orders)
    net = gross - disc
    returns = sum(o.returns for o in orders)
    kept = net - returns
    units = sum(o.units for o in orders)
    shipping = sum(o.shipping for o in orders)
    paid = sum(o.paid for o in orders)
    cov = ds.quality.get("cost_coverage")
    assumed = ds.quality.get("cost_assumed_share") or 0
    cogs = None
    if n and cov is not None and cov + assumed >= 0.95:  # only when (almost) every line has a cost
        cogs = sum((ln.cogs or 0) for o in orders for ln in o.lines)
    gross_profit = (kept - cogs) if cogs is not None else None
    contrib, missing = M.contribution(
        gross_profit, shipping, n,
        shipping_cost_per_order=s.get("avg_shipping_cost_per_order"),
        payment_fee_pct=s.get("payment_fee_pct"), payment_fee_fixed=s.get("payment_fee_fixed"),
        fulfillment_cost_per_order=s.get("fulfillment_cost_per_order"), customer_paid=paid,
    )
    if cogs is not None and cov is not None and cov + assumed < 0.999:
        missing = [*missing, f"unit costs for {1 - cov - assumed:.1%} of net sales (counted as zero cost)"]
    disc_orders = sum(1 for o in orders if o.has_discount)
    code_orders = sum(1 for o in orders if o.codes)
    # Conversion uses only days that have traffic data, so a missing day never distorts the ratio.
    sessions = sum(ds.traffic.values()) if ds.traffic else None
    orders_on_traffic_days = sum(1 for o in orders if o.local_date in ds.traffic)
    traffic_coverage = M.safe_div(len(ds.traffic), ds.quality.get("period_days"))
    return {
        "orders": n,
        "gross_sales": round(gross, 2),
        "discounts": round(disc, 2),
        "net_sales": round(net, 2),
        "returns": round(returns, 2),
        "net_sales_kept": round(kept, 2),
        "shipping_charged": round(shipping, 2),
        "units": units,
        "aov": _r(M.aov(net, n)),
        "units_per_order": _r(M.units_per_order(units, n), 3),
        "discount_rate": _r(M.discount_rate(disc, gross), 4),
        "discounted_order_share": _r(M.safe_div(disc_orders, n), 4),
        "coupon_order_share": _r(M.safe_div(code_orders, n), 4),
        "return_rate": _r(M.safe_div(returns, net), 4),
        "cogs": _r(cogs),
        "gross_profit": _r(gross_profit),
        "gross_margin_pct": _r(M.gross_margin_pct(kept, cogs), 4),
        "contribution": _r(contrib),
        "contribution_missing": missing,
        "sessions": sessions,
        "conversion_rate": _r(M.conversion_rate(orders_on_traffic_days, sessions), 5),
        "traffic_coverage": _r(traffic_coverage, 3),
        "cost_basis": ("recorded" if cov and cov >= 0.95 else "includes_assumption" if cogs is not None
                       else "insufficient"),
    }


def _r(v, nd=2):
    return None if v is None else round(v, nd)


def customer_metrics(db: Session, ds: Dataset) -> dict:
    """New vs returning (by customer order index) and 90-day repeat rate on mature cohorts."""
    if not ds.quality.get("customer_id_coverage"):
        return {"available": False, "reason": "No customer IDs on orders (read_customers not granted or "
                                              "orders imported without customers)."}
    new = [o for o in ds.orders if o.customer_order_index == 1]
    ret = [o for o in ds.orders if o.customer_order_index and o.customer_order_index > 1]
    unknown = len(ds.orders) - len(new) - len(ret)
    # 90-day repeat: customers whose first order is ≥ 90 days before the period end and inside the period
    cutoff = ds.end - timedelta(days=90)
    store = ds.store
    first_orders = db.execute(
        select(Order.customer_id, func.min(Order.processed_at))
        .where(Order.store_id == store.id, Order.customer_id.is_not(None), Order.is_test.is_(False),
               Order.cancelled_at.is_(None))
        .group_by(Order.customer_id)
    ).all()
    cohort = {cid: first for cid, first in first_orders if ds.start <= first.date() <= cutoff}
    repeaters = 0
    if cohort:
        later = db.execute(
            select(Order.customer_id, Order.processed_at)
            .where(Order.store_id == store.id, Order.customer_id.in_(list(cohort)), Order.is_test.is_(False),
                   Order.cancelled_at.is_(None), Order.customer_order_index > 1)
        ).all()
        seen = set()
        for cid, at in later:
            if cid not in seen and (at - cohort[cid]).days <= 90:
                seen.add(cid)
        repeaters = len(seen)
    return {
        "available": True,
        "new_customer_orders": len(new),
        "returning_customer_orders": len(ret),
        "unknown_customer_orders": unknown,
        "returning_order_share": _r(M.safe_div(len(ret), len(new) + len(ret)), 4),
        "new_customer_aov": _r(M.aov(sum(o.net for o in new), len(new))),
        "returning_customer_aov": _r(M.aov(sum(o.net for o in ret), len(ret))),
        "repeat_90d": {
            "cohort_customers": len(cohort),
            "repeat_customers": repeaters,
            "rate": _r(M.safe_div(repeaters, len(cohort)), 4),
            "definition": "Share of customers whose first order fell in the period (and ≥90 days before its "
                          "end) who ordered again within 90 days.",
        },
    }


def discount_dependency(ds: Dataset) -> dict:
    orders = ds.orders
    by_code: dict[str, dict] = defaultdict(lambda: {"orders": 0, "net_sales": 0.0, "discounts": 0.0})
    for o in orders:
        for c in o.codes or ["(automatic)" if o.automatic_discounts and o.has_discount else None]:
            if c is None:
                continue
            by_code[c]["orders"] += 1
            by_code[c]["net_sales"] += o.net
            by_code[c]["discounts"] += o.discounts / max(1, len(o.codes) or 1)
    disc = [o for o in orders if o.has_discount]
    full = [o for o in orders if not o.has_discount]
    top = sorted(({"code": k, **{kk: round(vv, 2) if isinstance(vv, float) else vv for kk, vv in v.items()}}
                  for k, v in by_code.items()), key=lambda r: -r["orders"])[:15]
    return {
        "discounted_orders": len(disc),
        "full_price_orders": len(full),
        "discounted_net_sales_share": _r(M.safe_div(sum(o.net for o in disc), sum(o.net for o in orders)), 4),
        "aov_discounted": _r(M.aov(sum(o.net for o in disc), len(disc))),
        "aov_full_price": _r(M.aov(sum(o.net for o in full), len(full))),
        "avg_discount_depth": _r(M.safe_div(sum(o.discounts for o in disc), sum(o.gross for o in disc)), 4),
        "top_codes": top,
        "note": "Orders using a discount are not necessarily caused by it: many would have been placed at "
                "full price. This is exposure, not incremental impact.",
    }


def monthly_series(db: Session, store: Store, months: int = 24) -> list[dict]:
    """Monthly net sales and orders across the stored history (store timezone ignored at month level)."""
    today = date.today()
    start = date(today.year - (months // 12) - 1, today.month, 1)
    rows = db.execute(
        select(Order.processed_at, Order.gross_sales, Order.total_discounts)
        .where(Order.store_id == store.id, Order.is_test.is_(False), Order.cancelled_at.is_(None),
               Order.processed_at >= start)
    ).all()
    agg: dict[str, list[float]] = defaultdict(lambda: [0, 0.0])
    for at, gross, disc in rows:
        k = f"{at.year:04d}-{at.month:02d}"
        agg[k][0] += 1
        agg[k][1] += gross - disc
    return [{"month": k, "orders": int(v[0]), "net_sales": round(v[1], 2)} for k, v in sorted(agg.items())][-months:]


def seasonality(series: list[dict]) -> dict:
    if len(series) < 13:
        return {"available": False, "reason": f"Only {len(series)} months of history; 13+ needed for a "
                                              "year-over-year view."}
    by_month = defaultdict(list)
    for r in series:
        by_month[int(r["month"][5:])].append(r["net_sales"])
    avg = {m: sum(v) / len(v) for m, v in by_month.items()}
    mean = sum(avg.values()) / len(avg)
    index = {m: round(v / mean, 3) for m, v in sorted(avg.items())} if mean else {}
    peak = max(index, key=index.get) if index else None
    return {"available": True, "monthly_index": index, "peak_month": peak,
            "note": "Index = average net sales of the calendar month / average of all months (1.0 = typical)."}


def weekly_series(ds: Dataset) -> list[dict]:
    agg: dict[date, list[float]] = defaultdict(lambda: [0, 0.0, 0.0])
    for o in ds.orders:
        wk = o.local_date - timedelta(days=o.local_date.weekday())
        agg[wk][0] += 1
        agg[wk][1] += o.net
        agg[wk][2] += o.discounts
    return [{"week": k.isoformat(), "orders": int(v[0]), "net_sales": round(v[1], 2), "discounts": round(v[2], 2)}
            for k, v in sorted(agg.items())]


def store_summary(db: Session, store: Store, start: date, end: date) -> dict:
    ds = load_dataset(db, store, start, end)
    p_start, p_end = previous_period(start, end)
    prev = load_dataset(db, store, p_start, p_end)
    y_start, y_end = year_ago(start, end)
    yoy = load_dataset(db, store, y_start, y_end)
    cur, pk, yk = kpis(ds), kpis(prev), kpis(yoy)
    compare_keys = ["orders", "net_sales", "aov", "units_per_order", "discount_rate", "gross_margin_pct",
                    "contribution", "conversion_rate", "return_rate", "sessions", "discounts", "gross_sales", "returns",
                    "discounted_order_share", "coupon_order_share", "cogs", "gross_profit"]
    series = monthly_series(db, store)
    return {
        "store": {"id": store.id, "code": store.code, "name": store.name, "currency": store.currency,
                  "is_demo": store.is_demo},
        "period": {"start": start.isoformat(), "end": end.isoformat()},
        "kpis": cur,
        "previous": {"period": {"start": p_start.isoformat(), "end": p_end.isoformat()}, "kpis": pk,
                     "change": {k: _r(M.growth(cur.get(k), pk.get(k)), 4) for k in compare_keys}},
        "year_ago": {"period": {"start": y_start.isoformat(), "end": y_end.isoformat()}, "kpis": yk,
                     "available": yk["orders"] > 0,
                     "change": {k: _r(M.growth(cur.get(k), yk.get(k)), 4) for k in compare_keys}},
        "customers": customer_metrics(db, ds),
        "discounts": discount_dependency(ds),
        "weekly": weekly_series(ds),
        "monthly": series,
        "seasonality": seasonality(series),
        "data_quality": ds.quality,
    }
