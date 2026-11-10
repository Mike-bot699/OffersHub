"""Live offer docs. Offer data are MAU's real Halloween events (calendar, 2 Nov 2026) and the
real Halloween banner file; product titles/ids are the owner's product-offers table."""

import io
from datetime import date

import pytest
from docx import Document
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.models import Product, PromoEvent, Store, Variant

TODAY = date(2026, 10, 27)
BANNER = (__import__("pathlib").Path(__file__).parent / "fixtures" / "halloween_banner.html").read_text()


@pytest.fixture()
def c(db, monkeypatch):
    import app.api.routes_docs as rd
    import app.offers.status as st

    monkeypatch.setattr(st, "business_today", lambda: TODAY)
    monkeypatch.setattr(rd, "business_today", lambda: TODAY)
    from app.main import create_app

    return TestClient(create_app())


def _mau(db):
    s = Store(code="MAU", name="Little Learners Toys", currency="USD", timezone="Europe/London",
              product_host="www.thelittlelearnerstoys.com")
    db.add(s)
    db.flush()
    tricks = PromoEvent(store_id=s.id, name="Tricks, Treats & Clever Feats", source="spreadsheet_import", status="live",
                        start_date=date(2026, 10, 25), end_date=date(2026, 11, 1), display="full_site", level="store",
                        offer_text="15% off $99+ (new) · 20% off $99+ (existing)", coupon_codes=["SPOOKLAB", "OWLPASS"],
                        threshold_amount=99, activation="manual_code", tags=["Halloween"],
                        bonus="Free Spooky Stories eBook over $120 — bedtime reading included.",
                        banner_kind="html", banner_content=BANNER, banner_filename="Halloween_Banner_Desktop.dc.html")
    last = PromoEvent(store_id=s.id, name="Last Treats Standing", source="spreadsheet_import", status="scheduled",
                      start_date=date(2026, 11, 1), end_date=date(2026, 11, 3), display="email_only", level="collection",
                      level_targets=["Halloween"], offer_text="15% off entire order (final 72h)", coupon_codes=["SPOOKLAB"],
                      threshold_amount=99, activation="manual_code",
                      banner_message="Subject: The treats are almost gone 🎃 | Preheader: 72 hours left on 15% off — then the cauldron closes | CTA: Grab the Last Treats")
    curious = PromoEvent(store_id=s.id, name="Curious Club: First Dibs", source="spreadsheet_import", status="scheduled",
                         start_date=date(2026, 11, 4), end_date=date(2026, 11, 10), display="full_site", coupon_codes=["FIRSTDIBS"])
    old = PromoEvent(store_id=s.id, name="Rainy Days, Bright Ideas", source="spreadsheet_import", status="completed",
                     start_date=date(2026, 10, 8), end_date=date(2026, 10, 11))
    biker = PromoEvent(store_id=s.id, name="Tiny Biker bundle", source="manual", status="in_progress", level="product",
                       level_targets=["Tiny Biker Anti-Tip Light & Sound Motorcycle"], offer_text="ComicBook - Tiny Rider Says Hello!")
    db.add_all([tricks, last, curious, old, biker])
    db.add_all([
        Product(store_id=s.id, source="elizeta", source_id="15348751499638", title="Tiny Biker Anti-Tip Light & Sound Motorcycle",
                handle="tiny-biker-anti-tip-light-sound-motorcycle", status="ACTIVE", perf_tag="best_seller", perf_rank=1,
                manual_tags=["Q4 Focus"]),
        Product(store_id=s.id, source="elizeta", source_id="10218824958283", title="ChatGPT Powered STEM Learning and Playing Robot",
                handle="chatgpt-powered-stem-learning-and-playing-robot", status="ACTIVE", perf_tag="popular", perf_rank=40),
        Product(store_id=s.id, source="elizeta", source_id="15822701396342", title="RC Paper Airplane", handle="rc-paper-airplane",
                status="ACTIVE", perf_tag=None, manual_tags=["Q4 Focus"]),
        Product(store_id=s.id, source="elizeta", source_id="15125585166710", title="TurboRace Interactive Game Set with 200 Challenges",
                handle="turborace", status="NOT_ACTIVE", perf_tag="best_seller", perf_rank=2),
    ])
    db.commit()
    return s, tricks, last


def test_doc_content_follows_the_plan_live(db, c):
    s, tricks, _last = _mau(db)
    biker = db.scalar(select(Product).where(Product.title.startswith("Tiny Biker")))
    db.add_all([Variant(store_id=s.id, product_id=biker.id, source="elizeta", source_id=f"v{i}", sku=sku)
                for i, sku in enumerate(["", "DES12-15348751400017.1", "DES12-15348751400017.2"])])
    db.commit()
    a = c.post("/api/extra-activities", json={"store_id": s.id, "name": "Extended Warranty", "price": "$8.95",
                                              "description": "+1 year of free replacements and support"})
    assert a.status_code == 201
    doc = c.post("/api/offer-docs", json={"name": "MAU · Halloween 2026", "tags": ["Halloween"], "store_ids": [s.id],
                                    "start_date": "2026-10-25", "end_date": "2026-11-03",
                                    "focus": {"perf_tags": ["best_seller"], "manual_tags": ["q4 focus"]}}).json()
    d = c.get(f"/api/public/docs/{doc['token']}").json()
    listed = c.get("/api/offer-docs").json()
    assert isinstance(listed, list) and listed[0]["name"] == "MAU · Halloween 2026"  # not shadowed by the API docs page
    names = [e["name"] for e in d["events"]]
    assert names == ["Tricks, Treats & Clever Feats", "Last Treats Standing"]  # Curious Club after range, Rainy Days completed
    t, lt = d["events"]
    assert t["display_text"] == "Home page, collection, product page" and t["banner"] is True
    assert t["threshold"] == "$99+" and t["activation"] == "Manual" and t["codes"] == ["SPOOKLAB", "OWLPASS"]
    assert t["bonus"]["free_gift"].startswith("Free Spooky Stories eBook")  # old single Bonus text until split
    assert lt["display_text"] == "Email only, not shown on the site" and lt["banner"] is False
    assert lt["emails"][0]["subject"] == "The treats are almost gone 🎃" and lt["products"] == "Halloween"
    assert [p["name"] for p in d["product_offers"]] == ["Tiny Biker Anti-Tip Light & Sound Motorcycle"]
    # the SKU comes from the variants, never the platform product id (15348751499638)
    assert d["product_offers"][0]["sku"] == "15348751400017"  # DES12-....1 / .2: the middle number, once (not the product id)
    focus = {p["name"]: p for p in d["focus"]}
    assert focus["Tiny Biker Anti-Tip Light & Sound Motorcycle"]["sku"] == "15348751400017"
    assert focus["RC Paper Airplane"]["sku"] is None  # no SKU on the variants: shown as "-", not the product id
    assert set(focus) == {"Tiny Biker Anti-Tip Light & Sound Motorcycle", "RC Paper Airplane"}  # not-active excluded
    assert focus["Tiny Biker Anti-Tip Light & Sound Motorcycle"]["url"] == \
        "https://www.thelittlelearnerstoys.com/products/tiny-biker-anti-tip-light-sound-motorcycle"
    assert d["extra_activities"][0]["price"] == "$8.95"
    # live: an edit in OffersHub shows on the same link, with the change spotlighted
    c.patch(f"/api/events/{tricks.id}", json={"offer_description": "New customers get 15% off orders over $99 with code SPOOKLAB.",
                                              "coupon_codes": ["SPOOKLAB"], "ad_channels": ["facebook", "email", "email"],
                                              "forecast_orders": 40, "forecast_aov": 120})
    t = c.get(f"/api/public/docs/{doc['token']}").json()["events"][0]
    assert t["offer"].startswith("New customers get 15% off")
    assert [x["name"] for x in t["channels"]] == ["Facebook", "Email"]
    assert t["forecast"] == {"orders": 40, "uses": None, "aov": "$120", "estimate": False}
    assert [c["text"] for c in t["changes"]["items"]] == ["code SPOOKLAB, OWLPASS → SPOOKLAB"]  # first fills are not changes
    # sections can be switched off
    c.put(f"/api/offer-docs/{doc['id']}", json={**{k: doc[k] for k in ("name", "tags", "store_ids", "start_date", "end_date", "focus")},
                                          "sections": {"changes": False, "forecast": False}})
    t = c.get(f"/api/public/docs/{doc['token']}").json()["events"][0]
    assert t["changes"] is None and t["forecast"] is None


def test_short_sku_is_the_middle_number():
    from app.offers.docs import short_sku

    assert short_sku("DES12-15125585166710.1") == "15125585166710"
    assert short_sku("DES12-15125585166710") == "15125585166710"
    assert short_sku("15125585166710.3") == "15125585166710"
    assert short_sku("  ") is None and short_sku(None) is None


def test_rolling_range_and_statuses(db, c):
    _s, *_ = _mau(db)
    doc = c.post("/api/offer-docs", json={"name": "Next week", "range_mode": "next_days", "days": 10,
                                    "statuses": ["scheduled"]}).json()
    assert doc["range"] == ["2026-10-27", "2026-11-05"]
    d = c.get(f"/api/public/docs/{doc['token']}").json()
    assert [e["name"] for e in d["events"]] == ["Last Treats Standing", "Curious Club: First Dibs"]
    db.get(PromoEvent, d["events"][0]["id"]).status = None
    db.commit()
    assert [e["name"] for e in c.get(f"/api/public/docs/{doc['token']}").json()["events"]] == ["Curious Club: First Dibs"]
    c.put(f"/api/offer-docs/{doc['id']}", json={"name": "Next week", "range_mode": "next_days", "days": 10, "statuses": ["scheduled", "not_set"]})
    assert len(c.get(f"/api/public/docs/{doc['token']}").json()["events"]) == 2


def test_public_link_needs_no_login_and_can_be_replaced(db, monkeypatch):
    monkeypatch.setenv("EGI_AUTH_MODE", "password")
    monkeypatch.setenv("EGI_AUTH_PASSWORD", "correct horse")
    from app.core.config import get_settings

    get_settings.cache_clear()
    from app.main import create_app

    _s, tricks, last = _mau(db)
    c = TestClient(create_app(), base_url="https://x")
    assert c.post("/api/offer-docs", json={"name": "x"}).status_code == 401  # managing docs needs sign-in
    c.post("/api/auth/login", json={"password": "correct horse"})
    doc = c.post("/api/offer-docs", json={"name": "MAU", "start_date": "2026-10-25", "end_date": "2026-11-03"}).json()
    anon = TestClient(create_app(), base_url="https://x")
    assert anon.get(f"/api/public/docs/{doc['token']}").status_code == 200
    assert anon.get("/api/offer-docs").status_code == 401
    # banners only for offers in the doc
    assert anon.get(f"/api/public/docs/{doc['token']}/banner/{tricks.id}").json()["kind"] == "html"
    assert anon.get(f"/api/public/docs/{doc['token']}/banner/{last.id}").status_code == 404
    docx = anon.get(f"/api/public/docs/{doc['token']}/docx")
    text = "\n".join(p.text for p in Document(io.BytesIO(docx.content)).paragraphs)
    assert "Tricks, Treats & Clever Feats" in text and "Home page, collection, product page" in text
    new = c.post(f"/api/offer-docs/{doc['id']}/new-link").json()
    assert anon.get(f"/api/public/docs/{doc['token']}").status_code == 404  # old link stops working
    assert anon.get(f"/api/public/docs/{new['token']}").status_code == 200
    get_settings.cache_clear()


def test_import_records_changes(db):
    """Re-importing the calendar logs what changed (real case: Tricks, Treats end date 31 Oct → 1 Nov)."""
    import openpyxl

    from app.models import EventChange
    from app.services.offers_import import import_offers_workbook

    def book(end, offer):
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Events"
        ws.append(["Start Date", "End Date", "Event Name", "Store", "🏷️ Offer", "🎟️ Coupon"])
        ws.append([__import__("datetime").datetime(2026, 10, 25), end, "🎃 Tricks, Treats & Clever Feats", "🧸 MAU", offer, "SPOOKLAB"])
        buf = io.BytesIO()
        wb.save(buf)
        return buf.getvalue()

    dt = __import__("datetime").datetime
    import_offers_workbook(db, book(dt(2026, 10, 31), "15% off entire order"), "a.xlsx", dry_run=False, today=TODAY)
    db.commit()
    import_offers_workbook(db, book(dt(2026, 11, 1), "15% off $99+ (new) · 20% off $99+ (existing)"), "b.xlsx",
                           dry_run=False, today=TODAY)
    db.commit()
    ch = {c.field: (c.old, c.new) for c in db.scalars(select(EventChange))}
    assert ch["end_date"] == ("2026-10-31", "2026-11-01")
    assert ch["offer_text"][0] == "15% off entire order"


def test_offer_sentence_from_the_calendar_coupon_cell():
    """Real MAU Coupon cells (calendar, Nov 2026)."""
    from app.offers.offer_text import describe

    assert describe("SPOOKLAB (15 %new) | OWLPASS (existing 20%)", 99, "full_site") == (
        "New customers get 15% off orders over $99 with code SPOOKLAB, shown on the site. "
        "Email subscribers only get 20% off orders over $99 with code OWLPASS.")
    assert describe("LASTPOST (15% + FS, $49+) | TREETIME (existing 20% + FS)", 49, "full_site").startswith(
        "Customers get 15% off orders over $49 plus free shipping with code LASTPOST, shown on the site.")
    assert "get $25 off orders over $99 with code JANSPARK" in describe(
        "NEWSPARK (new 15%) | JANSPARK (existing $25 off $99 · B-cell: credit)", 99, "full_site")
    assert describe("SPOOKLAB (reused, already live)", 99, "email_only") is None  # value unknown: not guessed
    # one code, one value in the Offer text (The Week Before the Week)
    assert describe("EBF20", 99, "full_site", None, "20% off $99+") == \
        "Customers get 20% off orders over $99 with code EBF20, shown on the site."
    assert describe("EBF20", 99, "full_site", None, "20% off (new) · 25% off (existing)") is None  # two values: not paired
    # no cell, or a cell with old codes: the Offer text, one part per code (Curious Club as edited in OffersHub)
    expected = ("New customers get 15% off orders over $99 with code FIRSTDIBS, shown on the site. "
                "Email subscribers only get 20% off orders over $99 with code CLUBDIBS.")
    assert describe(None, 99, "full_site", None, "15% off $99+ (new). 20% off $99+ (email)", ["FIRSTDIBS", "CLUBDIBS"]) == expected
    assert describe("FIRSTPEEK (new 15%) | ACORNPASS (existing 20%)", 99, "full_site", None,
                    "15% off $99+ (new, banner) · 20% off $99+ (existing, email)", ["FIRSTDIBS", "CLUBDIBS"]) == expected
    assert describe(None, 99, "full_site", None, "20% off (new) · 25% off (existing)", ["BF2026"]) is None  # 2 parts, 1 code
    assert describe("SPOOKLAB (reused, already live)", 99, "email_only",
                    {"SPOOKLAB": {"value": 15.0, "value_type": "percent"}}) == "Customers get 15% off orders over $99 with code SPOOKLAB."


def test_doc_uses_written_description_then_generated_then_offer_text(db, c):
    _s, tricks, last = _mau(db)
    tricks.coupon_raw = "SPOOKLAB (15 %new) | OWLPASS (existing 20%)"
    db.commit()
    doc = c.post("/api/offer-docs", json={"name": "x", "start_date": "2026-10-25", "end_date": "2026-11-03"}).json()
    ev = {e["name"]: e for e in c.get(f"/api/public/docs/{doc['token']}").json()["events"]}
    assert ev["Tricks, Treats & Clever Feats"]["offer"].startswith("New customers get 15% off orders over $99")
    assert ev["Last Treats Standing"]["offer"] == "Customers get 15% off orders over $99 with code SPOOKLAB."  # from its Offer text
    assert ev["Last Treats Standing"]["emails"] == [{"subject": "The treats are almost gone 🎃",
                                                     "preview": "72 hours left on 15% off — then the cauldron closes"}]
    c.patch(f"/api/events/{last.id}", json={"email_variants": [{"subject": "A", "preview": "a"}, {"subject": "B", "preview": ""},
                                                                {"subject": " ", "preview": ""}]})
    c.patch(f"/api/events/{tricks.id}", json={"offer_description": "Written by hand."})
    ev = {e["name"]: e for e in c.get(f"/api/public/docs/{doc['token']}").json()["events"]}
    assert [m["subject"] for m in ev["Last Treats Standing"]["emails"]] == ["A", "B"]  # empty rows dropped
    assert ev["Tricks, Treats & Clever Feats"]["offer"] == "Written by hand."


def test_events_get_their_share_of_the_period_forecast(db, c):
    """Real case: MAU November 2026, EUR 1,994,345 at 1.1179 = $2,229,478, AOV $105. Each event shows the
    orders and revenue of its days in that period; last year's code estimates are no longer shown."""
    s, *_ = _mau(db)
    bf = PromoEvent(store_id=s.id, name="Black Friday for Big Ideas", status="scheduled", source="manual", display="full_site",
                    start_date=date(2026, 11, 23), end_date=date(2026, 11, 29), coupon_codes=["BF2026", "FOXCLUB"],
                    compare_codes=["BF2025"])
    cy = PromoEvent(store_id=s.id, name="Cyber Monday, Curious Clicks", status="scheduled", source="manual", display="full_site",
                    start_date=date(2026, 11, 30), end_date=date(2026, 12, 2), coupon_codes=["CYBER20"], compare_codes=["CYBER15"])
    ny = PromoEvent(store_id=s.id, name="24 Little Wonders", status="scheduled", source="manual", display="email_only",
                    start_date=date(2026, 12, 1), end_date=date(2026, 12, 24), compare_codes=["SOLO22"])
    db.add_all([bf, cy, ny])
    db.commit()
    fc = {"label": "November 2026", "start": "2026-11-01", "end": "2026-11-30", "revenue": 1994345, "currency": "EUR",
          "fx_rate": 1.1179, "aov": 105}
    doc = c.post("/api/offer-docs", json={"name": "x", "start_date": "2026-11-20", "end_date": "2026-12-31"}).json()
    ev = {e["name"]: e for e in c.get(f"/api/public/docs/{doc['token']}").json()["events"]}
    assert all(e["forecast"] is None for e in ev.values())  # no period forecast: nothing, not last year's codes
    c.put(f"/api/offer-docs/{doc['id']}", json={"name": "x", "start_date": "2026-11-20", "end_date": "2026-12-31", "forecast": fc})
    ev = {e["name"]: e for e in c.get(f"/api/public/docs/{doc['token']}").json()["events"]}
    assert ev["Black Friday for Big Ideas"]["forecast"] == {
        "orders": 4954, "uses": None, "aov": "$105", "revenue": "$520,212", "estimate": True,
        "basis": "7 of 30 days of the November 2026 forecast"}  # 2,229,478.28 x 7/30 = 520,211.6; / 105
    cyber = ev["Cyber Monday, Curious Clicks"]["forecast"]
    assert cyber["orders"] == 708 and cyber["revenue"] == "$74,316"  # only 30 Nov is in November
    assert cyber["basis"] == "1 of 30 days of the November 2026 forecast (2 event days outside it)"
    assert ev["24 Little Wonders"]["forecast"] is None  # all in December: outside the forecast's period
    c.patch(f"/api/events/{bf.id}", json={"forecast_orders": 1400})
    ev = {e["name"]: e for e in c.get(f"/api/public/docs/{doc['token']}").json()["events"]}
    assert ev["Black Friday for Big Ideas"]["forecast"]["estimate"] is False  # typed forecast wins
    assert ev["Black Friday for Big Ideas"]["forecast"]["orders"] == 1400


def test_changes_box_starts_at_doc_creation_and_can_be_reset(db, c):
    """Real case: free-gift thresholds updated while preparing the doc must not show as news."""
    _s, tricks, _last = _mau(db)
    c.patch(f"/api/events/{tricks.id}", json={"free_gift": "Spooky Stories eBook over $150"})  # before the doc exists
    c.patch(f"/api/events/{tricks.id}", json={"free_gift": "Spooky Stories eBook over $130"})
    doc = c.post("/api/offer-docs", json={"name": "x", "start_date": "2026-10-25", "end_date": "2026-11-03"}).json()
    ev = lambda: {e["name"]: e for e in c.get(f"/api/public/docs/{doc['token']}").json()["events"]}
    assert ev()["Tricks, Treats & Clever Feats"]["changes"] is None
    c.patch(f"/api/events/{tricks.id}", json={"free_gift": "Spooky Stories eBook over $120"})  # after: news
    assert ev()["Tricks, Treats & Clever Feats"]["changes"]["items"][0]["text"] == \
        "bonus Spooky Stories eBook over $130 → Spooky Stories eBook over $120"
    c.post(f"/api/offer-docs/{doc['id']}/reset-changes")
    assert ev()["Tricks, Treats & Clever Feats"]["changes"] is None


def test_banner_check_against_the_offer(db, c):
    """Real banners: the Halloween design matches its offer; the outdated design of The Week Before
    the Week (text as on the owner's screenshot) still says HEADSTART 15%."""
    s, tricks, last = _mau(db)
    tricks.coupon_raw = "SPOOKLAB (15 %new) | OWLPASS (existing 20%)"
    old = ('<p>Desktop</p><div id="banner-desktop" style="width:1975px;height:750px"><span>The week before the week</span>'
           '<h1>Beat the Rush, Keep the Calm</h1><p>Extra 15% off with code <b>HEADSTART</b></p><p>The quiet way to do Black Friday.</p>'
           '<a>Start Early ›</a><small>On orders over $99 · Ends Sun 22 Nov, 11:59pm · Free gift over $150</small></div>')
    week = PromoEvent(store_id=s.id, name="The Week Before the Week", status="scheduled", source="manual", display="full_site",
                      start_date=date(2026, 11, 16), end_date=date(2026, 11, 22), coupon_codes=["EBF20"], offer_text="20% off $99+",
                      threshold_amount=99, bonus="Free gift tucked in over $150.", banner_kind="html", banner_content=old)
    db.add(week)
    db.commit()
    assert c.get(f"/api/events/{tricks.id}/banner-check").json()["findings"] == []
    f = c.get(f"/api/events/{week.id}/banner-check").json()["findings"]
    assert any("HEADSTART" in x for x in f) and any("15%" in x for x in f) and any("EBF20" in x for x in f)
    assert not any("$150" in x or "22 Nov" in x for x in f)  # bonus threshold and end date are right
    week.end_date = date(2026, 11, 23)
    db.commit()
    assert any("ends 22 Nov" in x for x in c.get(f"/api/events/{week.id}/banner-check").json()["findings"])
    assert c.get(f"/api/events/{last.id}/banner-check").json()["readable"] is False  # no banner


def test_period_forecast_from_revenue_and_aov(db, c):
    """Real case: MAU November 2026, EUR 1,994,345 at the ECB rate of 3 Nov 2026 (1.1179), forecast AOV $105."""
    s, _t, _l = _mau(db)
    base = {"name": "MAU · November 2026", "store_ids": [s.id], "start_date": "2026-11-01", "end_date": "2026-11-30"}
    fc = {"label": "November 2026", "revenue": 1994345, "currency": "EUR", "fx_rate": 1.1179, "fx_note": "ECB, 3 Nov 2026",
          "aov": 105, "current_aov": 115, "growth_pct": 40}
    doc = c.post("/api/offer-docs", json={**base, "forecast": fc}).json()
    assert doc["forecast"]["revenue"] == 1994345 and doc["forecast"]["currency"] == "EUR"
    pf = c.get(f"/api/public/docs/{doc['token']}").json()["period_forecast"]
    assert pf["revenue"] == "$2,229,478" and pf["revenue_usd"] == 2229478.28
    assert pf["orders"] == 21233  # 2,229,478.28 / 105
    assert pf["original"] == "EUR 1,994,345 at 1.1179 (ECB, 3 Nov 2026)"
    assert pf["aov"] == "$105" and pf["current_aov"] == "$115" and pf["growth"] == "+40% vs last year"
    text = "\n".join(p.text for p in Document(io.BytesIO(c.get(f"/api/public/docs/{doc['token']}/docx").content)).paragraphs)
    assert "Forecast:" not in text.split("Events")[0]  # no period box at the top any more
    # EUR without a rate: declared, never converted at 1:1
    c.put(f"/api/offer-docs/{doc['id']}", json={**base, "forecast": {**fc, "fx_rate": None}})
    pf = c.get(f"/api/public/docs/{doc['token']}").json()["period_forecast"]
    assert pf == {"label": "November 2026", "missing": "Revenue is in EUR: add the EUR→USD rate."}
    # no AOV: revenue only, no orders estimate
    c.put(f"/api/offer-docs/{doc['id']}", json={**base, "forecast": {"revenue": 2000000}})
    pf = c.get(f"/api/public/docs/{doc['token']}").json()["period_forecast"]
    assert pf["revenue"] == "$2,000,000" and pf["orders"] is None and pf["original"] is None
    # section switched off or nothing typed: no box
    c.put(f"/api/offer-docs/{doc['id']}", json={**base, "forecast": {}})
    assert c.get(f"/api/public/docs/{doc['token']}").json()["period_forecast"] is None
