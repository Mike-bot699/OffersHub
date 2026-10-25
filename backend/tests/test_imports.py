import io
from datetime import date

import pytest
from openpyxl import Workbook
from sqlalchemy import select

from app.core.errors import ConflictError, ValidationError
from app.models import CostRecord, Coupon, Offer, Product, PromoEvent, Store, TrafficDaily, Variant
from app.offers.classify import classify_offer
from app.services.csv_import import import_costs, import_traffic
from app.services.offers_import import import_offers_workbook

EVENT_HEADERS = ["Event Name", "Store", "Year", "Start Date", "End Date", "Duration (days)", "Offer", "Type",
                 "Coupon", "🚫 Threshold", "⚡ Offer Activation", "Bonus", "🖥️ Displayed", "Offers Orders",
                 "Total Orders", "%", "Sales Amount", "Total Sent (for email only)", "Order per email (for email only)",
                 "Bonus Give Away", "Notes"]


def _workbook() -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "Events"
    ws.append(EVENT_HEADERS)
    ws.append(["🍂 Autumn Sale", "🧥 AAA", 2024, "23/9/2024", "29/9/2024", 7, "Extra 10% off all orders", None,
               "FALL10", "$0,00", "Manual", None, "Full-Displayed", 110, 714, "15,41%", "$7.820,52"])
    ws.append(["🎃 Trick or Treat", "👗 BBB", 2024, "25/10/2024", "01/11/2024", 8, "Extra 15% off over $99", None,
               "TRICK15", "$99,00", "Auto", "Mystery Free Halloween Gift", "Fully displayed", 112, 909, "12,32%",
               "$15.496,74"])
    ws.append(["Copy row", "👗 BBB", 2025, "20/01/2025", "20/01/2025", 1, "15% out of $99 minimum order", None,
               "BLUE15", "$99,00", "Manual", None, "✉️ Email-Only", 6, 178, "3,37%", "$7.820,52", "39995"])
    ws.append(["Broken", "🧥 AAA", 2025, "06/05/2025", "05/05/2025", 1, "Buy 3 items get 10% off, buy 5 items get "
               "15% off", None, None, "-", "Auto", None, None, 900, 100, "9%", "$100,00"])
    ws.append(["Placeholder", "🧥 AAA", 2024, None, None, 1])
    ws.append(["Not launched", "👗 BBB", 2025, "01/06/2025", "07/06/2025", 7, "10% off", None, "X10", None,
               None, None, None, None, None, None, None, None, None, None,
               "Due to strained relations, this offer was not launched."])
    codes = wb.create_sheet("Fix Codes")
    codes.append(["Coupon Code", "Offer Description", "Event Name", "Channel", "Store", "Discount", "Min Purchase",
                  "Times Used", "Status", "Start Date", "End Date"])
    codes.append(["GOLD30", "30% off", "New Year", "👑 Top bar", "👗 BBB", "30.0%", "None", 26, "Active",
                  "2025-01-09", "2026-03-31"])
    codes.append(["SISTER20", "Buy 2, 20% off", "Sisters day", "✉️ Email", "👗 BBB", "20.0%", "2+ items", 0,
                  "DELETED", None, None])
    disc = wb.create_sheet("Store Discounts")
    disc.append(["Store name", "Code", "New Customer", "Threshold", "Existing Customer", "Threshold",
                 "Shipping threshold", "Last Updated", "Finance", "Notes"])
    disc.append(["North", "BBB", "15%", "$99,00", "30%", "$99,00", "$90,00", "13/03/2025", "x", None])
    wb.create_sheet("COGS")
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_workbook_dry_run_writes_nothing(db):
    rep = import_offers_workbook(db, _workbook(), "offers.xlsx", dry_run=True, today=date(2026, 10, 25))
    assert rep.events_parsed == 6
    assert sorted(rep.stores_created) == ["AAA", "BBB"]
    assert rep.sheets_ignored == ["COGS"]
    assert db.scalar(select(PromoEvent)) is None and db.scalar(select(Store)) is None


def test_workbook_import_normalises_and_flags(db):
    rep = import_offers_workbook(db, _workbook(), "offers.xlsx", dry_run=False, today=date(2026, 10, 25))
    assert rep.events_created == 6
    events = {e.name: e for e in db.scalars(select(PromoEvent))}
    autumn = events["Autumn Sale"]
    assert autumn.offer_sales == pytest.approx(7820.52)
    assert autumn.threshold_amount is None and autumn.threshold_text == "none"
    assert autumn.display == "full_site" and autumn.activation == "manual_code"
    assert autumn.verification_status == "documented_only"
    assert autumn.status == "ended" and autumn.offer_type == "percentage_discount"

    trick = events["Trick or Treat"]
    assert trick.threshold_amount == 99 and trick.activation == "automatic"
    assert "mystery_gift" in db.get(Offer, trick.offer_id).mechanic["components"]

    assert any("identical to row" in i for i in events["Copy row"].data_issues)
    broken = events["Broken"].data_issues
    assert any("before start date" in i for i in broken)
    assert any("exceed total orders" in i for i in broken)
    assert events["Broken"].offer_type == "quantity_incentive"
    assert events["Placeholder"].status == "undocumented"
    assert events["Not launched"].status == "not_launched"

    bbb = db.scalar(select(Store).where(Store.code == "BBB"))
    assert bbb.commercial_settings["free_shipping_threshold"] == 90
    assert bbb.commercial_settings["new_customer_discount_pct"] == pytest.approx(0.15)
    coupons = {c.code: c for c in db.scalars(select(Coupon))}
    assert coupons["GOLD30"].value == 30 and coupons["GOLD30"].usage_count == 26
    assert coupons["SISTER20"].min_quantity == 2


def test_workbook_reimport_identical_file_is_blocked_and_edited_file_updates(db):
    content = _workbook()
    import_offers_workbook(db, content, "offers.xlsx", dry_run=False)
    with pytest.raises(ConflictError):
        import_offers_workbook(db, content, "offers.xlsx", dry_run=False)
    rep = import_offers_workbook(db, content, "offers.xlsx", dry_run=False, force=True)
    assert rep.events_created == 0 and rep.events_updated == 6
    assert len(db.scalars(select(PromoEvent)).all()) == 6


def test_demo_store_code_not_overwritten(db):
    db.add(Store(code="AAA", name="Demo", is_demo=True))
    db.commit()
    rep = import_offers_workbook(db, _workbook(), "offers.xlsx", dry_run=False)
    assert all(e.store_id != 1 for e in db.scalars(select(PromoEvent)))
    assert any("demo" in i["issue"] for i in rep.issues)


def test_unsupported_file():
    with pytest.raises(ValidationError):
        import_offers_workbook(None, b"x", "offers.pdf")  # type: ignore[arg-type]


@pytest.mark.parametrize("text,expected", [
    ("Extra 10% off all orders", "percentage_discount"),
    ("$30 out of $100 minimum order", "fixed_discount"),
    ("Buy 1 item, Get 1 item at 40% off", "second_item"),
    ("Free waterproof travel bag when you buy 2 jackets", "free_gift"),
    ("Buy 3 items get 10% off, buy 5 items get 15% off", "quantity_incentive"),
    ("Up to 50% Off Storewide + Bonus", "tiered_discount"),
    ("$20 credit to buy your favorite toys", "future_voucher"),
    ("no offer just a message", "message_only"),
    ("Special Launch Price $67", "special_price"),
])
def test_classifier(text, expected):
    assert classify_offer(text)["offer_type"] == expected


def _catalog(db, store):
    p = Product(store_id=store.id, source="shopify", source_id="gid://shopify/Product/1", title="Jacket",
                handle="jacket")
    db.add(p)
    db.flush()
    db.add_all([
        Variant(store_id=store.id, product_id=p.id, source="shopify", source_id="gid://shopify/ProductVariant/11",
                sku="J-M", price=90),
        Variant(store_id=store.id, product_id=p.id, source="shopify", source_id="gid://shopify/ProductVariant/12",
                sku="DUP", price=90),
        Variant(store_id=store.id, product_id=p.id, source="shopify", source_id="gid://shopify/ProductVariant/13",
                sku="DUP", price=90),
    ])
    db.commit()


def test_costs_csv(db, store):
    _catalog(db, store)
    csv = b"SKU;Unit Cost;Variant ID\nJ-M;22,50;\nDUP;10;\n;9;13\nNOPE;5;\nJ-M;abc;\n"
    rep = import_costs(db, store, csv, "costs.csv", dry_run=False)
    assert rep.rows_imported == 2 and rep.rows_skipped == 3
    costs = sorted(c.unit_cost for c in db.scalars(select(CostRecord)))
    assert costs == [9.0, 22.5]
    assert any("shared by several variants" in i["issue"] for i in rep.issues)
    with pytest.raises(ConflictError):
        import_costs(db, store, csv, "costs.csv", dry_run=False)


def test_costs_never_cross_store(db, store):
    _catalog(db, store)
    other = Store(code="OTH", name="Other")
    db.add(other)
    db.commit()
    rep = import_costs(db, other, b"sku,unit_cost\nJ-M,10\n", "c.csv", dry_run=False)
    assert rep.rows_imported == 0 and db.scalar(select(CostRecord)) is None


def test_traffic_csv(db, store):
    _catalog(db, store)
    csv = (b"Day,Sessions,Sessions with cart additions,Sessions that reached checkout,"
           b"Sessions that completed checkout,Product\n"
           b"2026-10-01,1000,80,40,25,\n2026-10-01,1000,80,40,25,\n2026-10-02,\"1,200\",90,45,30,\n"
           b"2026-10-02,300,20,10,5,jacket\n2026-10-03,10,1,1,50,\n")
    rep = import_traffic(db, store, csv, "traffic.csv", dry_run=False)
    assert rep.rows_imported == 4 and rep.rows_skipped == 1
    rows = db.scalars(select(TrafficDaily).order_by(TrafficDaily.day)).all()
    assert rows[1].sessions == 1200
    assert any(r.product_id for r in rows)
    assert any("exceed sessions" in i["issue"] for i in rep.issues)
    # re-import with force updates instead of duplicating
    import_traffic(db, store, csv, "traffic.csv", dry_run=False, force=True)
    assert len(db.scalars(select(TrafficDaily)).all()) == 4
