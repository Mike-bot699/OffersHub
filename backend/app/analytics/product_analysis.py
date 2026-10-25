"""Product and variant intelligence for one store and period."""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from itertools import combinations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analytics import metrics as M
from app.analytics.dataset import Dataset, load_dataset, period_bounds_utc, previous_period
from app.models import PriceHistory, Product, Store, Variant


@dataclass
class _Acc:
    units: int = 0
    orders: set = field(default_factory=set)
    gross: float = 0.0
    discount: float = 0.0
    refunded_units: int = 0
    discounted_units: int = 0
    cogs: float = 0.0
    cost_units: int = 0
    customers: set = field(default_factory=set)
    order_net: float = 0.0
    solo_orders: int = 0


def _accumulate(ds: Dataset) -> tuple[dict[int, _Acc], dict[int, _Acc], dict[int, str]]:
    by_p: dict[int, _Acc] = defaultdict(_Acc)
    by_v: dict[int, _Acc] = defaultdict(_Acc)
    titles: dict[int, str] = {}
    for o in ds.orders:
        products_in_order = {ln.product_id for ln in o.lines if ln.product_id}
        for pid in products_in_order:
            by_p[pid].order_net += o.net
            if len(products_in_order) == 1:
                by_p[pid].solo_orders += 1
        for ln in o.lines:
            if not ln.product_id:
                continue
            titles.setdefault(ln.product_id, ln.title)
            for acc in (by_p[ln.product_id], by_v[ln.variant_id] if ln.variant_id else None):
                if acc is None:
                    continue
                acc.units += ln.qty
                acc.orders.add(o.id)
                acc.gross += ln.gross
                acc.discount += ln.discount
                acc.refunded_units += ln.refunded_qty
                if ln.discount > 0.005:
                    acc.discounted_units += ln.qty
                if ln.unit_cost is not None and ln.cost_basis == "recorded":
                    acc.cogs += ln.unit_cost * ln.kept_qty
                    acc.cost_units += ln.qty
                if o.customer_id:
                    acc.customers.add(o.customer_id)
    return by_p, by_v, titles


def _r(v, nd=2):
    return None if v is None else round(v, nd)


def product_performance(db: Session, store: Store, start: date, end: date, *, top_n: int = 200) -> dict:
    ds = load_dataset(db, store, start, end)
    p_start, p_end = previous_period(start, end)
    prev = load_dataset(db, store, p_start, p_end)
    by_p, by_v, titles = _accumulate(ds)
    prev_p, _, _ = _accumulate(prev)
    products = {p.id: p for p in db.scalars(select(Product).where(Product.store_id == store.id))}
    variants_by_product: dict[int, list[Variant]] = defaultdict(list)
    for v in db.scalars(select(Variant).where(Variant.store_id == store.id)):
        variants_by_product[v.product_id].append(v)
    lo, hi = period_bounds_utc(store, start, end)
    price_changes: dict[int, int] = defaultdict(int)
    vid_to_pid = {v.id: pid for pid, vs in variants_by_product.items() for v in vs}
    first_seen: set[int] = set()
    for ph in db.scalars(select(PriceHistory).where(PriceHistory.store_id == store.id)
                         .order_by(PriceHistory.observed_at)):
        if ph.variant_id not in first_seen:
            first_seen.add(ph.variant_id)  # first observation is the baseline, not a change
            continue
        at = ph.observed_at if ph.observed_at.tzinfo else ph.observed_at.replace(tzinfo=lo.tzinfo)
        if lo <= at < hi:
            price_changes[vid_to_pid.get(ph.variant_id, -1)] += 1

    total_net = sum(a.gross - a.discount for a in by_p.values())
    rows = []
    for pid, a in by_p.items():
        net = a.gross - a.discount
        p = products.get(pid)
        vs = variants_by_product.get(pid, [])
        pa = prev_p.get(pid)
        prev_net = (pa.gross - pa.discount) if pa else 0.0
        cost_cov = M.safe_div(a.cost_units, a.units)
        kept_net = net * (1 - (M.safe_div(a.refunded_units, a.units) or 0))
        gp = (kept_net - a.cogs) if cost_cov and cost_cov >= 0.95 else None
        prices = [v.price for v in vs if v.price is not None]
        inv = [v.inventory_quantity for v in vs if v.inventory_tracked and v.inventory_quantity is not None]
        rows.append({
            "product_id": pid,
            "title": p.title if p else titles.get(pid, "(unknown product)"),
            "status": p.status if p else None,
            "product_type": p.product_type if p else None,
            "units": a.units,
            "orders": len(a.orders),
            "gross_sales": round(a.gross, 2),
            "discounts": round(a.discount, 2),
            "net_sales": round(net, 2),
            "share_of_net": _r(M.safe_div(net, total_net), 4),
            "prev_net_sales": round(prev_net, 2),
            "growth": _r(M.growth(net, prev_net), 4),
            "avg_selling_price": _r(M.safe_div(net, a.units)),
            "discount_rate": _r(M.discount_rate(a.discount, a.gross), 4),
            "discounted_unit_share": _r(M.safe_div(a.discounted_units, a.units), 4),
            "return_rate_units": _r(M.safe_div(a.refunded_units, a.units), 4),
            "cost_coverage": _r(cost_cov, 3),
            "gross_profit": _r(gp),
            "gross_margin_pct": _r(M.safe_div(gp, kept_net), 4) if gp is not None else None,
            "aov_of_orders_with_product": _r(M.safe_div(a.order_net, len(a.orders))),
            "solo_order_share": _r(M.safe_div(a.solo_orders, len(a.orders)), 4),
            "buyers": len(a.customers),
            "price_min": min(prices) if prices else None,
            "price_max": max(prices) if prices else None,
            "variants_total": len(vs),
            "variants_available": sum(1 for v in vs if v.available_for_sale),
            "inventory_units": sum(inv) if inv else None,
            "days_of_cover": _r(M.safe_div(sum(inv), a.units / ((end - start).days + 1)), 1) if inv and a.units else None,
            "price_changes_in_period": price_changes.get(pid, 0),
        })
    rows.sort(key=lambda r: -r["net_sales"])
    acc = 0.0
    for i, r in enumerate(rows, start=1):
        acc += r["share_of_net"] or 0
        r["rank"] = i
        r["cumulative_share"] = round(acc, 4)

    # Products in the catalogue with no sales in the period (only active ones matter)
    unsold = [{"product_id": p.id, "title": p.title,
               "inventory_units": sum(v.inventory_quantity or 0 for v in variants_by_product.get(p.id, [])
                                      if v.inventory_tracked)}
              for pid, p in products.items() if pid not in by_p and (p.status or "ACTIVE") == "ACTIVE"]

    net_values = [r["net_sales"] for r in rows if r["net_sales"] > 0]
    h = M.hhi(net_values)
    concentration = {
        "products_with_sales": len(rows),
        "top1_share": _r(M.top_n_share(net_values, 1), 4),
        "top3_share": _r(M.top_n_share(net_values, 3), 4),
        "top10_share": _r(M.top_n_share(net_values, 10), 4),
        "products_for_80pct": M.items_for_share(net_values, 0.8),
        "hhi": _r(h, 4),
        "effective_products": _r(1 / h, 1) if h else None,
        "note": "HHI = Σ share². Effective products = 1/HHI: the number of equally-sized products that would "
                "give the same concentration.",
    }
    return {
        "period": {"start": start.isoformat(), "end": end.isoformat()},
        "previous_period": {"start": p_start.isoformat(), "end": p_end.isoformat()},
        "products": rows[:top_n],
        "unsold_active_products": unsold[:100],
        "concentration": concentration,
        "variants": variant_breakdown(by_v, variants_by_product, products),
        "data_quality": ds.quality,
    }


def variant_breakdown(by_v: dict[int, _Acc], variants_by_product: dict[int, list[Variant]],
                      products: dict[int, Product]) -> list[dict]:
    """Per-product variant spread, for products with ≥2 variants that sold."""
    out = []
    for pid, vs in variants_by_product.items():
        if len(vs) < 2:
            continue
        sold = [(v, by_v.get(v.id)) for v in vs]
        total_units = sum(a.units for _, a in sold if a)
        if total_units == 0:
            continue
        rows = []
        for v, a in sold:
            units = a.units if a else 0
            rows.append({"variant_id": v.id, "title": v.title, "sku": v.sku, "price": v.price,
                         "compare_at_price": v.compare_at_price, "units": units,
                         "unit_share": round(units / total_units, 4),
                         "net_sales": round((a.gross - a.discount), 2) if a else 0.0,
                         "return_rate_units": _r(M.safe_div(a.refunded_units, a.units), 4) if a else None,
                         "inventory": v.inventory_quantity if v.inventory_tracked else None,
                         "available": v.available_for_sale})
        rows.sort(key=lambda r: -r["units"])
        prices = {r["price"] for r in rows if r["price"] is not None}
        out.append({"product_id": pid, "title": products[pid].title if pid in products else str(pid),
                    "variants": rows, "units": total_units, "uniform_price": len(prices) <= 1,
                    "expected_share_if_even": round(1 / len(rows), 4)})
    out.sort(key=lambda r: -r["units"])
    return out[:50]


def co_purchase(db: Session, store: Store, start: date, end: date, *, min_pair_orders: int | None = None) -> dict:
    """Products bought together in the same order.

    support(A,B)   = orders containing A and B / all orders
    confidence A→B = orders with A and B / orders with A
    lift           = confidence A→B / (orders with B / all orders)  (>1 = bought together more than chance)
    """
    ds = load_dataset(db, store, start, end)
    n = len(ds.orders)
    item_orders: dict[int, int] = defaultdict(int)
    pair_orders: dict[tuple[int, int], int] = defaultdict(int)
    multi = 0
    titles: dict[int, str] = {}
    for o in ds.orders:
        items = sorted({ln.product_id for ln in o.lines if ln.product_id})
        for ln in o.lines:
            if ln.product_id:
                titles.setdefault(ln.product_id, ln.title)
        for pid in items:
            item_orders[pid] += 1
        if len(items) >= 2:
            multi += 1
            for a, b in combinations(items, 2):
                pair_orders[(a, b)] += 1
    threshold = min_pair_orders or max(3, round(n * 0.002))
    pairs = []
    for (a, b), nab in pair_orders.items():
        if nab < threshold:
            continue
        lift = (nab * n) / (item_orders[a] * item_orders[b])
        pairs.append({
            "a": a, "a_title": titles.get(a), "b": b, "b_title": titles.get(b), "orders_together": nab,
            "support": round(nab / n, 5), "confidence_a_to_b": round(nab / item_orders[a], 4),
            "confidence_b_to_a": round(nab / item_orders[b], 4), "lift": round(lift, 3),
        })
    pairs.sort(key=lambda p: (-p["lift"], -p["orders_together"]))
    return {"orders": n, "multi_product_orders": multi,
            "multi_product_share": round(multi / n, 4) if n else None,
            "min_pair_orders": threshold, "pairs": pairs[:50],
            "note": "Pairs below the minimum order count are hidden: with few orders, high lift is mostly noise."}


def product_weekly_units(db: Session, store: Store, product_id: int, weeks: int = 26) -> list[dict]:
    end = date.today()
    start = end - timedelta(weeks=weeks)
    ds = load_dataset(db, store, start, end)
    agg: dict[date, list] = defaultdict(lambda: [0, 0.0])
    for o in ds.orders:
        for ln in o.lines:
            if ln.product_id == product_id:
                wk = o.local_date - timedelta(days=o.local_date.weekday())
                agg[wk][0] += ln.qty
                agg[wk][1] += ln.net
    return [{"week": k.isoformat(), "units": v[0], "net_sales": round(v[1], 2)} for k, v in sorted(agg.items())]

