import pytest
from sqlalchemy import select

from app.core.errors import IntegrationError
from app.core.security import redact
from app.models import CostRecord, Coupon, Order, OrderLine, PriceHistory, Product, SyncJob, Variant
from app.services import integrations as svc
from app.services.sync import freshness, run_sync
from tests.fake_shopify import SHOP, FakeShopify, order_rows, sample_catalog


@pytest.fixture()
def fake(monkeypatch):
    f = FakeShopify()
    import httpx

    monkeypatch.setattr(svc, "http_factory", lambda: httpx.Client(transport=f.transport()))
    # never actually sleep in tests
    monkeypatch.setattr("app.connectors.shopify.connector.time.sleep", lambda s: None)
    monkeypatch.setattr("app.connectors.shopify.client.time.sleep", lambda s: None)
    return f


def _connect(db, store, method="admin_token"):
    if method == "admin_token":
        integ = svc.configure_shopify(db, store, shop_domain="test-shop", auth_method=method,
                                      access_token="shpat_secret123")
    else:
        integ = svc.configure_shopify(db, store, shop_domain=SHOP, auth_method=method,
                                      client_id="cid", client_secret="csecret")
    svc.verify(db, integ)
    db.commit()
    return integ


def test_credentials_are_encrypted_and_never_described(db, store, fake):
    integ = _connect(db, store)
    assert "shpat_secret123" not in integ.credentials_encrypted
    desc = svc.describe(integ)
    assert "shpat_secret123" not in str(desc)
    assert desc["status"] == "connected"
    assert desc["shop_info"]["probe"]["orders"] == "ok"
    assert store.timezone == "America/New_York"


def test_invalid_domain_rejected(db, store):
    with pytest.raises(IntegrationError):
        svc.configure_shopify(db, store, shop_domain="www.mystore.com", auth_method="admin_token",
                              access_token="x")


def test_client_credentials_token_exchange_is_cached(db, store, fake):
    integ = _connect(db, store, "client_credentials")
    assert fake.token_exchanges == 1
    assert integ.token_cache_encrypted and "shpat_fake_cc_token" not in integ.token_cache_encrypted
    run_sync(db, store.id, resources=["discounts"])
    assert fake.token_exchanges == 1  # cached token reused


def test_rejected_token_marks_error(db, store, fake):
    integ = svc.configure_shopify(db, store, shop_domain=SHOP, auth_method="admin_token", access_token="bad")
    fake.reject_token = True
    with pytest.raises(IntegrationError):
        svc.verify(db, integ)
    assert integ.status == "error"


def test_full_sync_products_orders_discounts(db, store, fake):
    fake.products_jsonl = sample_catalog()
    fake.orders_jsonl = (
        order_rows(1, processed="2026-09-01T10:00:00Z", lines=[("gid://shopify/ProductVariant/11", 1, 89.0, 8.9)],
                   codes=["fall10"], customer="gid://shopify/Customer/7")
        + order_rows(2, processed="2026-09-15T10:00:00Z",
                     lines=[("gid://shopify/ProductVariant/11", 1, 89.0, 0), ("gid://shopify/ProductVariant/21", 2, 25.0, 0)],
                     customer="gid://shopify/Customer/7", refunded=25.0)
    )
    fake.refunds = {"gid://shopify/Order/2": [{"quantity": 1, "lineItem": {"id": "gid://shopify/LineItem/21"},
                                               "subtotalSet": {"shopMoney": {"amount": "25.00"}}}]}
    fake.code_discounts = [{"id": "gid://shopify/DiscountCodeNode/5", "codeDiscount": {
        "__typename": "DiscountCodeBasic", "title": "Fall 10", "status": "ACTIVE", "startsAt": "2026-09-01T00:00:00Z",
        "endsAt": None, "asyncUsageCount": 1, "usageLimit": None, "appliesOncePerCustomer": False,
        "codes": {"nodes": [{"code": "FALL10"}]},
        "minimumRequirement": {"__typename": "DiscountMinimumSubtotal", "greaterThanOrEqualToSubtotal": {"amount": "99.0"}},
        "customerGets": {"value": {"__typename": "DiscountPercentage", "percentage": 0.1}}}}]
    fake.throttle_first = True
    _connect(db, store)

    job = run_sync(db, store.id)
    assert job.status == "success", job.stats
    assert db.scalar(select(Product).where(Product.store_id == store.id)) is not None
    assert len(db.scalars(select(Variant)).all()) == 2
    cost = db.scalar(select(CostRecord))
    assert cost.unit_cost == 22.5 and cost.source == "shopify"

    orders = db.scalars(select(Order).order_by(Order.processed_at)).all()
    assert [o.gross_sales for o in orders] == [89.0, 139.0]
    assert orders[0].discount_codes == ["FALL10"]
    assert orders[0].discount_applications[0]["value"] == 10.0
    assert [o.customer_order_index for o in orders] == [1, 2]
    assert orders[1].refunded_merchandise == 25.0
    line = db.scalar(select(OrderLine).where(OrderLine.source_id == "gid://shopify/LineItem/21"))
    assert line.refunded_quantity == 1 and line.product_id is not None

    coupon = db.scalar(select(Coupon))
    assert coupon.code == "FALL10" and coupon.value == 10.0 and coupon.min_subtotal == 99.0

    # bulk query used the initial history window, then the next run is incremental
    assert "created_at:>=" in fake.bulk_queries[1]
    run_sync(db, store.id)
    assert "updated_at:>" in fake.bulk_queries[-1]
    assert len(db.scalars(select(Order)).all()) == 2  # no duplicates
    assert freshness(db, store)["orders"]["status"] == "fresh"


def test_price_change_recorded(db, store, fake):
    fake.products_jsonl = sample_catalog()
    _connect(db, store)
    run_sync(db, store.id, resources=["products"])
    fake.products_jsonl[1]["price"] = "95.00"
    run_sync(db, store.id, resources=["products"], full=True)
    variant = db.scalar(select(Variant).where(Variant.sku == "RJ-M-P"))
    prices = [p.price for p in db.scalars(select(PriceHistory).where(PriceHistory.variant_id == variant.id)
                                          .order_by(PriceHistory.id))]
    assert prices == [89.0, 95.0]


def test_failed_resource_gives_partial_and_keeps_watermark(db, store, fake):
    fake.products_jsonl = sample_catalog()
    fake.fail_orders_bulk = True
    _connect(db, store)
    job = run_sync(db, store.id)
    assert job.status == "partial"
    assert "error" in job.stats["orders"]
    from app.services.sync import get_state

    assert get_state(db, store.id, "orders").watermark is None
    assert get_state(db, store.id, "products").watermark is not None


def test_missing_scope_is_a_warning_not_a_silent_gap(db, store, fake):
    fake.scopes = ["read_products", "read_orders"]
    _connect(db, store)
    job = run_sync(db, store.id)
    assert job.status == "success"
    assert job.stats["discounts"] == {"skipped": True}
    joined = " ".join(job.warnings)
    assert "read_all_orders" in joined and "read_customers" in joined


def test_demo_store_cannot_connect(db):
    from app.core.errors import ValidationError
    from app.models import Store

    demo = Store(code="DEMO", name="Demo", is_demo=True)
    db.add(demo)
    db.commit()
    with pytest.raises(ValidationError):
        svc.configure_shopify(db, demo, shop_domain=SHOP, auth_method="admin_token", access_token="x")


def test_redaction():
    assert "shpat_abc" not in redact("token shpat_abcDEF123 leaked")
    assert redact('{"client_secret": "zzz"}').count("zzz") == 0


def test_sync_jobs_recorded(db, store, fake):
    _connect(db, store)
    run_sync(db, store.id)
    assert db.scalar(select(SyncJob)).finished_at is not None
