"""Basket growth: order-value and units distributions, threshold proximity, quantity mix,
free-gift candidates. Feeds the simulations with observed data."""

from collections import Counter
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analytics import metrics as M
from app.analytics import simulations as S
from app.analytics.dataset import Dataset, load_dataset
from app.analytics.product_analysis import product_performance
from app.models import CostRecord, Product, Store, Variant


def _r(v, nd=2):
    return None if v is None else round(v, nd)


def observed_margin(ds: Dataset) -> tuple[float | None, str]:
    """Gross margin % on merchandise for lines with recorded costs (used for incremental merchandise)."""
    net = cogs = 0.0
    for o in ds.orders:
        for ln in o.lines:
            if ln.cost_basis == "recorded":
                net += ln.net
                cogs += ln.unit_cost * ln.qty
    if net <= 0:
        default = (ds.store.commercial_settings or {}).get("default_cogs_pct")
        if default is not None:
            return 1 - float(default), "assumption (store default COGS %)"
        return None, "unknown"
    return (net - cogs) / net, "recorded unit costs"


def distribution(values: list[float], bins: int = 12) -> list[dict]:
    if not values:
        return []
    vals = sorted(values)
    cap = M.percentile(vals, 0.98) or vals[-1]
    step = max(5.0, round(cap / bins / 5) * 5)
    counts: Counter = Counter()
    for v in vals:
        b = min(int(v // step), bins)
        counts[b] += 1
    return [{"from": b * step, "to": (b + 1) * step if b < bins else None, "orders": counts.get(b, 0)}
            for b in range(bins + 1)]


def basket_overview(db: Session, store: Store, start: date, end: date) -> dict:
    ds = load_dataset(db, store, start, end)
    values = [o.net for o in ds.orders if o.net > 0]
    vals = sorted(values)
    units = Counter(min(o.units, 6) for o in ds.orders)
    n = len(ds.orders)
    margin, margin_basis = observed_margin(ds)
    settings = store.commercial_settings or {}
    existing_fs = settings.get("free_shipping_threshold")
    candidates = S.candidate_thresholds(values, existing_fs)
    gift_cost = settings.get("default_gift_cost")
    threshold_rows = []
    for t in candidates:
        sim = S.threshold_incentive(values, t, incentive_cost=float(gift_cost or 0), margin_pct=margin,
                                    already_offered=bool(existing_fs and abs(t - existing_fs) < 0.01))
        threshold_rows.append({
            "threshold": t, "is_current_free_shipping": bool(existing_fs and abs(t - existing_fs) < 0.01),
            "share_above": sim["share_above"], "share_reachable": sim["share_reachable"],
            "orders_reachable": sim["orders_reachable"], "avg_gap_reachable": sim["avg_gap_reachable"],
            # The current free-shipping threshold is already offered: a break-even is meaningless there.
            "break_even_mover_rate": sim["break_even_mover_rate"] if gift_cost and not (
                existing_fs and abs(t - existing_fs) < 0.01) else None,
        })
    total_units = sum(o.units for o in ds.orders)
    return {
        "period": {"start": start.isoformat(), "end": end.isoformat()},
        "orders": n,
        "aov": _r(M.aov(sum(values), len(values))),
        "percentiles": {f"p{int(q * 100)}": _r(M.percentile(vals, q)) for q in (0.25, 0.5, 0.75, 0.9)},
        "distribution": distribution(values),
        "units_mix": [{"units": k if k < 6 else "6+", "orders": units.get(k, 0),
                       "share": _r(M.safe_div(units.get(k, 0), n), 4)} for k in range(1, 7)],
        "single_unit_share": _r(M.safe_div(units.get(1, 0), n), 4),
        "units_per_order": _r(M.units_per_order(total_units, n), 3),
        "avg_unit_price": _r(M.safe_div(sum(values), total_units)),
        "margin_pct": _r(margin, 4),
        "margin_basis": margin_basis,
        "free_shipping_threshold": existing_fs,
        "threshold_candidates": threshold_rows,
        "gift_cost_assumption": gift_cost,
        "data_quality": ds.quality,
    }


def simulate_threshold(db: Session, store: Store, start: date, end: date, *, threshold: float,
                       incentive_cost: float, margin_pct: float | None = None, reach_window: float = 0.25,
                       overshoot_pct: float = 0.05, already_offered: bool = False) -> dict:
    ds = load_dataset(db, store, start, end)
    margin, basis = (margin_pct, "user input") if margin_pct is not None else observed_margin(ds)
    res = S.threshold_incentive([o.net for o in ds.orders], threshold, incentive_cost=incentive_cost,
                                margin_pct=margin, reach_window=reach_window, overshoot_pct=overshoot_pct,
                                already_offered=already_offered)
    res["margin_basis"] = basis
    res["period"] = {"start": start.isoformat(), "end": end.isoformat()}
    return res


def simulate_quantity(db: Session, store: Store, start: date, end: date, *, min_units: int, discount_pct: float,
                      margin_pct: float | None = None) -> dict:
    ds = load_dataset(db, store, start, end)
    margin, basis = (margin_pct, "user input") if margin_pct is not None else observed_margin(ds)
    total_units = sum(o.units for o in ds.orders)
    aup = M.safe_div(sum(o.net for o in ds.orders), total_units) or 0.0
    res = S.quantity_incentive([(o.units, o.net) for o in ds.orders], min_units=min_units,
                               discount_pct=discount_pct, avg_unit_price=aup, margin_pct=margin)
    res["margin_basis"] = basis
    res["avg_unit_price"] = round(aup, 2)
    return res


def gift_candidates(db: Session, store: Store, start: date, end: date, limit: int = 15) -> dict:
    """Products that could serve as a free gift: known low unit cost, decent stock, not a top seller
    (giving away a bestseller cannibalises its sales). Ranked by price-to-cost ratio (perceived value per cost)."""
    perf = {r["product_id"]: r for r in product_performance(db, store, start, end, top_n=10_000)["products"]}
    latest_cost: dict[int, float] = {}
    for c in db.scalars(select(CostRecord).where(CostRecord.store_id == store.id, CostRecord.variant_id.is_not(None))
                        .order_by(CostRecord.effective_from, CostRecord.created_at)):
        latest_cost[c.variant_id] = c.unit_cost
    rows = []
    for v, p in db.execute(select(Variant, Product).join(Product, Product.id == Variant.product_id)
                           .where(Variant.store_id == store.id)).all():
        cost = latest_cost.get(v.id)
        if cost is None or not v.price or (p.status and p.status != "ACTIVE"):
            continue
        stock = v.inventory_quantity if v.inventory_tracked else None
        pr = perf.get(p.id, {})
        if (pr.get("share_of_net") or 0) > 0.05:
            continue
        if stock is not None and stock < 20:
            continue
        rows.append({"product_id": p.id, "variant_id": v.id, "title": p.title, "variant": v.title, "price": v.price,
                     "unit_cost": cost, "value_to_cost": round(v.price / cost, 2) if cost else None,
                     "inventory": stock, "units_sold_in_period": pr.get("units", 0)})
    rows.sort(key=lambda r: -(r["value_to_cost"] or 0))
    return {"candidates": rows[:limit],
            "criteria": "Recorded unit cost, active, ≥20 units in stock (if tracked), <5% of net sales; "
                        "ranked by price ÷ cost (perceived value per unit of cost).",
            "available": bool(latest_cost)}
