import pytest

from app.analytics import simulations as S


def test_threshold_incentive_economics():
    # 10 orders: 3 already above 100, 4 within 25% below (75–99), 3 far below
    values = [120, 150, 110, 80, 85, 90, 95, 40, 30, 50]
    r = S.threshold_incentive(values, 100, incentive_cost=10, margin_pct=0.5, overshoot_pct=0.0,
                              mover_rates=[0.5])
    assert r["orders_above"] == 3 and r["orders_reachable"] == 4
    sc = r["scenarios"][0]
    # top-ups: 20+15+10+5 = 50; half move -> 25 revenue, 12.5 GP; cost (3 + 2) × 10 = 50
    assert sc["incremental_revenue"] == 25 and sc["incremental_gross_profit"] == 12.5
    assert sc["incentive_cost"] == 50 and sc["net_effect"] == -37.5
    # break-even: 3×10 / (50×0.5 − 4×10) -> denominator negative -> infeasible
    assert r["break_even_mover_rate"] is None and not r["break_even_feasible"]


def test_threshold_break_even_when_feasible():
    values = [60] * 50 + [120] * 5
    r = S.threshold_incentive(values, 70, incentive_cost=2, margin_pct=0.6, overshoot_pct=0.0)
    # topup 10 each ×50 = 500; denom 500×0.6 − 50×2 = 200; deadweight 5×2 = 10 -> r = 0.05
    assert r["break_even_mover_rate"] == pytest.approx(0.05)
    assert r["break_even_feasible"]


def test_threshold_without_margin_reports_unknown_profit():
    r = S.threshold_incentive([50, 60, 120], 70, incentive_cost=5, margin_pct=None)
    assert all(s["net_effect"] is None for s in r["scenarios"])
    assert any("margin unknown" in a.lower() for a in r["assumptions"])


def test_already_offered_has_no_deadweight():
    r = S.threshold_incentive([50, 60, 120], 70, incentive_cost=5, margin_pct=0.5, already_offered=True)
    assert r["deadweight_cost"] == 0


def test_price_change_break_even():
    r = S.price_change(current_price=100, unit_cost=60, units=100, new_price=110)
    assert r["unit_margin_pct"] == 0.4
    assert r["break_even_volume_change"] == pytest.approx(-0.2)
    flat = next(s for s in r["scenarios"] if s["volume_change"] == 0.0)
    assert flat["gross_profit_change"] == 1000


def test_bundle():
    r = S.bundle(price_a=100, price_b=50, cost_a=40, cost_b=20, orders_with_a=1000, orders_with_both=100,
                 bundle_discount_pct=0.1)
    # GP per mover = 50×0.9 − 100×0.1 − 20 = 15 ; cannibalised = 100 × 150 × 0.1 = 1500
    assert r["gross_profit_per_mover"] == 15 and r["cannibalised_discount"] == 1500
    assert r["break_even_attach_uplift"] == pytest.approx(1500 / (15 * 900), abs=1e-4)


def test_quantity_incentive():
    data = [(1, 50.0)] * 80 + [(2, 100.0)] * 20
    r = S.quantity_incentive(data, min_units=2, discount_pct=0.1, avg_unit_price=50, margin_pct=0.6)
    assert r["orders_qualifying_now"] == 20 and r["orders_reachable"] == 80
    assert r["deadweight_discount"] == 200
    # per mover: 45 − 20 − 5 = 20 ; break-even = 200 / (20 × 80) = 0.125
    assert r["break_even_mover_rate"] == pytest.approx(0.125)


def test_discount_depth():
    r = S.discount_depth(avg_order_value=100, margin_pct=0.6, current_pct=0.2, proposed_pct=0.1,
                         current_redemptions=100)
    assert r["required_redemption_ratio"] == pytest.approx(0.8)


def test_candidate_thresholds_round():
    vals = list(range(20, 220, 2))
    cands = S.candidate_thresholds(vals, existing=99)
    assert 99 in cands and all(c % 5 == 0 or c == 99 for c in cands)
