"""Live offer docs. Offer data are MAU's real Halloween events (calendar, 2 Nov 2026) and the
real Halloween banner file; product titles/ids are the owner's product-offers table."""

import io
from datetime import date

import pytest
from docx import Document
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.models import Product, PromoEvent, Store

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
    a = c.post("/api/extra-activities", json={"store_id": s.id, "name": "Extended Warranty", "price": "$8.95",
                                              "description": "+1 year of free replacements and support"})
    assert a.status_code == 201
    doc = c.post("/api/docs", json={"name": "MAU · Halloween 2026", "tags": ["Halloween"], "store_ids": [s.id],
                                    "start_date": "2026-10-25", "end_date": "2026-11-03",
                                    "focus": {"perf_tags": ["best_seller"], "manual_tags": ["q4 focus"]}}).json()
    d = c.get(f"/api/public/docs/{doc['token']}").json()
    names = [e["name"] for e in d["events"]]
    assert names == ["Tricks, Treats & Clever Feats", "Last Treats Standing"]  # Curious Club after range, Rainy Days completed
    t, lt = d["events"]
    assert t["display_text"] == "Home page, collection, product page" and t["banner"] is True
    assert t["threshold"] == "$99+" and t["activation"] == "Manual" and t["codes"] == ["SPOOKLAB", "OWLPASS"]
    assert t["bonus"]["free_gift"].startswith("Free Spooky Stories eBook")  # old single Bonus text until split
    assert lt["display_text"] == "Email only, not shown on the site" and lt["banner"] is False
    assert lt["email"][0] == ["Subject", "The treats are almost gone 🎃"] and lt["products"] == "Halloween"
    assert [p["name"] for p in d["product_offers"]] == ["Tiny Biker Anti-Tip Light & Sound Motorcycle"]
    assert d["product_offers"][0]["sku"] == "15348751499638"
    focus = {p["name"]: p for p in d["focus"]}
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
    assert t["forecast"] == {"orders": 40, "uses": None, "aov": "$120"}
    assert {"field": "Code", "old": "SPOOKLAB, OWLPASS", "new": "SPOOKLAB"} in t["changes"]["items"]
    # sections can be switched off
    c.put(f"/api/docs/{doc['id']}", json={**{k: doc[k] for k in ("name", "tags", "store_ids", "start_date", "end_date", "focus")},
                                          "sections": {"changes": False, "forecast": False}})
    t = c.get(f"/api/public/docs/{doc['token']}").json()["events"][0]
    assert t["changes"] is None and t["forecast"] is None


def test_rolling_range_and_statuses(db, c):
    _s, *_ = _mau(db)
    doc = c.post("/api/docs", json={"name": "Next week", "range_mode": "next_days", "days": 10,
                                    "statuses": ["scheduled"]}).json()
    assert doc["range"] == ["2026-10-27", "2026-11-05"]
    d = c.get(f"/api/public/docs/{doc['token']}").json()
    assert [e["name"] for e in d["events"]] == ["Last Treats Standing", "Curious Club: First Dibs"]


def test_public_link_needs_no_login_and_can_be_replaced(db, monkeypatch):
    monkeypatch.setenv("EGI_AUTH_MODE", "password")
    monkeypatch.setenv("EGI_AUTH_PASSWORD", "correct horse")
    from app.core.config import get_settings

    get_settings.cache_clear()
    from app.main import create_app

    _s, tricks, last = _mau(db)
    c = TestClient(create_app(), base_url="https://x")
    assert c.post("/api/docs", json={"name": "x"}).status_code == 401  # managing docs needs sign-in
    c.post("/api/auth/login", json={"password": "correct horse"})
    doc = c.post("/api/docs", json={"name": "MAU", "start_date": "2026-10-25", "end_date": "2026-11-03"}).json()
    anon = TestClient(create_app(), base_url="https://x")
    assert anon.get(f"/api/public/docs/{doc['token']}").status_code == 200
    assert anon.get("/api/docs").status_code == 401
    # banners only for offers in the doc
    assert anon.get(f"/api/public/docs/{doc['token']}/banner/{tricks.id}").json()["kind"] == "html"
    assert anon.get(f"/api/public/docs/{doc['token']}/banner/{last.id}").status_code == 404
    docx = anon.get(f"/api/public/docs/{doc['token']}/docx")
    text = "\n".join(p.text for p in Document(io.BytesIO(docx.content)).paragraphs)
    assert "Tricks, Treats & Clever Feats" in text and "Home page, collection, product page" in text
    new = c.post(f"/api/docs/{doc['id']}/new-link").json()
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
