"""Discount codes and sales by discount from the Eli Z API.

Field names follow the platform developer's description of the endpoints (camelCase:
discountName, discountMethod, appliedDiscounts, discountedOrders…; code, campaign, status…).
Codes and amounts are MAU's real ones (Shopify discounts export and sales report); the
single-use codes under THANKS20 mirror the developer's example (2QKRFMRP under THANKS20).
"""

from datetime import date

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.connectors import elizeta as ez
from app.models import Coupon, DiscountReport, DiscountReportRow, PromoEvent
from app.offers.discount_report import import_discount_report
from app.services import elizeta as svc

KEY = "ez_live_abcdefghPi7g"
TODAY = date(2026, 10, 30)

CODES = [
    {"code": "WLL10", "campaign": "WLL10", "title": "WLL10", "status": "active", "valueType": "percentage", "value": "-10.0",
     "usageCount": 1212, "usageLimit": None, "startsAt": "2026-01-02T00:00:00Z", "endsAt": None},
    {"code": "SPOOKLAB", "campaign": "SPOOKLAB", "title": "SPOOKLAB", "status": "active", "valueType": "percentage",
     "value": "-15.0", "usageCount": 11, "startsAt": "2026-10-19T16:52:56Z", "endsAt": "2026-11-15T18:00:00Z"},
    {"code": "OWLPASS", "campaign": "OWLPASS", "title": "OWLPASS", "status": "active", "valueType": "percentage",
     "value": "-20.0", "usageCount": 4, "startsAt": "2026-10-24T11:46:55Z", "endsAt": "2026-11-15T13:00:00Z"},
    {"code": "BRIGHT20", "campaign": "BRIGHT20", "title": "BRIGHT20", "status": "expired", "valueType": "percentage",
     "value": "-20.0", "usageCount": 211, "startsAt": "2026-02-09T16:49:25Z", "endsAt": "2026-03-31T15:49:49Z"},
    *[{"code": c, "campaign": "THANKS20", "title": f"THANKS20 (2026-10-2{i} 02:03)", "status": s, "valueType": "percentage",
       "value": "-20.0", "usageCount": 1, "usageLimit": 1, "startsAt": f"2026-10-2{i}T02:03:00Z"}
      for i, (c, s) in enumerate([("2QKRFMRP", "expired"), ("7HD2KQ1M", "active"), ("PX0QWE77", "active"),
                                  ("ZZ81MNBV", "expired"), ("K2JD9QPA", "active"), ("Q1W2E3R4", "active")])],
]
SALES = [
    {"day": "2026-10-25", "discountName": "SPOOKLAB", "discountMethod": "discount_code", "discountType": "percentage",
     "discountClass": "order", "appliedDiscounts": -95.4, "discountedOrders": 6, "grossSales": 636.0, "salesReversals": 0,
     "netSales": 540.6, "grossSalesUsd": 636.0},
    {"day": "2026-10-28", "discountName": "SPOOKLAB", "discountMethod": "discount_code", "appliedDiscounts": -79.5,
     "discountedOrders": 5, "grossSales": 530.0, "salesReversals": 0, "netSales": 450.5},
    {"day": "2026-11-02", "discountName": "SPOOKLAB", "discountMethod": "discount_code", "appliedDiscounts": -15.9,
     "discountedOrders": 1, "grossSales": 106.0, "salesReversals": 0, "netSales": 90.1},
    {"day": "2026-10-27", "discountName": "2QKRFMRP", "discountMethod": "discount_code", "appliedDiscounts": -20.0,
     "discountedOrders": 1, "grossSales": 100.0, "netSales": 80.0},
    {"day": "2026-10-28", "discountName": "7HD2KQ1M", "discountMethod": "discount_code", "appliedDiscounts": -24.0,
     "discountedOrders": 1, "grossSales": 120.0, "netSales": 96.0},
    {"day": "2026-10-28", "discountName": "Kaching Bundles - RC Paper Airplane", "discountMethod": "automatic",
     "discountClass": "product", "appliedDiscounts": -61.0, "discountedOrders": 4, "grossSales": 410.0, "netSales": 349.0},
]


@pytest.fixture()
def platform(monkeypatch):
    seen: list = []
    state = {"codes": CODES, "sales": SALES}

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        q, path = req.url.params, req.url.path
        limit, offset = int(q.get("limit", 1000)), int(q.get("offset", 0))
        if path == "/api/v1/products":
            return httpx.Response(200, json={"products": [], "nextPageInfo": None})
        if path == "/api/v1/sales-reports":
            return httpx.Response(200, json={"sold": []})
        if path == "/api/v1/discounts/codes":
            return httpx.Response(200, json={"data": state["codes"][offset:offset + limit]})
        if path == "/api/v1/discounts/sales":
            rows = [r for r in state["sales"] if q["from"] <= r["day"] <= q["to"]]
            return httpx.Response(200, json={"data": rows[offset:offset + limit]})
        return httpx.Response(404)

    monkeypatch.setattr(ez, "http_factory", lambda: httpx.Client(transport=httpx.MockTransport(handler)))
    return {"seen": seen, "state": state}


def _sync(db, store):
    svc.configure(db, store, shop_id="26", api_key=KEY)
    db.commit()
    return svc.sync(db, store, today=TODAY)


def test_codes_grouped_by_campaign_and_sales_by_day(db, store, platform):
    job = _sync(db, store)
    assert job.status == "success", job.stats
    coupons = {c.code: c for c in db.scalars(select(Coupon).where(Coupon.store_id == store.id, Coupon.source == "elizeta"))}
    # six generated codes become one campaign, live because some of its codes are active
    assert set(coupons) == {"WLL10", "SPOOKLAB", "OWLPASS", "BRIGHT20", "THANKS20"}
    assert coupons["THANKS20"].status == "active" and coupons["THANKS20"].title == "THANKS20 (6 single-use codes)"
    assert coupons["THANKS20"].usage_count == 6 and coupons["SPOOKLAB"].value == 15 and coupons["SPOOKLAB"].value_type == "percent"
    assert job.stats["discount_codes"]["grouped_campaigns"] == 1 and "campaign" in job.stats["discount_codes"]["fields"]
    report = db.scalar(select(DiscountReport).where(DiscountReport.store_id == store.id))
    assert report.source == "elizeta" and report.by_day
    assert (report.period_start, report.period_end) == (date(2026, 10, 25), date(2026, 10, 28))  # days present, not the window
    rows = db.scalars(select(DiscountReportRow).where(DiscountReportRow.report_id == report.id)).all()
    names = {r.name for r in rows}
    assert "THANKS20" in names and "2QKRFMRP" not in names  # sales of generated codes follow their campaign
    spook = [r for r in rows if r.name == "SPOOKLAB"]
    assert sum(r.orders for r in spook) == 11 and sum(r.discounts for r in spook) == pytest.approx(174.9)
    auto = next(r for r in rows if r.method == "automatic")
    assert auto.name == "Kaching Bundles - RC Paper Airplane" and auto.discount_class == "product"
    req = next(r for r in platform["seen"] if r.url.path == "/api/v1/discounts/sales")
    assert req.url.params["shop"] == "26"


def test_technical_status_from_platform_codes(db, store, platform):
    mk = lambda name, codes, status: PromoEvent(store_id=store.id, name=name, coupon_codes=codes, status=status,
                                                 start_date=date(2026, 10, 25), end_date=date(2026, 10, 31), source="manual")
    live, gone, planned = mk("Halloween", ["SPOOKLAB"], "live"), mk("Ghost", ["NOPE10"], "live"), mk("Later", ["NOPE20"], "scheduled")
    db.add_all([live, gone, planned])
    db.commit()
    _sync(db, store)
    for e in (live, gone, planned):
        db.refresh(e)
    assert live.technical_status == "active" and "via Eli Z" in live.technical_evidence
    assert gone.technical_status == "unavailable"  # complete list: the code does not exist
    assert planned.technical_status == "unknown"   # not gone live: not created yet is expected


def test_offer_results_use_the_report_covering_the_offer(db, store, platform):
    """An older Shopify export still serves older offers once a recent platform report exists."""
    old = PromoEvent(store_id=store.id, name="Bright spring", coupon_codes=["BRIGHT20"], status="completed",
                     start_date=date(2026, 2, 9), end_date=date(2026, 3, 31), source="manual")
    db.add(old)
    import_discount_report(db, store, b'{"discount_name":"BRIGHT20","discount_method":"discount_code","applied_discounts":-5737.63,'
                                      b'"discounted_orders":211,"gross_sales":28688.15,"net_sales":22950.52}\n',
                           "Sales_by_discount_codes_-_2026-01-01_-_2026-10-15.jsonl", start=None, end=None, dry_run=False)
    db.commit()
    _sync(db, store)
    c = TestClient(__import__("app.main", fromlist=["create_app"]).create_app())
    ev = c.get(f"/api/events/{old.id}").json()
    assert ev["discount_results"]["status"] == "ok" and ev["discount_results"]["orders"] == 211
    assert ev["discount_results"]["report_period"] == ["2026-01-01", "2026-10-15"]


def test_unrecognised_shape_writes_nothing(db, store, platform):
    platform["state"]["codes"] = [{"id": 1, "name": "WLL10"}]
    platform["state"]["sales"] = [{"day": "2026-10-25", "label": "SPOOKLAB", "count": 3}]
    job = _sync(db, store)
    assert job.status == "partial"
    assert "without a code field" in job.stats["discount_codes"]["error"] and "name" in job.stats["discount_codes"]["error"]
    assert "without a discount name" in job.stats["discount_sales"]["error"]
    assert not db.scalars(select(Coupon).where(Coupon.source == "elizeta")).all()
    assert not db.scalars(select(DiscountReport)).all()


def test_offset_pagination(db, store, platform, monkeypatch):
    monkeypatch.setattr(ez, "PAGE", 3)
    _sync(db, store)
    offsets = [r.url.params["offset"] for r in platform["seen"] if r.url.path == "/api/v1/discounts/codes"]
    assert offsets == ["0", "3", "6", "9"]  # 10 codes, pages of 3
    assert db.scalar(select(Coupon).where(Coupon.code == "THANKS20")).raw["codes"] == 6
