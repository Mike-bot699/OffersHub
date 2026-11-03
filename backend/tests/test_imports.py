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
    # results recorded in the workbook -> completed, with the reason kept in the history
    assert autumn.status == "completed" and autumn.offer_type == "percentage_discount"
    assert autumn.status_history[-1]["source"] == "import" and "results" in autumn.status_history[-1]["note"]
    assert autumn.technical_status == "unknown"  # no connected store: not verified

    trick = events["Trick or Treat"]
    assert trick.threshold_amount == 99 and trick.activation == "automatic"
    assert "mystery_gift" in db.get(Offer, trick.offer_id).mechanic["components"]

    assert any("identical to row" in i for i in events["Copy row"].data_issues)
    broken = events["Broken"].data_issues
    assert any("before start date" in i for i in broken)
    assert any("exceed total orders" in i for i in broken)
    assert events["Broken"].offer_type == "quantity_incentive"
    assert events["Placeholder"].status is None  # nothing proves its stage: left for review
    assert events["Not launched"].status == "cancelled"

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


def test_reimport_matches_changed_events_and_keeps_known_status(db):
    """Real MAU calendar change (Oct → Nov 2026): codes and dates changed, one event renamed, one
    dropped, one new; an unrecognised calendar status ('🔴 Off') never erases a known status."""
    import io

    import openpyxl
    from sqlalchemy import select

    from app.models import PromoEvent
    from app.services.offers_import import import_offers_workbook

    head = ["Start Date", "End Date", "Event Name", "Store", "Status", "🏷️ Offer", "🎟️ Coupon"]

    def book(rows):
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Events"
        ws.append(head)
        for r in rows:
            ws.append(r)
        buf = io.BytesIO()
        wb.save(buf)
        return buf.getvalue()

    d = lambda s: __import__("datetime").datetime.fromisoformat(s)
    october = book([
        [d("2026-10-25"), d("2026-10-31"), "🎃 Tricks, Treats & Clever Feats", "MAU", "🟢 Live", "15% off", "SPOOKLAB | OWLPASS"],
        [d("2026-11-04"), d("2026-11-10"), "⚫ The Curious Club Goes First", "MAU", None, "25% off", "FIRSTPEEK, ACORNPASS"],
        [d("2026-11-20"), d("2026-11-26"), "⚫ Clever Shoppers Go Early", "MAU", None, "15% off", "HEADSTART, ROBINPASS"],
        [d("2026-11-27"), d("2026-11-29"), "⚫ Black Friday for Big Ideas", "MAU", None, "20% off", "BIGIDEAS, FOXCLUB"],
    ])
    november = book([
        [d("2026-10-25"), d("2026-11-01"), "🎃 Tricks, Treats & Clever Feats", "🧸 MAU", "🔴 Off", "15% off", "SPOOKLAB"],
        [d("2026-11-01"), d("2026-11-03"), "🎃 Last Treats Standing", "🧸 MAU", None, "15% off", "SPOOKLAB"],
        [d("2026-11-04"), d("2026-11-10"), "⚫ Curious Club: First Dibs", "🧸 MAU", None, "20% off", "FIRSTDIBS, CLUBDIBS"],
        [d("2026-11-23"), d("2026-11-29"), "⚫ Black Friday for Big Ideas", "🧸 MAU", None, "20% off", "BF2026, FOXCLUB"],
    ])
    today = __import__("datetime").date(2026, 10, 30)
    import_offers_workbook(db, october, "MAU-events-plan.xlsx", dry_run=False, create_missing_stores=True, today=today)
    db.commit()
    preview = import_offers_workbook(db, november, "MAU-events-update.xlsx", dry_run=True, today=today)
    assert (preview.events_created, preview.events_updated) == (1, 3)
    assert {(m["event"], m["by"]) for m in preview.matched_changed} >= {("Curious Club: First Dibs", "start date"),
                                                                         ("Black Friday for Big Ideas", "name")}
    assert [m["event"] for m in preview.not_in_file] == ["Clever Shoppers Go Early"]
    import_offers_workbook(db, november, "MAU-events-update.xlsx", dry_run=False, today=today)
    db.commit()
    evs = {e.name: e for e in db.scalars(select(PromoEvent))}
    assert len(evs) == 5 and "The Curious Club Goes First" not in evs  # renamed in place, no duplicates
    assert evs["Black Friday for Big Ideas"].coupon_codes == ["BF2026", "FOXCLUB"]
    assert evs["Tricks, Treats & Clever Feats"].status == "live"  # 'Off' not recognised: Live kept
    assert evs["Clever Shoppers Go Early"].status == "scheduled"   # dropped from the file: left as is


def test_displayed_top_event_and_unknown_values(db):
    """Real calendar values: '👑 Top Event' is Full-Displayed; an unknown one is reported, not dropped silently."""
    import io

    import openpyxl
    from sqlalchemy import select

    from app.models import PromoEvent
    from app.services.offers_import import import_offers_workbook

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Events"
    ws.append(["Start Date", "End Date", "Event Name", "Store", "🏷️ Offer", "🖥️ Displayed"])
    d = __import__("datetime").datetime
    ws.append([d(2026, 10, 25), d(2026, 11, 1), "🎃 Tricks, Treats & Clever Feats", "🧸 MAU", "15% off", "👑 Top Event"])
    ws.append([d(2026, 11, 4), d(2026, 11, 10), "⚫ Curious Club: First Dibs", "🧸 MAU", "20% off", "🛸 Popup only"])
    buf = io.BytesIO()
    wb.save(buf)
    rep = import_offers_workbook(db, buf.getvalue(), "MAU.xlsx", dry_run=False, today=__import__("datetime").date(2026, 10, 30))
    evs = {e.name: e for e in db.scalars(select(PromoEvent))}
    assert evs["Tricks, Treats & Clever Feats"].display == "full_site"  # same thing as Full-Displayed
    assert evs["Curious Club: First Dibs"].display is None
    assert any("Displayed 'Popup only' not recognised" in i["issue"] for i in rep.issues)


def test_consistency_warnings_on_real_calendar_rows():
    """MAU calendar rows as they were on 2 Nov 2026 (before the owner's corrections)."""
    from datetime import date

    from app.services.offers_import import consistency_issues, parse_codes

    def check(start, end, offer, coupon, thr, desc, banner):
        return consistency_issues(start=start, end=end, offer=offer, coupon_raw=coupon, coupons=parse_codes(coupon),
                                  threshold=thr, description=desc, banner=banner)

    grand = check(date(2026, 11, 13), date(2026, 11, 15), "15% off $99+ + free gift wrap & card over $120 (new)", "GRANDLOVE", 99,
                  "The grandparent week keeps its audience",
                  "desc: 15% off orders over $99 with code NANAPICKS — we wrap it | footer: Ends Thu 19 Nov, 11:59pm")
    assert any("NANAPICKS" in i for i in grand) and any("Ends Thu 19 Nov" in i for i in grand)
    bf = check(date(2026, 11, 23), date(2026, 11, 29), "20% off $99+ (new) · 25% off $99+ (existing specific segment)",
               "BF2026 (new 20%) | FOXCLUB (existing 25%)", 99, "Three days at full depth", "code BIGIDEAS — three days")
    assert any("BIGIDEAS" in i for i in bf) and any("'Three days' but the offer runs 7 days" in i for i in bf)
    cyber = check(date(2026, 11, 30), date(2026, 12, 2), "20% off $99+ + free shipping (new) · 25% off $99+ + free shipping",
                  "CYBER20 / CM20 / CM2026 (new 15% + FS) | ROBOTCLUB (existing 25% + FS)", 99, "Online-only, three days", "")
    assert cyber == ["Coupon column says 15%, the Offer column does not"]
    # corrected rows and gift thresholds raise nothing
    assert check(date(2026, 12, 1), date(2026, 12, 24), "No discount. Daily content series.", "-", None,
                 "Twenty-four days of one small idea a day", "") == []
    assert check(date(2026, 9, 21), date(2026, 10, 4), "Extra 20% OFF storewide on orders $99+ + Free Gift Box on $149+",
                 "LLT20", 99, "", "") == []


def test_advertising_column(db):
    """Calendar 'Advertising' column: Meta is stored as Facebook; a calendar without the column changes nothing."""
    import io

    import openpyxl
    from sqlalchemy import select

    from app.models import PromoEvent
    from app.services.offers_import import import_offers_workbook, parse_ad_channels

    assert parse_ad_channels("Email, Meta, Google") == ["facebook", "email", "google"]
    assert parse_ad_channels("Email") == ["email"]

    def book(with_ads):
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Events"
        ws.append(["Start Date", "End Date", "Event Name", "Store", "🏷️ Offer", *(["Advertising"] if with_ads else [])])
        d = __import__("datetime").datetime
        ws.append([d(2026, 11, 23), d(2026, 11, 29), "⚫ Black Friday for Big Ideas", "🧸 MAU", "20% off $99+",
                   *(["Email, Meta, Google"] if with_ads else [])])
        buf = io.BytesIO()
        wb.save(buf)
        return buf.getvalue()

    today = __import__("datetime").date(2026, 11, 3)
    import_offers_workbook(db, book(True), "a.xlsx", dry_run=False, today=today)
    db.commit()
    ev = db.scalar(select(PromoEvent))
    assert ev.ad_channels == ["facebook", "email", "google"]
    import_offers_workbook(db, book(False), "b.xlsx", dry_run=False, today=today)
    db.commit()
    db.refresh(ev)
    assert ev.ad_channels == ["facebook", "email", "google"]  # no column: left as it was


def test_partial_file_updates_only_its_columns(db):
    """Real case: adding Advertising to MAU events must not undo edits made in OffersHub (bonus threshold, codes)."""
    import io

    import openpyxl
    from sqlalchemy import select

    from app.models import PromoEvent
    from app.services.offers_import import import_offers_workbook

    d = __import__("datetime").datetime
    today = __import__("datetime").date(2026, 11, 3)

    def book(head, row):
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Events"
        ws.append(head)
        ws.append(row)
        buf = io.BytesIO()
        wb.save(buf)
        return buf.getvalue()

    full = book(["Start Date", "End Date", "Event Name", "Store", "🏷️ Offer", "🎟️ Coupon", "🚫 Threshold", "Bonus"],
                [d(2026, 11, 23), d(2026, 11, 29), "⚫ Black Friday for Big Ideas", "🧸 MAU", "20% off $99+",
                 "BF2026 (new 20%) | FOXCLUB (existing 25%)", "$99.00", "Free gift in every box over $120."])
    import_offers_workbook(db, full, "full.xlsx", dry_run=False, create_missing_stores=True, today=today)
    db.commit()
    ev = db.scalar(select(PromoEvent))
    ev.bonus = "Free gift in every box over $150."  # edited in OffersHub after the import
    db.commit()
    ads = book(["Start Date", "End Date", "Event Name", "Store", "Advertising"],
               [d(2026, 11, 23), d(2026, 11, 29), "⚫ Black Friday for Big Ideas", "🧸 MAU", "Email, Meta, Google"])
    rep = import_offers_workbook(db, ads, "ads.xlsx", dry_run=False, today=today)
    db.commit()
    db.refresh(ev)
    assert (rep.events_created, rep.events_updated) == (0, 1)
    assert ev.ad_channels == ["facebook", "email", "google"]
    assert ev.coupon_codes == ["BF2026", "FOXCLUB"] and ev.offer_text == "20% off $99+" and ev.threshold_amount == 99
    assert ev.bonus == "Free gift in every box over $150."
