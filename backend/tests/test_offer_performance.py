from datetime import UTC, date, datetime, timedelta

import pytest

from app.models import Order, PromoEvent
from app.offers.performance import discount_analysis, offer_performance
from app.services.demo import reset_demo


def _order(db, store, oid, day, gross, disc=0.0, codes=(), units=1):
    db.add(Order(store_id=store.id, source="shopify", source_id=f"o{oid}", currency="USD",
                 processed_at=datetime.combine(day, datetime.min.time(), tzinfo=UTC) + timedelta(hours=12),
                 gross_sales=gross, total_discounts=disc, subtotal=gross - disc, total_price=gross - disc,
                 units=units, discount_codes=list(codes)))


def test_discount_analysis_splits_and_depth(db, store):
    d = date(2026, 9, 10)
    _order(db, store, 1, d, 100)                          # full price
    _order(db, store, 2, d, 200, 20, ["FALL10"], units=2)  # 10% depth
    _order(db, store, 3, d, 100, 25, ["BIG25"])           # 25% depth
    db.commit()
    r = discount_analysis(db, store, date(2026, 9, 1), date(2026, 9, 30))
    t = r["totals"]
    assert t["orders"] == 3 and t["discounts"] == 45
    assert t["discount_rate"] == pytest.approx(45 / 400, abs=1e-4)
    assert t["discounted_order_share"] == pytest.approx(2 / 3, abs=1e-4)
    split = {s["segment"]: s for s in r["aov_split"]}
    assert split["Full price"]["aov"] == 100 and split["With a discount"]["aov"] == pytest.approx(127.5)
    depth = {b["bucket"]: b["orders"] for b in r["by_depth"]}
    assert depth == {"No discount": 1, "Up to 10%": 1, "10–20%": 0, "20–30%": 1, "Over 30%": 0}
    codes = {c["code"]: c for c in r["by_code"]}
    assert codes["FALL10"]["discount_per_order"] == 20 and codes["FALL10"]["units_per_order"] == 2


def test_offer_performance_before_after(db, store):
    start = date(2026, 9, 15)
    for i in range(7):  # baseline: 2 orders/day at 100
        day = start - timedelta(days=7 - i)
        _order(db, store, f"b{i}a", day, 100)
        _order(db, store, f"b{i}b", day, 100)
    for i in range(7):  # during: still 2 orders/day, one with the code
        day = start + timedelta(days=i)
        _order(db, store, f"d{i}a", day, 100, 10, ["SAVE10"])
        _order(db, store, f"d{i}b", day, 100)
    db.add(PromoEvent(store_id=store.id, name="Save 10", start_date=start, end_date=start + timedelta(days=6),
                      status="ended", coupon_codes=["SAVE10"], offer_type="percentage_discount"))
    db.commit()
    r = offer_performance(db, store, today=date(2026, 10, 1))
    m = r["events"][0]["measured"]
    assert m["during"]["orders_per_day"] == 2 and m["before"]["orders_per_day"] == 2
    assert m["change"]["orders_per_day"] == 0
    assert m["change"]["net_sales_per_day"] < 0  # same volume, discounts given -> net sales per day fell
    assert m["offer_orders"] == 7 and m["offer_order_share"] == 0.5 and m["offer_discounts"] == 70
    assert "before/after" in m["evidence"]


def test_promo_without_lift_detector_on_demo(db):
    from app.models import Recommendation
    from app.strategy.engine import run_store

    store = reset_demo(db, days=420, daily_orders=14)
    res = run_store(db, store)
    assert res["detector_errors"] == []
    rec = db.query(Recommendation).filter(Recommendation.detector == "promo_without_lift").first()
    if rec:  # depends on synthetic data; when present it must be honest about its evidence
        assert rec.confidence == "low" and "Before/after" in rec.confidence_reason
