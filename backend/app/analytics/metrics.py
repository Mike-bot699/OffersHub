"""Deterministic metric formulas.

Every function is pure, documented with its formula, and returns None when an input
needed for the definition is missing — never a silent zero. See docs/METRICS.md.

Vocabulary (all amounts in shop currency, excluding tax):
  gross_sales      = Σ line original unit price × quantity         (merchandise, excl. gift cards)
  discounts        = Σ discounts applied to the order (line + order level)
  net_sales        = gross_sales − discounts                       (before returns)
  returns          = Σ refunded merchandise
  net_sales_kept   = net_sales − returns
  COGS             = Σ unit_cost × (quantity − refunded quantity)  (returned units assumed restocked)
  gross_profit     = net_sales_kept − COGS
  contribution     = gross_profit + shipping charged − shipping cost − payment fees − fulfilment cost
"""

import math
from collections.abc import Sequence


def safe_div(num: float | None, den: float | None) -> float | None:
    if num is None or den in (None, 0):
        return None
    return num / den


def aov(net_sales: float, orders: int) -> float | None:
    """Average order value = net_sales / orders (merchandise after discounts, before returns,
    excluding shipping and tax — consistent with Shopify's AOV definition)."""
    return safe_div(net_sales, orders)


def units_per_order(units: int, orders: int) -> float | None:
    """Units per order = Σ units / orders."""
    return safe_div(units, orders)


def discount_rate(discounts: float, gross_sales: float) -> float | None:
    """Discount rate = discounts / gross_sales. Share of list-price value given away."""
    return safe_div(discounts, gross_sales)


def conversion_rate(orders: int | None, sessions: int | None) -> float | None:
    """Conversion rate = orders / sessions. Requires a traffic source; Shopify orders alone cannot provide it."""
    return safe_div(orders, sessions)


def gross_margin_pct(net_sales_kept: float | None, cogs: float | None) -> float | None:
    """Gross margin % = (net_sales_kept − COGS) / net_sales_kept."""
    if net_sales_kept is None or cogs is None:
        return None
    return safe_div(net_sales_kept - cogs, net_sales_kept)


def contribution(
    gross_profit: float | None,
    shipping_charged: float,
    orders: int,
    *,
    shipping_cost_per_order: float | None,
    payment_fee_pct: float | None,
    payment_fee_fixed: float | None,
    fulfillment_cost_per_order: float | None,
    customer_paid: float,
) -> tuple[float | None, list[str]]:
    """Order contribution = gross_profit + shipping charged − shipping cost − payment fees − fulfilment cost.

    Returns (value, missing_components). If gross profit is unknown the value is None.
    Missing cost components are listed so the UI can show the contribution is overstated.
    """
    if gross_profit is None:
        return None, ["unit costs (COGS)"]
    missing = []
    value = gross_profit + shipping_charged
    if shipping_cost_per_order is None:
        missing.append("shipping cost per order")
    else:
        value -= shipping_cost_per_order * orders
    if payment_fee_pct is None and payment_fee_fixed is None:
        missing.append("payment processing fees")
    else:
        value -= (payment_fee_pct or 0) * customer_paid + (payment_fee_fixed or 0) * orders
    if fulfillment_cost_per_order is None:
        missing.append("fulfilment cost per order")
    else:
        value -= fulfillment_cost_per_order * orders
    return value, missing


def growth(current: float | None, previous: float | None) -> float | None:
    """Period-over-period growth = (current − previous) / previous."""
    if current is None or previous in (None, 0):
        return None
    return (current - previous) / abs(previous)


# ---- concentration ---------------------------------------------------------------------------
def shares(values: Sequence[float]) -> list[float]:
    total = sum(values)
    return [v / total for v in values] if total > 0 else [0.0 for _ in values]


def hhi(values: Sequence[float]) -> float | None:
    """Herfindahl–Hirschman index = Σ shareᵢ² (0–1). 1/HHI is the 'effective number of products'."""
    if not values or sum(values) <= 0:
        return None
    return sum(s * s for s in shares(values))


def top_n_share(values: Sequence[float], n: int) -> float | None:
    if not values or sum(values) <= 0:
        return None
    return sum(sorted(values, reverse=True)[:n]) / sum(values)


def items_for_share(values: Sequence[float], target: float = 0.8) -> int | None:
    """Smallest number of items whose combined share reaches `target` (Pareto count)."""
    if not values or sum(values) <= 0:
        return None
    total, acc = sum(values), 0.0
    for i, v in enumerate(sorted(values, reverse=True), start=1):
        acc += v
        if acc / total >= target - 1e-12:
            return i
    return len(values)


# ---- pricing economics ----------------------------------------------------------------------
def break_even_volume_change(price_change_pct: float, unit_margin_pct: float) -> float | None:
    """Volume change needed to keep total gross profit constant after a price change.

    With price P, unit cost C and unit margin m = (P − C)/P, a price change of p (fraction)
    keeps gross profit constant when volume changes by  Δv = −p / (m + p).
      e.g. m = 40%, p = −10%  →  Δv = +33.3%  (a 10% cut needs a third more units)
           m = 40%, p = +10%  →  Δv = −20.0%  (a 10% rise can lose a fifth of units)
    """
    if unit_margin_pct is None or (unit_margin_pct + price_change_pct) <= 0:
        return None
    return -price_change_pct / (unit_margin_pct + price_change_pct)


def discount_break_even_uplift(discount_pct: float, margin_pct: float) -> float | None:
    """Extra orders (as a fraction of orders that would have happened anyway) needed for a
    discount of d on everything to break even on gross profit:  uplift = d / (m − d).
    Returns None when the discount is at or above the margin (it can never break even)."""
    if margin_pct is None or margin_pct - discount_pct <= 0:
        return None
    return discount_pct / (margin_pct - discount_pct)


# ---- statistics -------------------------------------------------------------------------------
def _norm_cdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def two_proportion_test(conv_a: int, n_a: int, conv_b: int, n_b: int) -> dict | None:
    """Two-sided z-test for a difference in proportions (e.g. conversion of control vs treatment).
    Returns rates, absolute/relative lift, z, p-value and a 95% CI on the difference."""
    if n_a <= 0 or n_b <= 0:
        return None
    pa, pb = conv_a / n_a, conv_b / n_b
    pooled = (conv_a + conv_b) / (n_a + n_b)
    se_pooled = math.sqrt(pooled * (1 - pooled) * (1 / n_a + 1 / n_b))
    se = math.sqrt(pa * (1 - pa) / n_a + pb * (1 - pb) / n_b)
    z = (pb - pa) / se_pooled if se_pooled > 0 else 0.0
    p = 2 * (1 - _norm_cdf(abs(z)))
    return {
        "rate_a": pa, "rate_b": pb, "diff": pb - pa, "relative_lift": safe_div(pb - pa, pa),
        "z": z, "p_value": p, "ci95": (pb - pa - 1.96 * se, pb - pa + 1.96 * se),
    }


def mean_difference_test(mean_a: float, sd_a: float, n_a: int, mean_b: float, sd_b: float, n_b: int) -> dict | None:
    """Welch-style comparison of two means using a normal approximation (adequate for n ≥ 30 per arm)."""
    if n_a < 2 or n_b < 2:
        return None
    se = math.sqrt(sd_a ** 2 / n_a + sd_b ** 2 / n_b)
    diff = mean_b - mean_a
    z = diff / se if se > 0 else 0.0
    return {"diff": diff, "relative_lift": safe_div(diff, mean_a), "z": z,
            "p_value": 2 * (1 - _norm_cdf(abs(z))), "ci95": (diff - 1.96 * se, diff + 1.96 * se),
            "approximation": "normal" if min(n_a, n_b) >= 30 else "normal (small sample — treat with caution)"}


def required_sample_per_arm(base_rate: float, min_detectable_rel_lift: float, *, alpha: float = 0.05,
                            power: float = 0.8) -> int | None:
    """Sample size per arm to detect a relative lift in a proportion (two-sided)."""
    if not 0 < base_rate < 1 or min_detectable_rel_lift <= 0:
        return None
    z_a = {0.05: 1.959964, 0.1: 1.644854}.get(alpha, 1.959964)
    z_b = {0.8: 0.841621, 0.9: 1.281552}.get(power, 0.841621)
    p1, p2 = base_rate, base_rate * (1 + min_detectable_rel_lift)
    if p2 >= 1:
        return None
    pbar = (p1 + p2) / 2
    num = (z_a * math.sqrt(2 * pbar * (1 - pbar)) + z_b * math.sqrt(p1 * (1 - p1) + p2 * (1 - p2))) ** 2
    return math.ceil(num / (p2 - p1) ** 2)


def percentile(sorted_values: Sequence[float], q: float) -> float | None:
    """Linear-interpolation percentile (q in 0–1) of an already sorted sequence."""
    if not sorted_values:
        return None
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    pos = q * (len(sorted_values) - 1)
    lo = math.floor(pos)
    hi = min(lo + 1, len(sorted_values) - 1)
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * (pos - lo)
