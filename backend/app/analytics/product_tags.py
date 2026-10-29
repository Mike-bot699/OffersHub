"""Performance tags for products: Best Seller, Top Seller, Popular, Free.

Same rule as the team's other tool (Email Maker, products sync), applied per store over the
last 30 days of sales:

- revenue = current price x units sold; the report's own revenue is used only when the
  product has no price;
- ranking = products with revenue > 0, by revenue then units. If no product in the store has
  revenue data, units are used instead;
- bands over the ranked products only: top 10% Best Seller, 10–25% Top Seller, 25–50% Popular,
  the rest untagged;
- Free = units sold with revenue 0 (gifts). Excluded from the ranking, shown only in stores
  that have revenue data.

One addition, declared: below `min_ranked` products with sales no band is assigned, because
relative bands on a handful of products make a $75 product a "Best Seller".
"""

from dataclasses import dataclass

TAGS = {"best_seller": "🔥 Best Seller", "top_seller": "⭐ Top Seller", "popular": "Popular", "free": "Free"}
BANDS = ((0.10, "best_seller"), (0.25, "top_seller"), (0.50, "popular"))
DEFAULT_MIN_RANKED = 10


@dataclass
class Sales:
    product_id: int
    units: int
    price: float | None
    report_revenue: float | None


def compute_tags(rows: list[Sales], *, min_ranked: int = DEFAULT_MIN_RANKED) -> dict[int, dict]:
    """product_id -> {tag, rank, units, revenue}. Products absent from the result have no sales."""
    out: dict[int, dict] = {}
    with_rev = []
    for r in rows:
        revenue = r.price * r.units if r.price is not None else r.report_revenue
        out[r.product_id] = {"tag": None, "rank": None, "units": r.units, "revenue": revenue}
        with_rev.append((r, revenue))
    has_revenue = any(rev is not None and rev > 0 for _, rev in with_rev)
    if has_revenue:
        for r, rev in with_rev:
            if r.units > 0 and rev is not None and rev == 0:
                out[r.product_id]["tag"] = "free"
        ranked = [(r, rev) for r, rev in with_rev if rev is not None and rev > 0]
        ranked.sort(key=lambda x: (-x[1], -x[0].units))
    else:
        ranked = [(r, rev) for r, rev in with_rev if r.units > 0]
        ranked.sort(key=lambda x: -x[0].units)
    n = len(ranked)
    for i, (r, _) in enumerate(ranked):
        out[r.product_id]["rank"] = i + 1
        if n < min_ranked:
            continue
        for share, tag in BANDS:
            if i < n * share:
                out[r.product_id]["tag"] = tag
                break
    return out
