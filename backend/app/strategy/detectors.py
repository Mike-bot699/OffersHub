"""Opportunity detectors.

Each detector inspects the analysis context and returns zero or more RecDraft objects.
Rules of the house:
- Fire only when the evidence clears an explicit threshold; otherwise return nothing.
- Never fill a gap with a guess: list it in `missing_data`, lower confidence, or skip.
- Impact estimates are ranges from transparent scenarios, labelled as such; exposure
  (e.g. "discounts given") is never presented as incremental profit.
"""

import re
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy.orm import Session

from app.analytics import simulations as S
from app.models import Store
from app.models.enums import Objective, StrategyType
from app.strategy.types import Evidence, RecDraft

SIZE_RE = re.compile(r"^(xxs|xs|s|m|l|xl|xxl|xxxl|\d{1,2}(\.5)?|\d{2}/\d{2}|s/m|m/l|l/xl|one size|default title)$",
                     re.IGNORECASE)


@dataclass
class Context:
    db: Session
    store: Store
    start: date
    end: date
    summary: dict
    products: dict
    copurchase: dict
    basket: dict
    gifts: dict
    history: dict
    alerts: list[dict]
    objectives: list[str]
    integration: dict | None
    extra: dict = field(default_factory=dict)

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1

    @property
    def annualize(self) -> float:
        return 365 / self.days

    @property
    def src(self) -> str:
        return ("Demo orders" if self.store.is_demo else "Store orders") + f", {self.start} to {self.end}"

    @property
    def cur(self) -> str:
        return self.store.currency

    def money(self, v: float | None) -> str:
        return "n/a" if v is None else f"{self.cur} {v:,.0f}"


def nice_price(target: float, *, like: float) -> float:
    """Round a proposed price to the same style as the current one (whole number, .99 or .95 endings)."""
    cents = round(like % 1, 2)
    if cents in (0.99, 0.95, 0.9):
        return round(int(target) + cents if int(target) + cents >= target - 0.5 else int(target) + 1 + cents, 2)
    return float(round(target)) if like >= 10 else round(target, 2)


def pct(v: float | None, nd: int = 1) -> str:
    return "n/a" if v is None else f"{v * 100:.{nd}f}%"


# ---------------------------------------------------------------------------------------------
def data_gaps(ctx: Context) -> list[RecDraft]:
    out = []
    q = ctx.summary["data_quality"]
    k = ctx.summary["kpis"]
    annual_net = k["net_sales"] * ctx.annualize
    if q["orders"] == 0:
        out.append(RecDraft(
            detector="data_gaps", key="no_orders", strategy_type=StrategyType.DATA_QUALITY, scope_type="store",
            title="No order data for this period",
            problem="There are no orders in the selected period, so no analysis can run.",
            proposed_action="Connect the store and run a synchronisation, or widen the date range.",
            economic_rationale="Every recommendation depends on order history.",
            next_action="Open Integrations and connect Shopify, then run a manual sync.",
            confidence="high", confidence_reason="Directly observed.", complexity="low", risk_level="low",
            blocks_analysis=True, impact_hint=0))
        return out
    cov = q.get("cost_coverage")
    if cov is None or cov < 0.9:
        out.append(RecDraft(
            detector="data_gaps", key="costs", strategy_type=StrategyType.DATA_QUALITY, scope_type="store",
            title="Add unit costs to unlock margin and profit analysis",
            problem=f"Only {pct(cov or 0)} of net sales have a recorded unit cost. Gross margin, contribution, "
                    "price tests and gift/threshold economics cannot be calculated reliably.",
            proposed_action="Import a costs CSV (SKU, unit_cost) or enter Cost per item in Shopify and grant "
                            "read_inventory so costs sync automatically.",
            economic_rationale="Without costs, revenue growth can hide margin erosion; several opportunities "
                               "are withheld until margins are known.",
            evidence=[Evidence("Net sales with recorded cost", pct(cov or 0), ctx.src)],
            missing_data=["Unit cost per variant"], confidence="high", confidence_reason="Directly measured.",
            complexity="low", risk_level="low", next_action="Settings → Data imports → Costs CSV.",
            success_metrics=["Cost coverage ≥ 95% of net sales"], blocks_analysis=True,
            impact_hint=0.04 * annual_net))
    if not k.get("sessions"):
        out.append(RecDraft(
            detector="data_gaps", key="traffic", strategy_type=StrategyType.DATA_QUALITY, scope_type="store",
            title="Add traffic data to measure conversion",
            problem="No sessions data is available, so conversion rate and revenue per visitor cannot be measured "
                    "and experiments can only be judged on orders.",
            proposed_action="Import a daily sessions export (Shopify Analytics → Sessions over time, or GA4) "
                            "as a traffic CSV.",
            economic_rationale="Conversion is one of the three revenue drivers (traffic × conversion × AOV).",
            missing_data=["Daily sessions"], confidence="high", confidence_reason="Directly observed.",
            complexity="low", risk_level="low", next_action="Settings → Data imports → Traffic CSV.",
            blocks_analysis=True, impact_hint=0.02 * annual_net))
    if not ctx.summary["customers"].get("available"):
        out.append(RecDraft(
            detector="data_gaps", key="customers", strategy_type=StrategyType.DATA_QUALITY, scope_type="store",
            title="Enable customer IDs for repeat-purchase analysis",
            problem="Orders carry no customer identifier, so repeat purchase, new vs returning and retention "
                    "opportunities cannot be analysed.",
            proposed_action="Grant read_customers (plus protected customer data level 1) to the Shopify app. "
                            "Only pseudonymous IDs are stored.",
            economic_rationale="Retention levers are often cheaper than acquisition, but must be measured first.",
            missing_data=["Customer ID on orders"], confidence="high", confidence_reason="Directly observed.",
            complexity="low", risk_level="low", next_action="Update app scopes in the Shopify Dev Dashboard.",
            blocks_analysis=True, impact_hint=0.015 * annual_net))
    return out


def hero_concentration(ctx: Context) -> list[RecDraft]:
    conc = ctx.products["concentration"]
    prods = ctx.products["products"]
    if len(prods) < 3 or (conc["top1_share"] or 0) < 0.30 and (conc["top3_share"] or 0) < 0.65:
        return []
    hero = prods[0]
    pairs = [p for p in ctx.copurchase["pairs"] if hero["product_id"] in (p["a"], p["b"])]
    comp = None
    if pairs:
        best = max(pairs, key=lambda p: p["lift"] * p["orders_together"])
        comp_id = best["b"] if best["a"] == hero["product_id"] else best["a"]
        comp = next((p for p in prods if p["product_id"] == comp_id), None)
    ev = [
        Evidence("Share of net sales — top product", pct(conc["top1_share"]), ctx.src),
        Evidence("Share of net sales — top 3 products", pct(conc["top3_share"]), ctx.src),
        Evidence("Effective number of products (1/HHI)", conc["effective_products"], "derived"),
        Evidence(f"{hero['title']} growth vs previous period", pct(hero["growth"]), ctx.src),
        Evidence(f"{hero['title']} orders with no other product", pct(hero["solo_order_share"]), ctx.src),
    ]
    if hero.get("gross_margin_pct") is not None:
        ev.append(Evidence(f"{hero['title']} gross margin", pct(hero["gross_margin_pct"]), "derived from unit costs"))
    diversify = Objective.DIVERSIFY_REVENUE in ctx.objectives
    impact = None
    assumptions = ["Concentration is not a problem in itself; the hero product is evidence of product–market fit."]
    missing = []
    if comp and comp.get("gross_margin_pct") is not None and hero["solo_order_share"]:
        solo_orders = hero["orders"] * hero["solo_order_share"]
        unit_gp = (comp["avg_selling_price"] or 0) * comp["gross_margin_pct"]
        vals = [solo_orders * k * unit_gp * ctx.annualize for k in (0.03, 0.06, 0.10)]
        impact = {"metric": "gross profit / year", "low": round(vals[0]), "base": round(vals[1]),
                  "high": round(vals[2]), "unit": ctx.cur,
                  "basis": f"If 3% / 6% / 10% of single-item {hero['title']} orders add {comp['title']} at its "
                           f"average selling price and margin."}
        assumptions.append("Attach-rate scenarios (3–10%) are assumptions, not estimates.")
    elif comp:
        missing.append(f"Unit cost of {comp['title']} (needed to value the cross-sell)")
    if diversify:
        title = f"Reduce dependence on {hero['title']}"
        action = (f"Grow the next tier of products: feature the products ranked 2–5 alongside {hero['title']}, "
                  f"test a 'pairs with' module{' with ' + comp['title'] if comp else ''}, and develop one adjacent "
                  "product for the hero's buyers.")
        stype = StrategyType.DIVERSIFICATION
    else:
        title = f"Monetise the hero product: {hero['title']}"
        action = (f"Build around {hero['title']} rather than away from it: "
                  + (f"attach {comp['title']} as a complementary add-on (it is bought together {best['lift']:.1f}× "
                     "more than chance), " if comp else "add a complementary add-on at checkout, ")
                  + "and test a premium edition or new colourway priced above the current range to anchor value.")
        stype = StrategyType.CROSS_SELL
    return [RecDraft(
        detector="hero_concentration", key=f"p{hero['product_id']}", strategy_type=stype, scope_type="product",
        scope={"product_ids": [hero["product_id"]] + ([comp["product_id"]] if comp else []),
               "labels": [hero["title"]]},
        title=title,
        problem=f"{hero['title']} generates {pct(hero['share_of_net'])} of net sales and "
                f"{pct(hero['solo_order_share'])} of its orders contain nothing else.",
        proposed_action=action,
        economic_rationale="A product that already converts is the cheapest place to add order value: each "
                           "added item rides on traffic that is already paid for. Dependence on one product is "
                           "a risk only if supply, ads or demand for it falter.",
        evidence=ev, estimated_impact=impact, assumptions=assumptions, missing_data=missing,
        confidence="medium" if impact else "low",
        confidence_reason="Concentration and basket composition are observed; the response to a cross-sell or "
                          "premium edition is not.",
        risks=["Pushing add-ons too hard can lower conversion of the hero product.",
               f"{pct(hero['share_of_net'])} of revenue is exposed to a single product's supply and ad performance."],
        risk_level="medium", complexity="medium",
        recommended_test=f"Show the add-on to a random 50% of {hero['title']} product-page visitors for 3–4 weeks; "
                         "compare attach rate and contribution per visitor.",
        success_metrics=["Attach rate of the add-on", "AOV of orders containing the hero",
                         "Hero product conversion rate (must not fall)"],
        next_action="Pick the add-on and set up the product-page module; create the experiment in Experiments.",
        frameworks=["anchoring_decoy", "bundling", "offer_stacking"],
        impact_hint=(impact or {}).get("base") or 0.01 * ctx.summary["kpis"]["net_sales"] * ctx.annualize,
    )]


def declining_products(ctx: Context) -> list[RecDraft]:
    prods = ctx.products["products"]
    prev_total = sum(p["prev_net_sales"] for p in prods) or 0
    out = []
    for p in prods:
        if prev_total <= 0 or p["prev_net_sales"] < 0.03 * prev_total:
            continue
        if p["growth"] is None or p["growth"] > -0.25:
            continue
        lost = p["prev_net_sales"] - p["net_sales"]
        availability = p["variants_total"] and p["variants_available"] < 0.6 * p["variants_total"]
        if availability:
            cause = (f"only {p['variants_available']} of {p['variants_total']} variants are available — the drop "
                     "is consistent with stockouts")
            stype, action = StrategyType.INVENTORY, "Restock the unavailable variants before spending on demand."
        elif p["price_changes_in_period"]:
            cause = f"the price changed {p['price_changes_in_period']} time(s) in the period"
            stype, action = StrategyType.PRICING, "Review whether the price change explains the drop; consider reverting."
        else:
            cause = "no availability or price change explains it; check seasonality (same period last year), " \
                    "ad delivery and the product page"
            stype, action = StrategyType.MERCHANDISING, ("Diagnose before acting: compare with the same period last "
                                                          "year, check paid-traffic delivery to this product and its "
                                                          "page conversion; refresh imagery/merchandising if needed.")
        margin = p.get("gross_margin_pct")
        out.append(RecDraft(
            detector="declining_product", key=f"p{p['product_id']}", strategy_type=stype, scope_type="product",
            scope={"product_ids": [p["product_id"]], "labels": [p["title"]]},
            title=f"{p['title']} sales fell {pct(-p['growth'], 0)}",
            problem=f"Net sales fell from {ctx.money(p['prev_net_sales'])} to {ctx.money(p['net_sales'])} versus the "
                    f"previous period; {cause}.",
            proposed_action=action,
            economic_rationale="Recovering an established product is usually cheaper than building a new one.",
            evidence=[Evidence("Net sales, previous period", ctx.money(p["prev_net_sales"]), ctx.src),
                      Evidence("Net sales, this period", ctx.money(p["net_sales"]), ctx.src),
                      Evidence("Variants available", f"{p['variants_available']}/{p['variants_total']}", "catalogue"),
                      Evidence("Price changes in period", p["price_changes_in_period"], "price history")],
            estimated_impact={"metric": "net sales at stake / year", "low": None, "base": round(lost * ctx.annualize),
                              "high": None, "unit": ctx.cur,
                              "basis": "Exposure: the period-over-period decline annualised. Not a forecast of "
                                       "what can be recovered."},
            assumptions=["The previous period is a fair baseline (it may not be if the product is seasonal)."],
            missing_data=[] if margin is not None else ["Unit cost (to value the decline in profit)"],
            confidence="medium" if availability else "low",
            confidence_reason="The decline is measured; its cause is inferred.",
            risks=["Reacting to a seasonal dip with discounts erodes margin for no gain."],
            risk_level="low", complexity="low" if availability else "medium",
            success_metrics=["Weekly net sales of the product", "Variant availability"],
            next_action="Open the product in Products to check weekly trend and variant stock.",
            frameworks=["inventory_availability"] if availability else [],
            impact_hint=lost * ctx.annualize * (margin or 0.4) * 0.3,
        ))
    return sorted(out, key=lambda r: -(r.impact_hint or 0))[:4]


def stockout_risk(ctx: Context) -> list[RecDraft]:
    out = []
    for p in ctx.products["products"][:15]:
        if (p["share_of_net"] or 0) < 0.03 or p["days_of_cover"] is None:
            continue
        if p["days_of_cover"] >= 21:
            continue
        daily = p["net_sales"] / ctx.days
        gap = max(0, 30 - p["days_of_cover"])
        out.append(RecDraft(
            detector="stockout_risk", key=f"p{p['product_id']}", strategy_type=StrategyType.INVENTORY,
            scope_type="product", scope={"product_ids": [p["product_id"]], "labels": [p["title"]]},
            title=(f"{p['title']} is out of stock" if not p["inventory_units"]
                   else f"{p['title']} has ~{p['days_of_cover']:.0f} days of stock"),
            problem=(f"Tracked inventory is zero while the product sells {p['units'] / ctx.days:.1f} units a day"
                     if not p["inventory_units"] else
                     f"At the current sales rate, tracked inventory ({p['inventory_units']} units) covers about "
                     f"{p['days_of_cover']:.0f} days") + f". It is {pct(p['share_of_net'])} of net sales.",
            proposed_action="Reorder now or slow demand (pause ads/promotions on this product) until stock arrives.",
            economic_rationale="Sales lost to stockouts are rarely recovered; promotions for out-of-stock items "
                               "waste spend.",
            evidence=[Evidence("Units sold per day", round(p["units"] / ctx.days, 1), ctx.src),
                      Evidence("Tracked inventory", p["inventory_units"], "catalogue (last sync)"),
                      Evidence("Share of net sales", pct(p["share_of_net"]), ctx.src)],
            estimated_impact={"metric": "net sales at risk (next 30 days)", "low": None,
                              "base": round(daily * gap), "high": None, "unit": ctx.cur,
                              "basis": "Days in the next 30 without stock × current daily net sales. Exposure only."},
            assumptions=["Sales continue at the period's average daily rate.", "No inbound stock is recorded here."],
            confidence="medium", confidence_reason="Run-rate and stock are observed; future demand is not.",
            risks=["Inventory counts may be stale between syncs."], risk_level="low", complexity="low",
            success_metrics=["Days of cover > lead time"], next_action="Check the supplier lead time and reorder.",
            frameworks=["inventory_availability"], impact_hint=daily * gap * 12 * 0.4))
    return out[:3]


def price_increase(ctx: Context) -> list[RecDraft]:
    out = []
    for p in ctx.products["products"][:25]:
        if (p["share_of_net"] or 0) < 0.02 or p["units"] < 30 or p.get("gross_margin_pct") is None:
            continue
        if (p["discounted_unit_share"] or 0) > 0.25 or (p["growth"] is not None and p["growth"] < -0.05):
            continue
        if (p["return_rate_units"] or 0) > 0.08 or p["price_changes_in_period"]:
            continue
        price = p["price_max"] or p["avg_selling_price"]
        if not price:
            continue
        new_price = nice_price(price * 1.06, like=price)
        unit_cost = price * (1 - p["gross_margin_pct"])
        sim = S.price_change(current_price=price, unit_cost=unit_cost, units=p["units"], new_price=new_price,
                             volume_changes=(-0.08, -0.03, 0.0))
        rows = {s["volume_change"]: s["gross_profit_change"] for s in sim["scenarios"]}
        out.append(RecDraft(
            detector="price_increase", key=f"p{p['product_id']}", strategy_type=StrategyType.PRICING,
            scope_type="product", scope={"product_ids": [p["product_id"]], "labels": [p["title"]]},
            title=f"Test a higher price on {p['title']} ({ctx.cur} {price:g} → {new_price:g})",
            problem=f"{p['title']} sells steadily with little discounting ({pct(p['discounted_unit_share'])} of units "
                    f"discounted) and a {pct(p['gross_margin_pct'])} margin — signs of price headroom that have not "
                    "been tested.",
            proposed_action=f"Run a controlled price test at {ctx.cur} {new_price:g} (+6%).",
            economic_rationale=f"At this margin the price rise stays profitable unless unit volume falls by more "
                               f"than {pct(-(sim['break_even_volume_change'] or 0))}.",
            evidence=[Evidence("Units sold", p["units"], ctx.src),
                      Evidence("Growth vs previous period", pct(p["growth"]), ctx.src),
                      Evidence("Units sold with a discount", pct(p["discounted_unit_share"]), ctx.src),
                      Evidence("Return rate (units)", pct(p["return_rate_units"]), ctx.src),
                      Evidence("Gross margin", pct(p["gross_margin_pct"]), "derived from unit costs"),
                      Evidence("Break-even volume change", pct(sim["break_even_volume_change"]), "derived")],
            estimated_impact={"metric": "gross profit / year", "low": round((rows[-0.08] or 0) * ctx.annualize),
                              "base": round((rows[-0.03] or 0) * ctx.annualize),
                              "high": round((rows[0.0] or 0) * ctx.annualize), "unit": ctx.cur,
                              "basis": "Scenarios where unit volume falls 8% / 3% / 0% after the price change."},
            assumptions=["Volume responses are scenarios; price elasticity has not been measured for this product.",
                         "Unit cost is constant."],
            confidence="low", confidence_reason="Signals of headroom are observed; demand response is unknown "
                                                "until tested.",
            risks=["Lower conversion if competitors are cheaper.", "Price changes visible to returning customers."],
            risk_level="medium", complexity="medium",
            recommended_test="Shopify has no native price A/B test: use a market split (raise the price in one "
                             "market only) or alternate weeks for 4+ weeks; compare contribution per session.",
            success_metrics=["Gross profit per session for the product", "Unit conversion rate", "Return rate"],
            next_action="Create the experiment, then prepare the price change for approval.",
            frameworks=["price_break_even", "anchoring_decoy"],
            impact_hint=max(0, (rows[-0.03] or 0) * ctx.annualize)))
    return sorted(out, key=lambda r: -(r.impact_hint or 0))[:3]


def variant_signals(ctx: Context) -> list[RecDraft]:
    out = []
    dead = []
    for prod in ctx.products["variants"]:
        if prod["units"] < 30:
            continue
        expected = prod["expected_share_if_even"]
        vs = prod["variants"]
        non_size = [v for v in vs if v["title"] and not all(SIZE_RE.match(part.strip())
                                                              for part in v["title"].split("/"))]
        top = vs[0]
        if non_size and prod["uniform_price"] and top in non_size and top["unit_share"] >= max(0.4, 2 * expected):
            out.append(RecDraft(
                detector="variant_winner", key=f"p{prod['product_id']}", strategy_type=StrategyType.PRODUCT_EXPANSION,
                scope_type="variant", scope={"product_ids": [prod["product_id"]], "variant_ids": [top["variant_id"]],
                                             "labels": [f"{prod['title']} — {top['title']}"]},
                title=f"Expand on the winning option: {prod['title']} — {top['title']}",
                problem=f"'{top['title']}' takes {pct(top['unit_share'])} of units for {prod['title']} (an even split "
                        f"would be {pct(expected)}), at the same price as the other options.",
                proposed_action="Develop 1–2 adjacent options close to the winner (similar colour/style) and test a "
                                "limited-edition or premium version of the winner at a higher price.",
                economic_rationale="Concentrated option demand signals a preference the catalogue under-serves; "
                                   "new options of a proven product are lower-risk than new products.",
                evidence=[Evidence("Winning option unit share", pct(top["unit_share"]), ctx.src),
                          Evidence("Even-split share", pct(expected), "derived"),
                          Evidence("Units of product", prod["units"], ctx.src)],
                assumptions=["Option preference is not caused by stockouts of the others."],
                missing_data=["Supplier cost and minimum order for new options"],
                confidence="low", confidence_reason="Preference is observed; demand for new options is a hypothesis.",
                risks=["Inventory investment in options that do not sell."], risk_level="medium", complexity="high",
                recommended_test="Pre-launch a small batch or a waitlist for the new option and measure sign-ups "
                                 "before committing inventory.",
                success_metrics=["Units of new option in first 30 days", "Cannibalisation of existing options"],
                next_action="Shortlist adjacent options with the supplier.", frameworks=["anchoring_decoy"],
                impact_hint=0.005 * ctx.summary["kpis"]["net_sales"] * ctx.annualize))
        for v in vs:
            if v["inventory"] and v["inventory"] >= 30 and v["unit_share"] < 0.25 * expected:
                dead.append((prod, v))
    if dead:
        labels = [f"{p['title']} — {v['title']} ({v['inventory']} in stock)" for p, v in dead[:8]]
        out.append(RecDraft(
            detector="slow_variants", key="store", strategy_type=StrategyType.MERCHANDISING, scope_type="variant",
            scope={"variant_ids": [v["variant_id"] for _, v in dead], "labels": labels},
            title=f"{len(dead)} slow-moving variants hold stock",
            problem="These variants sell at under a quarter of an even share within their product while holding "
                    "30+ units: " + "; ".join(labels[:5]) + ("…" if len(labels) > 5 else ""),
            proposed_action="Stop reordering them; use them as free gifts or bundle add-ons before discounting "
                            "them publicly.",
            economic_rationale="Turning slow stock into a perceived-value gift recovers cash without training "
                               "customers to expect discounts on core products.",
            evidence=[Evidence("Slow variants", len(dead), ctx.src)],
            assumptions=["Low share is not explained by recent restocks."],
            missing_data=[], confidence="medium", confidence_reason="Sales share and stock are observed.",
            risks=["Some variants may be new or seasonal."], risk_level="low", complexity="low",
            success_metrics=["Units cleared", "Inventory value at cost"],
            next_action="Review the list in Products → Variants.", frameworks=["offer_stacking"],
            impact_hint=0.003 * ctx.summary["kpis"]["net_sales"] * ctx.annualize))
    return out


def bundles(ctx: Context) -> list[RecDraft]:
    prods = {p["product_id"]: p for p in ctx.products["products"]}
    out = []
    for pair in sorted(ctx.copurchase["pairs"], key=lambda p: -(p["lift"] * p["orders_together"]))[:6]:
        if pair["lift"] < 1.5:
            continue
        a, b = prods.get(pair["a"]), prods.get(pair["b"])
        if not a or not b:
            continue
        if (b["avg_selling_price"] or 0) > (a["avg_selling_price"] or 0):
            a, b = b, a  # anchor on the higher-priced item, attach the cheaper one
        ma, mb = a.get("gross_margin_pct"), b.get("gross_margin_pct")
        price_a, price_b = a["avg_selling_price"], b["avg_selling_price"]
        if not price_a or not price_b:
            continue
        cost_a = price_a * (1 - ma) if ma is not None else None
        cost_b = price_b * (1 - mb) if mb is not None else None
        with_both = pair["orders_together"]
        sim = S.bundle(price_a=price_a, price_b=price_b, cost_a=cost_a, cost_b=cost_b, orders_with_a=a["orders"],
                       orders_with_both=with_both, bundle_discount_pct=0.10)
        discounted_ok = sim["break_even_feasible"] and (sim["break_even_attach_uplift"] or 1) <= 0.08
        a_only = sim["orders_with_a_only"]
        impact = None
        if mb is not None:
            vals = [a_only * k * price_b * mb * ctx.annualize for k in (0.02, 0.05, 0.08)]
            impact = {"metric": "gross profit / year", "low": round(vals[0]), "base": round(vals[1]),
                      "high": round(vals[2]), "unit": ctx.cur,
                      "basis": f"Full-price cross-sell: 2% / 5% / 8% more of {a['title']} orders add {b['title']}."}
        if discounted_ok:
            action = (f"Offer a '{a['title']} + {b['title']}' bundle at 10% off "
                      f"({ctx.cur} {sim['bundle_price']:g}) as an add-on on the {a['title']} page.")
        else:
            action = (f"Recommend {b['title']} on the {a['title']} page and in the cart at full price ('complete the "
                      f"look'). A 10% bundle would need {pct(sim['break_even_attach_uplift'])} more orders to attach "
                      f"just to pay for the discount given to the {with_both} orders that already buy both.")
        out.append(RecDraft(
            detector="bundle", key=f"{pair['a']}-{pair['b']}", strategy_type=StrategyType.BUNDLE if discounted_ok
            else StrategyType.CROSS_SELL, scope_type="product",
            scope={"product_ids": [a["product_id"], b["product_id"]], "labels": [a["title"], b["title"]]},
            title=f"{'Bundle' if discounted_ok else 'Cross-sell'} {b['title']} with {a['title']}",
            problem=f"{a['title']} and {b['title']} are bought together {pair['lift']:.1f}× more often than chance "
                    f"({with_both} orders), yet {a_only} {a['title']} orders did not include {b['title']}.",
            proposed_action=action,
            economic_rationale="Complementary products already chosen together are the most credible add-on; "
                               "whether to discount depends on how much the discount would give away to existing "
                               "pair buyers.",
            evidence=[Evidence("Orders with both", with_both, ctx.src),
                      Evidence("Lift", pair["lift"], "derived"),
                      Evidence(f"Share of {a['title']} orders with {b['title']}", pct(sim["current_attach_rate"]),
                               ctx.src),
                      Evidence("Break-even attach uplift for a 10% bundle", pct(sim["break_even_attach_uplift"]),
                               "derived")],
            estimated_impact=impact,
            assumptions=["Attach-rate scenarios are assumptions to be tested.", *sim["assumptions"][:2]],
            missing_data=[] if mb is not None else [f"Unit cost of {b['title']}"],
            confidence="medium" if pair["orders_together"] >= 20 and mb is not None else "low",
            confidence_reason="Co-purchase is observed; the effect of promoting it is not.",
            risks=["Too many recommendations dilute the product page."], risk_level="low", complexity="low",
            recommended_test=f"Show the add-on to a random half of {a['title']} visitors for 3 weeks.",
            success_metrics=["Attach rate", "AOV of orders containing the anchor", "Contribution per visitor"],
            next_action="Set up the add-on module and create the experiment.",
            frameworks=["bundling", "offer_stacking"], impact_hint=(impact or {}).get("base") or 0))
    return out[:3]


def threshold_gift(ctx: Context) -> list[RecDraft]:
    b = ctx.basket
    out = []
    if b["orders"] < 200:
        return out
    gift = (ctx.gifts.get("candidates") or [None])[0]
    gift_cost = gift["unit_cost"] if gift else (ctx.store.commercial_settings or {}).get("default_gift_cost")
    fs = b.get("free_shipping_threshold")
    current = next((c for c in b["threshold_candidates"] if c["is_current_free_shipping"]), None)
    if current and current["share_above"] is not None:
        if current["share_above"] >= 0.6:
            ship = (ctx.store.commercial_settings or {}).get("avg_shipping_cost_per_order")
            subsidy = ship * current["share_above"] * b["orders"] * ctx.annualize if ship else None
            out.append(RecDraft(
                detector="free_shipping_threshold", key="too_low", strategy_type=StrategyType.FREE_SHIPPING,
                scope_type="store", title=f"Free-shipping threshold ({ctx.cur} {fs:g}) is below most baskets",
                problem=f"{pct(current['share_above'])} of orders already exceed the free-shipping threshold, so it "
                        "rarely changes behaviour while the store pays shipping on most orders.",
                proposed_action="Test a higher threshold near the 70th–80th percentile of order value, or keep the "
                                "threshold and add a gift tier above it.",
                economic_rationale="A threshold motivates only orders just below it; when most orders clear it, it "
                                   "is a shipping subsidy rather than an AOV lever.",
                evidence=[Evidence("Orders above threshold", pct(current["share_above"]), ctx.src),
                          Evidence("Orders within 25% below", pct(current["share_reachable"]), ctx.src)],
                estimated_impact={"metric": "shipping cost absorbed / year", "low": None,
                                  "base": round(subsidy) if subsidy else None, "high": None, "unit": ctx.cur,
                                  "basis": "Exposure: qualifying orders × average shipping cost. Not a saving "
                                           "forecast."} if subsidy else None,
                missing_data=[] if ship else ["Average shipping cost per order"],
                confidence="medium", confidence_reason="Basket distribution is observed.",
                risks=["Raising the threshold can lower conversion for mid-size baskets."], risk_level="medium",
                complexity="low", recommended_test="Raise the threshold in one market or for 50% of sessions "
                                                   "(theme flag) for 3–4 weeks.",
                success_metrics=["AOV", "Conversion rate", "Contribution per session"],
                next_action="Run the threshold simulator in Basket Growth.", frameworks=["zero_price", "goal_gradient"],
                impact_hint=(subsidy or 0) * 0.2))
        elif current["share_above"] <= 0.10:
            out.append(RecDraft(
                detector="free_shipping_threshold", key="too_high", strategy_type=StrategyType.FREE_SHIPPING,
                scope_type="store", title=f"Free-shipping threshold ({ctx.cur} {fs:g}) is out of reach for most",
                problem=f"Only {pct(current['share_above'])} of orders reach the threshold; it is unlikely to "
                        "motivate customers who are far below it.",
                proposed_action="Test a threshold closer to the 60th percentile of order value.",
                economic_rationale="Thresholds work through proximity (goal-gradient); distant goals do not pull.",
                evidence=[Evidence("Orders above threshold", pct(current["share_above"]), ctx.src)],
                confidence="medium", confidence_reason="Basket distribution is observed.",
                risks=["Lower threshold increases shipping subsidy."], risk_level="medium", complexity="low",
                success_metrics=["AOV", "Share of orders reaching threshold"],
                next_action="Run the threshold simulator in Basket Growth.", frameworks=["goal_gradient"],
                impact_hint=0.005 * ctx.summary["kpis"]["net_sales"] * ctx.annualize))
    if gift_cost is None:
        return out
    from app.analytics.basket_analysis import simulate_threshold

    best = None
    for c in b["threshold_candidates"]:
        if c["is_current_free_shipping"] or (c["share_reachable"] or 0) < 0.12:
            continue
        sim = simulate_threshold(ctx.db, ctx.store, ctx.start, ctx.end, threshold=c["threshold"],
                                 incentive_cost=float(gift_cost))
        # Require the 20% "base" scenario to be profitable: break-even must sit well below it.
        if sim["break_even_mover_rate"] is None or sim["break_even_mover_rate"] > 0.15:
            continue
        if best is None or sim["break_even_mover_rate"] < best["break_even_mover_rate"]:
            best = sim
    if best is None:
        return out
    sc = {s["mover_rate"]: s["net_effect"] for s in best["scenarios"]}
    gift_name = f"{gift['title']}" if gift else "a low-cost gift"
    out.append(RecDraft(
        detector="threshold_gift", key=f"t{best['threshold']:g}", strategy_type=StrategyType.FREE_GIFT,
        scope_type="store",
        title=f"Free {gift_name} on orders over {ctx.cur} {best['threshold']:g}",
        problem=f"{pct(best['share_reachable'])} of orders sit within 25% below {ctx.cur} {best['threshold']:g} "
                f"(average gap {ctx.money(best['avg_gap_reachable'])}).",
        proposed_action=f"Offer {gift_name} (unit cost {ctx.cur} {gift_cost:g}) automatically above "
                        f"{ctx.cur} {best['threshold']:g}, with a cart progress bar showing the remaining amount.",
        economic_rationale=f"The gift pays for itself if {pct(best['break_even_mover_rate'])} of near-threshold "
                           f"orders top up — including the cost of gifting the {best['orders_above']} orders that "
                           "already qualify. A gift is valued above its cost, unlike a discount of equal cost.",
        evidence=[Evidence("Orders within reach", best["orders_reachable"], ctx.src),
                  Evidence("Orders already above", best["orders_above"], ctx.src),
                  Evidence("Break-even top-up rate", pct(best["break_even_mover_rate"]), "derived"),
                  Evidence("Margin used", pct(ctx.basket.get("margin_pct")), ctx.basket.get("margin_basis"))],
        estimated_impact={"metric": "net gross-profit effect / year", "low": _ann(sc.get(0.10), ctx),
                          "base": _ann(sc.get(0.20), ctx), "high": _ann(sc.get(0.35), ctx), "unit": ctx.cur,
                          "basis": "Scenarios where 10% / 20% / 35% of near-threshold orders top up; includes "
                                   "deadweight gift cost."},
        assumptions=best["assumptions"],
        missing_data=[] if gift else ["A specific gift product with a recorded unit cost"],
        confidence="medium" if gift else "low",
        confidence_reason="Basket distribution and costs are observed; the top-up rate is an assumption.",
        risks=["Gift stock-outs mid-campaign.", "Customers may return the top-up item."],
        risk_level="low", complexity="medium",
        recommended_test="Run the gift for a random half of sessions (theme flag or app) for 3–4 weeks; compare "
                         "AOV, conversion and contribution per session.",
        success_metrics=["Share of orders ≥ threshold", "AOV", "Contribution per session", "Conversion rate"],
        next_action="Simulate alternatives in Basket Growth, then create the experiment.",
        frameworks=["zero_price", "goal_gradient", "offer_stacking"],
        impact_hint=max(0, _ann(sc.get(0.20), ctx) or 0)))
    return out


def _ann(v, ctx):
    return None if v is None else round(v * ctx.annualize)


def quantity_incentive(ctx: Context) -> list[RecDraft]:
    b = ctx.basket
    if b["orders"] < 200 or (b["single_unit_share"] or 0) < 0.65 or b.get("margin_pct") is None:
        return []
    from app.analytics.basket_analysis import simulate_quantity

    sim = simulate_quantity(ctx.db, ctx.store, ctx.start, ctx.end, min_units=2, discount_pct=0.10)
    if not sim["break_even_feasible"] or sim["break_even_mover_rate"] > 0.25:
        return []
    sc = {s["mover_rate"]: s["net_effect"] for s in sim["scenarios"]}
    return [RecDraft(
        detector="quantity_incentive", key="buy2", strategy_type=StrategyType.QUANTITY_INCENTIVE, scope_type="store",
        title="Test 'buy 2, save 10%'",
        problem=f"{pct(b['single_unit_share'])} of orders contain a single unit.",
        proposed_action="Offer 10% off orders with 2+ items (automatic discount), promoted on product pages and cart.",
        economic_rationale=f"Breaks even if {pct(sim['break_even_mover_rate'])} of single-unit orders add a second "
                           f"item, after giving 10% to the {sim['orders_qualifying_now']} orders that already have 2+.",
        evidence=[Evidence("Single-unit orders", pct(b["single_unit_share"]), ctx.src),
                  Evidence("Average unit price", ctx.money(sim["avg_unit_price"]), ctx.src),
                  Evidence("Break-even mover rate", pct(sim["break_even_mover_rate"]), "derived")],
        estimated_impact={"metric": "net gross-profit effect / year", "low": _ann(sc.get(0.10), ctx),
                          "base": _ann(sc.get(0.20), ctx), "high": _ann(sc.get(0.35), ctx), "unit": ctx.cur,
                          "basis": "10% / 20% / 35% of single-unit orders add a unit; includes deadweight discount."},
        assumptions=sim["assumptions"], confidence="low",
        confidence_reason="Unit mix is observed; customer response is an assumption.",
        risks=["Trains customers to expect a multi-buy discount.", "Higher returns on second items."],
        risk_level="medium", complexity="low",
        recommended_test="Alternate weeks or a market split for 4 weeks; compare units per order and contribution.",
        success_metrics=["Units per order", "Contribution per order", "Return rate"],
        next_action="Compare with a gift-at-threshold alternative in Basket Growth.",
        frameworks=["goal_gradient"], impact_hint=max(0, _ann(sc.get(0.20), ctx) or 0))]


def discount_dependency(ctx: Context) -> list[RecDraft]:
    d = ctx.summary["discounts"]
    k = ctx.summary["kpis"]
    share, rate = k["discounted_order_share"] or 0, k["discount_rate"] or 0
    prev = ctx.summary["previous"]["change"].get("discount_rate")
    if share < 0.30 and rate < 0.10 and not (prev and prev > 0.25 and rate > 0.06):
        return []
    annual_disc = k["discounts"] * ctx.annualize
    margin_note = k.get("gross_margin_pct")
    evergreen = [a for a in ctx.alerts if a["kind"] == "evergreen_code"]
    ev = [Evidence("Orders with a discount", pct(share), ctx.src),
          Evidence("Discounts as % of gross sales", pct(rate), ctx.src),
          Evidence("AOV discounted vs full price", f"{ctx.money(d['aov_discounted'])} vs {ctx.money(d['aov_full_price'])}",
                   ctx.src)]
    if d["top_codes"]:
        top = d["top_codes"][0]
        ev.append(Evidence(f"Most used code: {top['code']}", f"{top['orders']} orders", ctx.src))
    if evergreen:
        ev.append(Evidence("Evergreen deep codes active", ", ".join(a["title"].split()[1] for a in evergreen[:3]),
                           "synced discounts"))
    return [RecDraft(
        detector="discount_dependency", key="store", strategy_type=StrategyType.DISCOUNT_REDUCTION, scope_type="store",
        title="Reduce reliance on discount codes",
        problem=f"{pct(share)} of orders use a discount, giving away {pct(rate)} of gross sales "
                f"({ctx.money(k['discounts'])} this period).",
        proposed_action="Replace blanket percentage codes with conditional value (gift above a threshold, bundles) "
                        "and run a holdout: send the next email campaign without a code to a random 50% of the list.",
        economic_rationale="Some discounted orders would have happened at full price; that share of the discount is "
                           "pure margin loss. Frequent discounts also lower the price customers expect to pay.",
        evidence=ev,
        estimated_impact={"metric": "discount recoverable / year", "low": round(annual_disc * 0.15),
                          "base": round(annual_disc * 0.30), "high": round(annual_disc * 0.50), "unit": ctx.cur,
                          "basis": "If 15% / 30% / 50% of discounted orders were non-incremental and would convert "
                                   "without the code. Only a holdout test can tell which applies."},
        assumptions=["The non-incremental share is unknown and is shown as scenarios."],
        missing_data=[] if margin_note is not None else ["Unit costs (to compare against margin)"],
        confidence="medium", confidence_reason="Discount exposure is measured; how much of it is incremental is not.",
        risks=["Short-term order volume may dip during the transition.",
               "Customers trained on codes may wait for the next promotion."],
        risk_level="medium", complexity="medium",
        recommended_test="Email holdout: 50% receive the code, 50% the same email without it; compare revenue and "
                         "contribution per recipient over 14 days.",
        success_metrics=["Contribution per recipient", "Discount rate", "Full-price order share"],
        next_action="Create the holdout experiment for the next campaign.",
        frameworks=["promotion_expectations", "offer_stacking", "controlled_experiments"],
        impact_hint=annual_disc * 0.3)]


def retention(ctx: Context) -> list[RecDraft]:
    c = ctx.summary["customers"]
    if not c.get("available"):
        return []
    rep = c["repeat_90d"]
    if rep["cohort_customers"] < 200 or rep["rate"] is None or rep["rate"] >= 0.20:
        return []
    k = ctx.summary["kpis"]
    margin = k.get("gross_margin_pct")
    ret_aov = c.get("returning_customer_aov") or k["aov"]
    cohort_year = rep["cohort_customers"] * 365 / max(1, ctx.days - 90)
    impact = None
    if margin is not None and ret_aov:
        vals = [cohort_year * pp * ret_aov * margin for pp in (0.01, 0.02, 0.04)]
        impact = {"metric": "gross profit / year", "low": round(vals[0]), "base": round(vals[1]),
                  "high": round(vals[2]), "unit": ctx.cur,
                  "basis": "If the 90-day repeat rate rises by 1 / 2 / 4 points for new customers acquired at the "
                           "current pace, each extra repeat order at the returning-customer AOV and current margin."}
    return [RecDraft(
        detector="retention", key="store", strategy_type=StrategyType.RETENTION, scope_type="store",
        title="Raise second-order rate with a post-purchase programme",
        problem=f"Only {pct(rep['rate'])} of new customers order again within 90 days "
                f"({rep['repeat_customers']} of {rep['cohort_customers']}).",
        proposed_action="Build a post-purchase sequence: care/usage content, a complementary-product recommendation "
                        "based on the first purchase, and a time-limited gift (not a discount) on the second order.",
        economic_rationale="Repeat orders carry no acquisition cost; small changes in second-order rate compound.",
        evidence=[Evidence("90-day repeat rate", pct(rep["rate"]), ctx.src),
                  Evidence("Returning-customer AOV", ctx.money(c.get("returning_customer_aov")), ctx.src),
                  Evidence("New-customer AOV", ctx.money(c.get("new_customer_aov")), ctx.src)],
        estimated_impact=impact, assumptions=["Repeat-rate uplifts are scenarios."],
        missing_data=[] if margin is not None else ["Unit costs"],
        confidence="low", confidence_reason="Repeat rate is measured; uplift from a programme is not.",
        risks=["Email fatigue if sequences are too frequent."], risk_level="low", complexity="medium",
        recommended_test="Randomly hold out 20% of new customers from the sequence for 90 days; compare repeat rate.",
        success_metrics=["90-day repeat rate", "Revenue per customer at 90 days"],
        next_action="Draft the sequence in the email platform; create the holdout experiment.",
        frameworks=["retention_economics", "controlled_experiments"], impact_hint=(impact or {}).get("base") or 0)]


def conversion_drop(ctx: Context) -> list[RecDraft]:
    k = ctx.summary["kpis"]
    prev = ctx.summary["previous"]["kpis"]
    ch = ctx.summary["previous"]["change"].get("conversion_rate")
    if ch is None or ch > -0.15 or not k.get("sessions") or not prev.get("sessions"):
        return []
    lost_orders = k["sessions"] * (prev["conversion_rate"] - k["conversion_rate"])
    margin = k.get("gross_margin_pct")
    exposure = lost_orders * (k["aov"] or 0) * ctx.annualize
    return [RecDraft(
        detector="conversion_drop", key="store", strategy_type=StrategyType.CONVERSION, scope_type="store",
        title=f"Conversion rate fell {pct(-ch, 0)}",
        problem=f"Conversion went from {pct(prev['conversion_rate'], 2)} to {pct(k['conversion_rate'], 2)} while "
                f"sessions moved {pct(ctx.summary['previous']['change'].get('sessions'))}.",
        proposed_action="Check traffic mix first (new paid campaigns lower average conversion), then site changes, "
                        "stock of top products and checkout errors in the period.",
        economic_rationale="A conversion drop on stable traffic is lost demand already paid for.",
        evidence=[Evidence("Conversion this period", pct(k["conversion_rate"], 2), "traffic import + orders"),
                  Evidence("Conversion previous period", pct(prev["conversion_rate"], 2), "traffic import + orders")],
        estimated_impact={"metric": "net sales at stake / year", "low": None, "base": round(exposure), "high": None,
                          "unit": ctx.cur, "basis": "Exposure: orders lost at the previous conversion rate × AOV."},
        confidence="medium", confidence_reason="Both rates are measured; the cause is not.",
        risks=["Traffic-mix changes can explain the drop without any site problem."], risk_level="low",
        complexity="medium", success_metrics=["Conversion rate by traffic source"],
        next_action="Break conversion down by source and landing page in your analytics tool.",
        impact_hint=exposure * (margin or 0.4) * 0.3)]


def returns_hotspots(ctx: Context) -> list[RecDraft]:
    prods = [p for p in ctx.products["products"] if p["units"] >= 30 and p["return_rate_units"] is not None]
    if not prods:
        return []
    total_units = sum(p["units"] for p in prods)
    avg = sum(p["return_rate_units"] * p["units"] for p in prods) / total_units
    out = []
    for p in prods:
        if p["return_rate_units"] >= max(2 * avg, 0.08):
            refunded = p["net_sales"] * p["return_rate_units"]
            out.append(RecDraft(
                detector="returns_hotspot", key=f"p{p['product_id']}", strategy_type=StrategyType.CONVERSION,
                scope_type="product", scope={"product_ids": [p["product_id"]], "labels": [p["title"]]},
                title=f"High returns on {p['title']} ({pct(p['return_rate_units'])} of units)",
                problem=f"Units of {p['title']} are returned at {pct(p['return_rate_units'])}, against a store "
                        f"average of {pct(avg)}.",
                proposed_action="Read the return reasons; improve the size guide, fit notes and photos; if a single "
                                "variant drives returns, fix or drop it.",
                economic_rationale="Returns cost the sale, the shipping both ways and handling; fixing information "
                                   "gaps is cheaper than any promotion.",
                evidence=[Evidence("Return rate (units)", pct(p["return_rate_units"]), ctx.src),
                          Evidence("Store average return rate", pct(avg), ctx.src)],
                estimated_impact={"metric": "net sales refunded / year", "low": None,
                                  "base": round(refunded * ctx.annualize), "high": None, "unit": ctx.cur,
                                  "basis": "Exposure: refunded merchandise value annualised."},
                confidence="medium", confidence_reason="Return rate is measured; causes need return-reason data.",
                missing_data=["Return reasons"], risks=[], risk_level="low", complexity="low",
                success_metrics=["Return rate (units)"], next_action="Export return reasons for this product.",
                impact_hint=refunded * ctx.annualize * 0.3))
    return out[:2]


def promo_history(ctx: Context) -> list[RecDraft]:
    by = ctx.history.get("by", {})
    disp = {r["key"]: r for r in by.get("display", [])}
    email, site = disp.get("email_only"), disp.get("full_site")
    if not email or not site or email["events"] < 5 or site["events"] < 5:
        return []
    if email["median_offer_order_share"] >= 0.03:
        return []
    return [RecDraft(
        detector="promo_history", key="email_only", strategy_type=StrategyType.PROMOTION, scope_type="store",
        title="Email-only discount codes rarely move orders",
        problem=f"Across {email['events']} documented email-only events, the median share of orders using the code "
                f"was {pct(email['median_offer_order_share'])}, versus {pct(site['median_offer_order_share'])} for "
                f"{site['events']} site-wide events.",
        proposed_action="Stop attaching discount codes to calendar 'awareness day' emails; use content or a small "
                        "gift instead, and reserve codes for a few planned peaks. Validate with a holdout.",
        economic_rationale="Codes with tiny reach still leak to coupon sites and train subscribers to wait; "
                           "removing them costs little if they drive few orders.",
        evidence=[Evidence("Median order share — email-only", pct(email["median_offer_order_share"]),
                           "offers workbook (documented, unverified)"),
                  Evidence("Median order share — full site", pct(site["median_offer_order_share"]),
                           "offers workbook (documented, unverified)")],
        assumptions=["Recorded results in the workbook are accurate (several rows have flagged data issues)."],
        confidence="low", confidence_reason="Based on documented, unverified historical records; reach differs by "
                                            "design between email and site-wide offers.",
        risks=["Some email codes may serve list-growth goals not captured here."], risk_level="low",
        complexity="low", recommended_test="Holdout on the next two email-only events.",
        success_metrics=["Revenue per recipient", "Discount rate"],
        next_action="Review upcoming email-only events in the Offers calendar.",
        frameworks=["promotion_expectations", "controlled_experiments"],
        impact_hint=0.002 * ctx.summary["kpis"]["net_sales"] * ctx.annualize)]


def promo_without_lift(ctx: Context) -> list[RecDraft]:
    perf = ctx.extra.get("offer_performance") or {}
    weak = []
    for ev in perf.get("events", []):
        m = ev.get("measured")
        if not m or not m.get("change") or ev["status"] == "live":
            continue
        ch = m["change"].get("net_sales_per_day")
        given = m.get("offer_discounts") or m["during"]["discounts"]
        if ch is not None and ch <= 0.02 and given and given > 0 and ev["days"] >= 2:
            weak.append((ev, ch, given))
    if not weak:
        return []
    total = sum(g for _, _, g in weak)
    names = [f"{ev['name']} ({ev['start']}, {pct(ch, 0)} sales/day, {ctx.money(g)} discounts)" for ev, ch, g in weak[:5]]
    return [RecDraft(
        detector="promo_without_lift", key="store", strategy_type=StrategyType.PROMOTION, scope_type="offer",
        scope={"event_ids": [ev["event_id"] for ev, _, _ in weak], "labels": [ev["name"] for ev, _, _ in weak]},
        title=f"{len(weak)} promotion(s) gave discounts without lifting daily sales",
        problem="Net sales per day did not rise against the period just before these offers: " + "; ".join(names) + ".",
        proposed_action="Do not repeat these mechanics as they were. For the next equivalent date, test a "
                        "non-discount alternative (gift above a threshold, bundle, early access) or hold out half "
                        "of the audience to see what the discount actually adds.",
        economic_rationale="A discount that does not change demand transfers margin to customers who would have "
                           "bought anyway.",
        evidence=[Evidence("Offers without a lift", len(weak), ctx.src, "before/after"),
                  Evidence("Discounts given during them", ctx.money(total), ctx.src)],
        estimated_impact={"metric": "discounts given in these offers", "low": None, "base": round(total), "high": None,
                          "unit": ctx.cur, "basis": "Exposure over the last 180 days, not a saving forecast."},
        assumptions=["The period before each offer is a fair comparison (seasonality is not removed)."],
        confidence="low", confidence_reason="Before/after comparison: seasonality, traffic and other campaigns can "
                                            "hide or fake a lift.",
        risks=["Some offers serve goals other than sales (list growth, clearing stock)."], risk_level="low",
        complexity="low", recommended_test="Holdout: send the next equivalent offer to a random half of the audience.",
        success_metrics=["Net sales per day vs holdout", "Discount rate", "Contribution per recipient"],
        next_action="Open Offer analysis to review each event.",
        frameworks=["promotion_expectations", "controlled_experiments", "offer_stacking"],
        impact_hint=total * ctx.annualize / 2 * 0.5)]


DETECTORS = [data_gaps, hero_concentration, declining_products, stockout_risk, price_increase, variant_signals,
             bundles, threshold_gift, quantity_incentive, discount_dependency, retention, conversion_drop,
             returns_hotspots, promo_history, promo_without_lift]
