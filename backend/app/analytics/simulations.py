"""Transparent scenario models.

None of these forecasts demand. Each takes observed data plus explicit assumptions
(response rates, margins, costs) and returns, for a range of response scenarios, the
revenue and gross-profit effect — plus the break-even response at which the change
pays for itself. Whether that break-even is plausible is a judgement the user makes,
ideally validated with an experiment.
"""

from collections.abc import Sequence

from app.analytics import metrics as M

DEFAULT_RESPONSE_SCENARIOS = (0.05, 0.10, 0.20, 0.35)


def _r(v, nd=2):
    return None if v is None else round(v, nd)


def threshold_incentive(
    order_values: Sequence[float],
    threshold: float,
    *,
    incentive_cost: float,
    margin_pct: float | None,
    reach_window: float = 0.25,
    overshoot_pct: float = 0.05,
    mover_rates: Sequence[float] = DEFAULT_RESPONSE_SCENARIOS,
    already_offered: bool = False,
) -> dict:
    """Free gift / free shipping / reward unlocked at a spend threshold.

    Population (from observed orders in the period):
      above     = orders already ≥ threshold → receive the incentive without changing behaviour
      reachable = orders in [threshold × (1 − reach_window), threshold)
    For a mover rate r (share of reachable orders that top up):
      movers               = r × reachable
      incremental revenue  = Σ over movers of (threshold × (1 + overshoot) − current value)  [expected value]
      incremental GP       = incremental revenue × margin
      incentive cost       = (above + movers) × incentive_cost   (above is zero if already_offered)
      net effect           = incremental GP − incentive cost
    Break-even r solves net effect = 0.
    Not modelled: extra orders from higher conversion, pull-forward of future orders,
    returns of top-up items, operational cost of handling gifts.
    """
    vals = [v for v in order_values if v > 0]
    n = len(vals)
    lo = threshold * (1 - reach_window)
    above = [v for v in vals if v >= threshold]
    reach = [v for v in vals if lo <= v < threshold]
    target = threshold * (1 + overshoot_pct)
    topup_total = sum(target - v for v in reach)
    deadweight_orders = 0 if already_offered else len(above)
    scenarios = []
    for r in mover_rates:
        movers = r * len(reach)
        inc_rev = r * topup_total
        inc_gp = inc_rev * margin_pct if margin_pct is not None else None
        cost = (deadweight_orders + movers) * incentive_cost
        scenarios.append({
            "mover_rate": r, "movers": _r(movers, 1), "incremental_revenue": _r(inc_rev),
            "incremental_gross_profit": _r(inc_gp), "incentive_cost": _r(cost),
            "net_effect": _r(inc_gp - cost) if inc_gp is not None else None,
        })
    break_even = None
    if margin_pct is not None and reach:
        denom = topup_total * margin_pct - len(reach) * incentive_cost
        if denom > 0:
            break_even = deadweight_orders * incentive_cost / denom
    return {
        "threshold": threshold,
        "orders": n,
        "orders_above": len(above),
        "share_above": _r(M.safe_div(len(above), n), 4),
        "orders_reachable": len(reach),
        "share_reachable": _r(M.safe_div(len(reach), n), 4),
        "avg_gap_reachable": _r(M.safe_div(sum(threshold - v for v in reach), len(reach))),
        "deadweight_cost": _r(deadweight_orders * incentive_cost),
        "scenarios": scenarios,
        "break_even_mover_rate": _r(break_even, 4),
        "break_even_feasible": break_even is not None and break_even <= 1,
        "assumptions": [
            f"Orders within {reach_window:.0%} below the threshold are the only ones that might top up.",
            f"Movers land {overshoot_pct:.0%} above the threshold on average.",
            f"Incentive costs {incentive_cost:.2f} per qualifying order (gift COGS, or shipping no longer charged).",
            (f"Incremental merchandise earns a {margin_pct:.0%} gross margin." if margin_pct is not None
             else "Gross margin unknown — profit effect cannot be computed; add unit costs."),
            ("Orders already above the threshold receive the incentive at full cost (deadweight)."
             if not already_offered else "The incentive is already offered, so existing qualifiers add no new cost."),
            "Effects on conversion rate, future orders and returns are not modelled.",
        ],
    }


def candidate_thresholds(order_values: Sequence[float], existing: float | None = None) -> list[float]:
    vals = sorted(v for v in order_values if v > 0)
    if len(vals) < 20:
        return [existing] if existing else []
    out = set()
    for q in (0.5, 0.6, 0.7, 0.8):
        raw = M.percentile(vals, q) * 1.15
        step = 5 if raw < 100 else 10 if raw < 300 else 25
        out.add(float(-(-raw // step) * step))
    if existing:
        out.add(float(existing))
    return sorted(out)


def quantity_incentive(
    orders_units_values: Sequence[tuple[int, float]],
    *,
    min_units: int,
    discount_pct: float,
    avg_unit_price: float,
    margin_pct: float | None,
    mover_rates: Sequence[float] = DEFAULT_RESPONSE_SCENARIOS,
) -> dict:
    """"Buy N, save d% on the order".

    qualifying = orders already with ≥ N units → receive d% at full cost (deadweight)
    reachable  = orders with N − 1 units → may add one unit at avg_unit_price
    For mover rate r: incremental revenue = movers × avg_unit_price × (1 − d)
                      discount cost on movers' original basket = movers × avg value × d
    Net GP effect = movers × (unit price × (1 − d) − unit cost) − movers × value × d − deadweight
    """
    qual = [(u, v) for u, v in orders_units_values if u >= min_units]
    reach = [(u, v) for u, v in orders_units_values if u == min_units - 1]
    dead = sum(v for _, v in qual) * discount_pct
    reach_avg = M.safe_div(sum(v for _, v in reach), len(reach)) or 0.0
    unit_cost = avg_unit_price * (1 - margin_pct) if margin_pct is not None else None
    scenarios = []
    for r in mover_rates:
        movers = r * len(reach)
        inc_rev = movers * (avg_unit_price * (1 - discount_pct) - reach_avg * discount_pct)
        if unit_cost is not None:
            gp = movers * (avg_unit_price * (1 - discount_pct) - unit_cost - reach_avg * discount_pct) - dead
        else:
            gp = None
        scenarios.append({"mover_rate": r, "movers": _r(movers, 1), "incremental_revenue": _r(inc_rev - dead),
                          "net_effect": _r(gp)})
    be = None
    if unit_cost is not None and reach:
        per_mover = avg_unit_price * (1 - discount_pct) - unit_cost - reach_avg * discount_pct
        if per_mover > 0:
            be = dead / (per_mover * len(reach))
    return {
        "min_units": min_units, "discount_pct": discount_pct,
        "orders_qualifying_now": len(qual), "orders_reachable": len(reach),
        "deadweight_discount": _r(dead), "scenarios": scenarios,
        "break_even_mover_rate": _r(be, 4), "break_even_feasible": be is not None and be <= 1,
        "assumptions": [
            f"Orders with exactly {min_units - 1} unit(s) are the only ones that might add a unit.",
            f"An added unit sells at the average unit price {avg_unit_price:.2f} before the discount.",
            "Orders already qualifying receive the discount without buying more (deadweight).",
            "Incremental orders from new customers are not modelled.",
        ],
    }


def price_change(
    *,
    current_price: float,
    unit_cost: float | None,
    units: int,
    new_price: float,
    volume_changes: Sequence[float] = (-0.30, -0.20, -0.10, -0.05, 0.0, 0.05, 0.10),
) -> dict:
    """Revenue and gross profit for a price change under assumed volume responses.

    Break-even volume change: Δv = −p / (m + p)   (see metrics.break_even_volume_change)
    """
    p = (new_price - current_price) / current_price if current_price else 0.0
    m = (current_price - unit_cost) / current_price if unit_cost is not None and current_price else None
    base_rev = current_price * units
    base_gp = (current_price - unit_cost) * units if unit_cost is not None else None
    rows = []
    for dv in volume_changes:
        u = units * (1 + dv)
        rev = new_price * u
        gp = (new_price - unit_cost) * u if unit_cost is not None else None
        rows.append({"volume_change": dv, "units": _r(u, 1), "revenue": _r(rev),
                     "revenue_change": _r(rev - base_rev), "gross_profit": _r(gp),
                     "gross_profit_change": _r(gp - base_gp) if gp is not None and base_gp is not None else None})
    return {
        "current_price": current_price, "new_price": new_price, "price_change_pct": _r(p, 4),
        "unit_margin_pct": _r(m, 4), "baseline_units": units, "baseline_revenue": _r(base_rev),
        "baseline_gross_profit": _r(base_gp), "scenarios": rows,
        "break_even_volume_change": _r(M.break_even_volume_change(p, m), 4) if m is not None else None,
        "assumptions": [
            "Volume responses are scenarios, not estimates of price elasticity.",
            "Unit cost is constant across the volumes shown." if unit_cost is not None
            else "Unit cost unknown — only revenue effects can be shown; add costs for profit.",
            "Effects on other products (substitution, basket composition) are not modelled.",
        ],
    }


def bundle(
    *,
    price_a: float, price_b: float, cost_a: float | None, cost_b: float | None,
    orders_with_a: int, orders_with_both: int, bundle_discount_pct: float,
    attach_uplifts: Sequence[float] = (0.02, 0.05, 0.10, 0.15),
) -> dict:
    """Bundle A+B at (price_a + price_b) × (1 − d).

    Movers: a share k of orders that had A without B now take the bundle.
      GP change per mover    = price_b × (1 − d) − price_a × d − cost_b
      Revenue per mover      = (price_a + price_b) × (1 − d) − price_a
    Cannibalisation: orders that already bought both now receive the discount:
      lost = orders_with_both × (price_a + price_b) × d
    """
    a_only = max(0, orders_with_a - orders_with_both)
    bundle_price = (price_a + price_b) * (1 - bundle_discount_pct)
    cannibal = orders_with_both * (price_a + price_b) * bundle_discount_pct
    gp_per = price_b * (1 - bundle_discount_pct) - price_a * bundle_discount_pct - cost_b if cost_b is not None else None
    rev_per = bundle_price - price_a
    rows = []
    for k in attach_uplifts:
        movers = k * a_only
        rows.append({"attach_uplift": k, "movers": _r(movers, 1),
                     "incremental_revenue": _r(movers * rev_per - cannibal),
                     "net_effect": _r(movers * gp_per - cannibal) if gp_per is not None else None})
    be = None
    if gp_per is not None and gp_per > 0 and a_only:
        be = cannibal / (gp_per * a_only)
    return {
        "bundle_price": _r(bundle_price), "current_attach_rate": _r(M.safe_div(orders_with_both, orders_with_a), 4),
        "orders_with_a_only": a_only, "cannibalised_discount": _r(cannibal), "gross_profit_per_mover": _r(gp_per),
        "scenarios": rows, "break_even_attach_uplift": _r(be, 4),
        "break_even_feasible": be is not None and be <= 1,
        "assumptions": [
            "Only orders that already contain A are considered; the bundle attracting new customers is not modelled.",
            "Customers who already buy both receive the bundle discount (cannibalisation).",
            "Cost of B is needed for profit; without it only revenue is shown." if cost_b is None else
            "Unit costs are constant.",
        ],
        "cost_a_known": cost_a is not None,
    }


def discount_depth(*, avg_order_value: float, margin_pct: float, current_pct: float, proposed_pct: float,
                   current_redemptions: int) -> dict:
    """Compare two discount depths on gross profit per redeemed order: GP = V × (m − d).

    Required redemption ratio for the proposed depth to match the current total GP:
        R₂ / R₁ = (m − d₁) / (m − d₂)
    """
    gp1 = avg_order_value * (margin_pct - current_pct)
    gp2 = avg_order_value * (margin_pct - proposed_pct)
    ratio = (margin_pct - current_pct) / (margin_pct - proposed_pct) if margin_pct > proposed_pct else None
    return {
        "gp_per_order_current": _r(gp1), "gp_per_order_proposed": _r(gp2),
        "required_redemption_ratio": _r(ratio, 4),
        "required_redemptions": _r(ratio * current_redemptions, 0) if ratio else None,
        "assumptions": ["Average order value is unchanged by the discount depth.",
                        "All redemptions are compared at the same margin before discount."],
    }


def gift_vs_discount(*, qualifying_aov: float, discount_pct: float, gift_unit_cost: float,
                     gift_perceived_value: float | None = None) -> dict:
    """Cost per qualifying order of a d% discount vs a free gift with known unit cost."""
    disc_cost = qualifying_aov * discount_pct
    return {
        "discount_cost_per_order": _r(disc_cost), "gift_cost_per_order": _r(gift_unit_cost),
        "saving_per_order_with_gift": _r(disc_cost - gift_unit_cost),
        "perceived_value_ratio": _r(M.safe_div(gift_perceived_value, gift_unit_cost), 2)
        if gift_perceived_value else None,
        "assumptions": ["Both incentives drive the same number of qualifying orders — test this; it is not given.",
                        "Gift cost includes product COGS only unless you add packaging/handling."],
    }
