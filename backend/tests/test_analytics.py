from datetime import UTC, date, datetime, timedelta

import pytest

from app.analytics.basket_analysis import basket_overview, simulate_threshold
from app.analytics.dataset import load_dataset
from app.analytics.product_analysis import co_purchase, product_performance
from app.analytics.store_analysis import kpis, store_summary
from app.models import CostRecord, Order, OrderLine, Product, Store, Variant
from app.services.demo import reset_demo


def _order(db, store, oid, day, lines, *, discounts=0.0, codes=(), refunded=0.0, test=False, cancelled=False):
    gross = sum(q * p for _, _, q, p in lines)
    o = Order(store_id=store.id, source="shopify", source_id=f"o{oid}", processed_at=datetime.combine(
        day, datetime.min.time(), tzinfo=UTC) + timedelta(hours=15), currency="USD", gross_sales=gross,
        total_discounts=discounts, subtotal=gross - discounts, total_price=gross - discounts,
        units=sum(q for *_, q, _ in lines), discount_codes=list(codes), refunded_merchandise=refunded,
        total_refunded=refunded, is_test=test, cancelled_at=datetime.now(UTC) if cancelled else None)
    db.add(o)
    db.flush()
    for i, (pid, vid, q, p) in enumerate(lines):
        db.add(OrderLine(store_id=store.id, order_id=o.id, source_id=f"l{oid}{i}", product_id=pid, variant_id=vid,
                         title=f"P{pid}", quantity=q, original_unit_price=p,
                         total_discount=discounts * (q * p) / gross if gross else 0))
    return o


@pytest.fixture()
def catalog(db, store):
    ps = []
    for i, (price, cost) in enumerate([(100, 40), (50, 10), (20, None)]):
        p = Product(store_id=store.id, source="shopify", source_id=f"p{i}", title=f"P{i}", status="ACTIVE")
        db.add(p)
        db.flush()
        v = Variant(store_id=store.id, product_id=p.id, source="shopify", source_id=f"v{i}", price=price)
        db.add(v)
        db.flush()
        if cost is not None:
            db.add(CostRecord(store_id=store.id, variant_id=v.id, unit_cost=cost, source="csv_import",
                              created_at=datetime.now(UTC)))
        ps.append((p.id, v.id, price))
    db.commit()
    return ps


def test_kpis_exclude_test_and_cancelled_and_compute_margin(db, store, catalog):
    (p0, v0, _), (p1, v1, _), _ = catalog
    day = date(2026, 9, 10)
    _order(db, store, 1, day, [(p0, v0, 1, 100)])
    _order(db, store, 2, day, [(p0, v0, 1, 100), (p1, v1, 2, 50)], discounts=20, codes=["X"])
    _order(db, store, 3, day, [(p0, v0, 5, 100)], test=True)
    _order(db, store, 4, day, [(p0, v0, 5, 100)], cancelled=True)
    db.commit()
    k = kpis(load_dataset(db, store, date(2026, 9, 1), date(2026, 9, 30)))
    assert k["orders"] == 2
    assert k["gross_sales"] == 300 and k["net_sales"] == 280 and k["aov"] == 140
    assert k["units_per_order"] == 2
    assert k["discount_rate"] == pytest.approx(20 / 300, abs=1e-4)
    assert k["coupon_order_share"] == 0.5
    # COGS = 40 + 40 + 2×10 = 100 ; gross profit = 280 − 100
    assert k["cogs"] == 100 and k["gross_profit"] == 180
    assert k["conversion_rate"] is None  # no traffic data
    assert "unit costs (COGS)" not in k["contribution_missing"]


def test_missing_costs_withhold_margin(db, store, catalog):
    _, _, (p2, v2, _) = catalog
    _order(db, store, 1, date(2026, 9, 10), [(p2, v2, 3, 20)])
    db.commit()
    k = kpis(load_dataset(db, store, date(2026, 9, 1), date(2026, 9, 30)))
    assert k["gross_profit"] is None and k["gross_margin_pct"] is None
    assert k["cost_basis"] == "insufficient"


def test_store_isolation(db, store, catalog):
    (p0, v0, _), *_ = catalog
    other = Store(code="OTH", name="Other")
    db.add(other)
    db.commit()
    _order(db, store, 1, date(2026, 9, 10), [(p0, v0, 1, 100)])
    _order(db, other, 2, date(2026, 9, 10), [(None, None, 1, 999)])
    db.commit()
    assert kpis(load_dataset(db, store, date(2026, 9, 1), date(2026, 9, 30)))["net_sales"] == 100
    assert kpis(load_dataset(db, other, date(2026, 9, 1), date(2026, 9, 30)))["net_sales"] == 999


def test_timezone_bounds(db, catalog):
    store = db.get(Store, 1)
    store.timezone = "Asia/Tokyo"  # UTC+9: an order at 15:00 UTC on the 30th is the 1st locally
    (p0, v0, _), *_ = catalog
    _order(db, store, 1, date(2026, 9, 30), [(p0, v0, 1, 100)])
    db.commit()
    assert load_dataset(db, store, date(2026, 9, 1), date(2026, 9, 30)).n_orders == 0
    assert load_dataset(db, store, date(2026, 10, 1), date(2026, 10, 31)).n_orders == 1


def test_product_concentration_and_copurchase(db, store, catalog):
    (p0, v0, _), (p1, v1, _), (p2, v2, _) = catalog
    for i in range(10):
        _order(db, store, i, date(2026, 9, 10), [(p0, v0, 1, 100), (p1, v1, 1, 50)])
    for i in range(10, 15):
        _order(db, store, i, date(2026, 9, 11), [(p2, v2, 1, 20)])
    db.commit()
    perf = product_performance(db, store, date(2026, 9, 1), date(2026, 9, 30))
    top = perf["products"][0]
    assert top["product_id"] == p0 and top["share_of_net"] == pytest.approx(1000 / 1600, abs=1e-4)
    assert top["gross_margin_pct"] == pytest.approx(0.6)
    assert perf["products"][2]["gross_margin_pct"] is None  # no cost for P2
    assert perf["concentration"]["products_for_80pct"] == 2
    cp = co_purchase(db, store, date(2026, 9, 1), date(2026, 9, 30), min_pair_orders=3)
    pair = cp["pairs"][0]
    assert {pair["a"], pair["b"]} == {p0, p1}
    assert pair["lift"] == pytest.approx(15 / 10)


def test_demo_end_to_end(db):
    store = reset_demo(db, days=200, daily_orders=12)
    assert store.is_demo
    end = date.today()
    start = end - timedelta(days=89)
    summary = store_summary(db, store, start, end)
    assert summary["kpis"]["orders"] > 300
    assert summary["kpis"]["conversion_rate"] is not None
    assert summary["customers"]["available"]
    assert 0 < summary["data_quality"]["cost_coverage"] < 1  # one product deliberately without cost
    basket = basket_overview(db, store, start, end)
    assert basket["threshold_candidates"]
    sim = simulate_threshold(db, store, start, end, threshold=120, incentive_cost=4.1)
    assert sim["orders_reachable"] > 0 and sim["margin_basis"] == "recorded unit costs"
    perf = product_performance(db, store, start, end)
    assert perf["concentration"]["top1_share"] > 0.2
