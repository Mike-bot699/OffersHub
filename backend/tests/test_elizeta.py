"""Eli Z client and sync against an emulated platform.

Response shapes follow what the team's Email Maker reads from Eli Z (lib/elizeta-catalog.ts):
`products` + `nextPageInfo` cursor pagination, and a sales report under `sold` with
`title`, `sales` (units) and `avgPrice`. Real responses were not available here.
"""

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.analytics.product_tags import Sales, compute_tags
from app.connectors import elizeta as ez
from app.models import Product, SalesReportRow
from app.services import elizeta as svc

KEY = "ez_live_abcdefghPi7g"


def _product(i, status="active", price=50.0, sku=None):
    return {"id": 1000 + i, "title": f"Toy {i}", "handle": f"toy-{i}", "status": status, "product_type": "Toys",
            "variants": [{"id": 5000 + i, "sku": sku or f"SKU-{i}", "price": str(price)}]}


def fake_platform(products, sold, seen: list):
    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        if req.headers.get("authorization") != f"Bearer {KEY}":
            return httpx.Response(401, json={"error": "unauthorized"})
        q = req.url.params
        if req.url.path == "/api/v1/products":
            if q.get("shop") != "26":
                return httpx.Response(404, json={"error": "shop not found"})
            if q.get("limit") == "1":
                return httpx.Response(200, json={"products": products[:1]})
            if "page_info" in q:  # second page: the status filter is NOT re-applied by the platform
                return httpx.Response(200, json={"products": products[2:], "nextPageInfo": None})
            return httpx.Response(200, json={"products": products[:2], "nextPageInfo": "cursor2"})
        if req.url.path == "/api/v1/sales-reports":
            return httpx.Response(200, json={"sold": sold})
        return httpx.Response(404)
    return handler


@pytest.fixture()
def platform(monkeypatch):
    seen: list = []
    state = {"products": [_product(0), _product(1), _product(2), _product(3, status="draft"),
                          _product(4, price=0, sku="GIFT")],
             "sold": [{"title": "Toy 0", "sales": 10, "avgPrice": 50}, {"title": "TOY  1", "sales": 3},
                      {"title": "renamed", "sales": 2, "sku": "SKU-2"}, {"title": "Toy 4", "sales": 7},
                      {"title": "Unknown thing", "sales": 1}]}
    monkeypatch.setattr(ez, "http_factory",
                        lambda: httpx.Client(transport=httpx.MockTransport(fake_platform(state["products"], state["sold"], seen))))
    return {"seen": seen, **state}


def test_key_is_write_only_and_hinted(db, store):
    svc.configure(db, store, shop_id="26", api_key=KEY)
    db.commit()
    d = svc.describe(svc.get(db, store.id))
    assert d["key_hint"] == "••••Pi7g" and d["shop_id"] == "26" and d["status"] == "configured"
    assert KEY not in repr(d)
    svc.configure(db, store, shop_id="26", api_key="")  # blank keeps the key
    assert svc.describe(svc.get(db, store.id))["key_hint"] == "••••Pi7g"
    with pytest.raises(Exception):
        svc.configure(db, store, shop_id="abc", api_key=None)


def test_sync_filters_inactive_on_every_page_and_matches_sales(db, store, platform):
    svc.configure(db, store, shop_id="26", api_key=KEY)
    db.commit()
    assert svc.verify(db, svc.get(db, store.id))["sample_products"] == 1
    job = svc.sync(db, store)
    assert job.status == "success", job.stats
    titles = {p.title for p in db.scalars(select(Product).where(Product.store_id == store.id))}
    assert "Toy 3" not in titles and {"Toy 0", "Toy 2", "Toy 4"} <= titles  # draft dropped from page 2
    rows = {r.title: r for r in db.scalars(select(SalesReportRow))}
    assert rows["TOY  1"].match_method == "title" and rows["renamed"].match_method == "sku"
    assert rows["Unknown thing"].product_id is None
    # orders and discount codes are declared unavailable, not silently empty
    assert any("orders endpoint" in w for w in job.warnings)
    sales_req = next(r for r in platform["seen"] if r.url.path == "/api/v1/sales-reports")
    assert sales_req.url.params["shopid"] == "26" and sales_req.url.params["currency"] == "USD"


def test_wrong_shop_or_key_is_reported(db, store, platform):
    svc.configure(db, store, shop_id="27", api_key=KEY)
    with pytest.raises(ez.ElizetaError, match="shop id"):
        svc.verify(db, svc.get(db, store.id))
    svc.configure(db, store, shop_id="26", api_key="wrong-key-0000")
    with pytest.raises(ez.ElizetaError, match="API key"):
        svc.verify(db, svc.get(db, store.id))
    assert svc.get(db, store.id).status == "error"


def test_tag_rule_free_and_minimum(db):
    rows = [Sales(i, 10, 100.0 - i, None) for i in range(20)] + [Sales(99, 5, 0.0, None)]
    t = compute_tags(rows, min_ranked=10)
    assert t[0]["tag"] == "best_seller" and t[1]["tag"] == "best_seller" and t[2]["tag"] == "top_seller"
    assert t[99]["tag"] == "free" and t[99]["rank"] is None
    small = compute_tags([Sales(1, 1, 75.0, None), Sales(2, 1, 10.0, None)], min_ranked=10)
    assert small[1]["tag"] is None and small[1]["rank"] == 1  # too few products: no relative bands
    by_units = compute_tags([Sales(i, 20 - i, None, None) for i in range(10)], min_ranked=0)
    assert by_units[0]["tag"] == "best_seller"  # no revenue data anywhere: ranked by units


def test_settings_api_never_returns_the_key(db, store, platform):
    from app.main import create_app

    c = TestClient(create_app())
    r = c.put(f"/api/stores/{store.id}/settings", json={"code": "mau", "product_host": "https://www.Thelittlelearnerstoys.com/x",
                                                        "elizeta_shop_id": "26", "elizeta_api_key": KEY})
    assert r.status_code == 200, r.text
    body = r.json()
    assert KEY not in r.text and body["elizeta"]["key_hint"] == "••••Pi7g"
    assert body["code"] == "MAU" and body["product_host"] == "www.thelittlelearnerstoys.com"
    assert c.post(f"/api/stores/{store.id}/elizeta/verify").json()["elizeta"]["status"] == "connected"
    assert KEY not in str(c.get("/api/audit").json())
