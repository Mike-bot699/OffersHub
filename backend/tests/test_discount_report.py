"""Shopify "Sales by discount code" import, checked on the real MAU export (2026-01-01 → 2026-10-30)."""

import json
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.models import PromoEvent
from app.offers.discount_report import import_discount_report, latest_report, period_from_filename, summarize

# A slice of the real export: biggest codes, an automatic bundle, single-use KNO codes and the test codes.
REAL = [
    {"discount_name": "WLL10", "discount_method": "discount_code", "discount_type": "percentage", "discount_class": "order", "applied_discounts": -12852.83, "discounted_orders": 1212, "gross_sales": 128552.19, "sales_reversals": -362.05, "net_sales": 115338.6},
    {"discount_name": "POP10", "discount_method": "discount_code", "discount_type": "percentage", "discount_class": "order", "applied_discounts": -8115.89, "discounted_orders": 690, "gross_sales": 81164.6, "sales_reversals": -239.08, "net_sales": 72811.07},
    {"discount_name": "Kaching Bundles - MixMaster Junior DJ Console and 4 others", "discount_method": "automatic", "discount_type": "percentage", "discount_class": "product", "applied_discounts": -5473.5, "discounted_orders": 357, "gross_sales": 56813.71, "sales_reversals": -133.88, "net_sales": 51207.78},
    {"discount_name": "SPOOKLAB", "discount_method": "discount_code", "discount_type": "percentage", "discount_class": "order", "applied_discounts": -300.03, "discounted_orders": 11, "gross_sales": 2000.44, "sales_reversals": 0, "net_sales": 1700.41},
    *[{"discount_name": f"KNO-{c}", "discount_method": "discount_code", "discount_type": "percentage", "discount_class": "order", "applied_discounts": -20.0, "discounted_orders": 1, "gross_sales": 100.0, "sales_reversals": 0, "net_sales": 80.0}
      for c in ("HXE09IXP", "LWBB2YLE", "VV5EBZZB", "DB3FLLAI", "Y-CMKYOR")],
    {"discount_name": "ANGELA-TEST", "discount_method": "discount_code", "discount_type": "percentage", "discount_class": "order", "applied_discounts": -104.45, "discounted_orders": 1, "gross_sales": 160.7, "sales_reversals": 0, "net_sales": 56.25},
    {"discount_name": "ANTOTEST100", "discount_method": "discount_code", "discount_type": "percentage", "discount_class": "order", "applied_discounts": -100.44, "discounted_orders": 1, "gross_sales": 100.44, "sales_reversals": 0, "net_sales": 0},
]
FILENAME = "Sales_by_discount_codes_-_2026-01-01_-_2026-10-30.jsonl"


def _content(rows=REAL) -> bytes:
    return "\n".join(json.dumps(r) for r in rows).encode()


def test_period_from_shopify_filename():
    assert period_from_filename(FILENAME) == (date(2026, 1, 1), date(2026, 10, 30))


def test_import_summary_groups_single_use_and_drops_tests(db, store):
    rep = import_discount_report(db, store, _content(), FILENAME, start=None, end=None, dry_run=True)
    assert rep.rows == 11 and rep.automatic == 1 and rep.test_rows == ["ANGELA-TEST", "ANTOTEST100"]
    import_discount_report(db, store, _content(), FILENAME, start=None, end=None, dry_run=False)
    db.add(PromoEvent(store_id=store.id, name="Tricks, Treats & Clever Feats", coupon_codes=["SPOOKLAB", "OWLPASS"]))
    db.commit()
    s = summarize(db, latest_report(db, store))
    names = [ln["name"] for ln in s["lines"]]
    assert names[:2] == ["WLL10", "POP10"]
    assert "KNO-* (single-use codes)" in names and not any(n.startswith("KNO-H") for n in names)
    assert "ANGELA-TEST" not in names and s["excluded_tests"] == ["ANGELA-TEST", "ANTOTEST100"]
    wll = s["lines"][0]
    assert wll["discounts"] == 12852.83 and wll["aov"] == pytest.approx(115338.6 / 1212, abs=0.01)
    assert next(ln for ln in s["lines"] if ln["name"] == "SPOOKLAB")["offers"] == ["Tricks, Treats & Clever Feats"]
    assert s["totals"]["automatic"]["orders"] == 357


def test_reimport_same_period_replaces(db, store):
    import_discount_report(db, store, _content(), FILENAME, start=None, end=None, dry_run=False)
    rep = import_discount_report(db, store, _content(REAL[:2]), FILENAME, start=None, end=None, dry_run=False)
    assert rep.replaced_previous
    assert [ln["name"] for ln in summarize(db, latest_report(db, store))["lines"]] == ["WLL10", "POP10"]


def test_csv_export_and_api(db, store):
    csv = (b"Discount name,Discount method,Discount type,Discount class,Applied discounts,Discounted orders,"
           b"Gross sales,Sales reversals,Net sales\nWLL10,discount_code,percentage,order,-12852.83,1212,128552.19,-362.05,115338.6\n")
    from app.main import create_app

    c = TestClient(create_app())
    r = c.post(f"/api/stores/{store.id}/discount-report/import", files={"file": ("discounts.csv", csv)},
               data={"dry_run": "false", "start": "2026-01-01", "end": "2026-10-30"})
    assert r.status_code == 200, r.text
    rep = c.get(f"/api/stores/{store.id}/discount-report").json()
    assert rep["lines"][0]["orders"] == 1212
    o = c.get(f"/api/overview?store_id={store.id}&days=90").json()
    assert o["discount_report"]["lines"][0]["name"] == "WLL10"


def test_real_file_if_present(db, store):
    p = Path("/root/.claude/uploads/393841e1-13f6-59b9-a88c-a4b0d4bdf9d3/c24f4a06-Sales_by_discount_codes_-_2026-01-01_-_2026-10-30.jsonl")
    if not p.exists():
        pytest.skip("real export not available here")
    import_discount_report(db, store, p.read_bytes(), FILENAME, start=None, end=None, dry_run=False)
    s = summarize(db, latest_report(db, store))
    assert s["lines"][0]["name"] == "WLL10" and s["grouped"] == {"KNO-* (single-use codes)": 35}


def test_offers_get_their_code_results(db, store):
    from app.main import create_app

    import_discount_report(db, store, _content(), FILENAME, start=None, end=None, dry_run=False)
    db.commit()
    c = TestClient(create_app())
    mk = lambda **kw: c.post("/api/events", json={"store_id": store.id, **kw}).json()
    spook = mk(name="Tricks, Treats", start_date="2026-10-25", end_date="2026-10-31", coupon_codes=["spooklab"])
    a = mk(name="Anniversary A", start_date="2026-09-19", end_date="2026-09-22", coupon_codes=["WLL10"])
    mk(name="Anniversary B", start_date="2026-09-30", end_date="2026-10-04", coupon_codes=["WLL10"])
    bundle = mk(name="DJ bundle", start_date="2026-03-01", end_date="2026-03-31",
                linked_discounts=["Kaching Bundles - MixMaster Junior DJ Console and 4 others"])
    future = mk(name="Black Friday", start_date="2026-11-27", end_date="2026-11-29", coupon_codes=["WLL10"])
    rows = {e["name"]: e for e in c.get(f"/api/events?store_id={store.id}").json()}
    r = rows["Tricks, Treats"]["discount_results"]
    assert r["orders"] == 11 and r["net_sales"] == 1700.41 and r["shared_with"] == 0 and r["matched"] == ["SPOOKLAB"]
    shared = rows["Anniversary A"]["discount_results"]
    assert shared["orders"] == 1212 and shared["shared_with"] == 1  # B also uses WLL10; Black Friday is after the report
    assert rows["DJ bundle"]["discount_results"]["orders"] == 357
    assert rows["Black Friday"]["discount_results"]["status"] == "after_report"
    assert c.get(f"/api/events/{a['id']}").json()["discount_results"]["orders"] == 1212
    assert spook and bundle and future


def test_by_day_report_splits_offer_and_late_use(db, store):
    rows = [{"day": "2026-10-26", "discount_name": "SPOOKLAB", "discount_method": "discount_code", "applied_discounts": -30, "discounted_orders": 2, "gross_sales": 200, "net_sales": 170},
            {"day": "2026-11-03", "discount_name": "SPOOKLAB", "discount_method": "discount_code", "applied_discounts": -15, "discounted_orders": 1, "gross_sales": 100, "net_sales": 85},
            {"day": "2026-10-01", "discount_name": "SPOOKLAB", "discount_method": "discount_code", "applied_discounts": -15, "discounted_orders": 1, "gross_sales": 100, "net_sales": 85}]
    import_discount_report(db, store, _content(rows), "codes_-_2026-10-01_-_2026-11-05.jsonl", start=None, end=None, dry_run=False)
    db.add(PromoEvent(store_id=store.id, name="Halloween", coupon_codes=["SPOOKLAB"], start_date=date(2026, 10, 25), end_date=date(2026, 10, 31)))
    db.commit()
    from app.offers.discount_report import offer_results

    ev = db.query(PromoEvent).one()
    row = {"id": ev.id, "store_id": store.id, "coupon_codes": ["SPOOKLAB"], "linked_discounts": [],
           "start_date": "2026-10-25", "end_date": "2026-10-31"}
    offer_results(db, [row])
    res = row["discount_results"]
    assert res["orders"] == 2 and res["late"] == {"orders": 1, "discounts": 15.0}


def test_placements_and_offer_linking(db, store):
    from app.main import create_app

    import_discount_report(db, store, _content(), FILENAME, start=None, end=None, dry_run=False)
    db.commit()
    c = TestClient(create_app())
    ev = c.post("/api/events", json={"store_id": store.id, "name": "Halloween"}).json()
    r = c.post(f"/api/stores/{store.id}/discount-placements/link", json={"code": "spooklab", "event_id": ev["id"]}).json()
    assert [o["name"] for o in r["offers"]] == ["Halloween"]
    assert c.get(f"/api/events/{ev['id']}").json()["coupon_codes"] == ["SPOOKLAB"]  # the offer follows
    auto = "Kaching Bundles - MixMaster Junior DJ Console and 4 others"
    c.post(f"/api/stores/{store.id}/discount-placements/link", json={"code": auto, "event_id": ev["id"], "automatic": True})
    c.put(f"/api/stores/{store.id}/discount-placements", json={"code": "WLL10", "placements": ["Welcome popup", " welcome popup ", "Abandoned cart email"]})
    rep = c.get(f"/api/stores/{store.id}/discount-report").json()
    lines = {ln["name"]: ln for ln in rep["lines"]}
    assert lines["WLL10"]["placements"] == ["Welcome popup", "Abandoned cart email"]
    assert lines["SPOOKLAB"]["offers"] == ["Halloween"] and lines[auto]["offers"] == ["Halloween"]
    r = c.post(f"/api/stores/{store.id}/discount-placements/link", json={"code": "SPOOKLAB", "event_id": ev["id"], "unlink": True}).json()
    assert r["offers"] == []
