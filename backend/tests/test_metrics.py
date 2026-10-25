import pytest

from app.analytics import metrics as m


def test_basic_ratios_none_on_missing():
    assert m.aov(1000, 10) == 100
    assert m.aov(1000, 0) is None
    assert m.units_per_order(25, 10) == 2.5
    assert m.discount_rate(150, 1000) == 0.15
    assert m.conversion_rate(30, None) is None
    assert m.conversion_rate(30, 1500) == 0.02


def test_gross_margin():
    assert m.gross_margin_pct(1000, 400) == pytest.approx(0.6)
    assert m.gross_margin_pct(1000, None) is None


def test_contribution_lists_missing_components():
    value, missing = m.contribution(600, 50, 10, shipping_cost_per_order=6, payment_fee_pct=None,
                                    payment_fee_fixed=None, fulfillment_cost_per_order=None, customer_paid=1100)
    assert value == 600 + 50 - 60
    assert "payment processing fees" in missing and "fulfilment cost per order" in missing
    value, missing = m.contribution(600, 50, 10, shipping_cost_per_order=6, payment_fee_pct=0.03,
                                    payment_fee_fixed=0.3, fulfillment_cost_per_order=2, customer_paid=1000)
    assert value == pytest.approx(600 + 50 - 60 - 30 - 3 - 20)
    assert missing == []
    assert m.contribution(None, 50, 10, shipping_cost_per_order=None, payment_fee_pct=None, payment_fee_fixed=None,
                          fulfillment_cost_per_order=None, customer_paid=0)[0] is None


def test_concentration():
    vals = [50, 30, 10, 10]
    assert m.top_n_share(vals, 1) == 0.5
    assert m.hhi(vals) == pytest.approx(0.25 + 0.09 + 0.01 + 0.01)
    assert m.items_for_share(vals, 0.8) == 2
    assert m.hhi([]) is None


def test_break_even_volume():
    assert m.break_even_volume_change(-0.10, 0.40) == pytest.approx(1 / 3)
    assert m.break_even_volume_change(0.10, 0.40) == pytest.approx(-0.2)
    assert m.break_even_volume_change(-0.5, 0.4) is None  # price below cost


def test_discount_break_even():
    # 20% off with 50% margin: need 2/3 more orders to keep gross profit
    assert m.discount_break_even_uplift(0.2, 0.5) == pytest.approx(0.2 / 0.3)
    assert m.discount_break_even_uplift(0.5, 0.4) is None


def test_two_proportion():
    r = m.two_proportion_test(200, 10000, 260, 10000)
    assert r["diff"] == pytest.approx(0.006)
    assert r["p_value"] < 0.01
    r = m.two_proportion_test(20, 1000, 22, 1000)
    assert r["p_value"] > 0.5


def test_sample_size():
    n = m.required_sample_per_arm(0.02, 0.10)
    assert 75000 < n < 85000


def test_percentile():
    assert m.percentile([1, 2, 3, 4], 0.5) == 2.5
    assert m.percentile([], 0.5) is None
