from datetime import date, timedelta

from fastapi.testclient import TestClient

from app.models import Integration
from app.services.offers_import import parse_level

TODAY = date.today()


def _client():
    from app.main import create_app

    return TestClient(create_app())


def test_plan_and_expired_split_and_auto_scheduled(db, store):
    c = _client()
    mk = lambda name, s, e, **kw: c.post("/api/events", json={"store_id": store.id, "name": name,
                                                               "start_date": str(s), "end_date": str(e), **kw}).json()
    future = mk("Future", TODAY + timedelta(days=5), TODAY + timedelta(days=9), level="collection",
                level_targets=["Halloween"], landing_page="https://x.com/collections/halloween", articles="Spooky picks")
    running = mk("Running", TODAY - timedelta(days=2), TODAY + timedelta(days=2), status="live")
    old = mk("Old", TODAY - timedelta(days=30), TODAY - timedelta(days=20), status="completed")
    assert future["status"] == "scheduled" and future["duration_days"] == 5  # future dates -> Scheduled
    assert running["status"] == "live"
    plan = [e["name"] for e in c.get(f"/api/events?store_id={store.id}&when=plan").json()]
    expired = [e["name"] for e in c.get(f"/api/events?store_id={store.id}&when=expired").json()]
    assert plan == ["Running", "Future"] and expired == ["Old"]
    assert c.get(f"/api/events/{future['id']}").json()["level_targets"] == ["Halloween"]
    assert old["code_active_after_end"] is False  # not verified in a store: never claimed


def test_banner_roundtrip_and_limits(db, store):
    c = _client()
    ev = c.post("/api/events", json={"store_id": store.id, "name": "Halloween", "display": "full_site"}).json()
    html = '<div id="banner-desktop" style="width:1975px;height:750px">Hi</div><script>alert(1)</script>'
    assert c.put(f"/api/events/{ev['id']}/banner", json={"kind": "html", "content": html, "filename": "b.html"}).status_code == 200
    listed = c.get(f"/api/events?store_id={store.id}").json()[0]
    assert listed["has_banner"] and "banner_content" not in listed  # lists stay light
    assert c.get(f"/api/events/{ev['id']}/banner").json()["content"] == html
    assert c.put(f"/api/events/{ev['id']}/banner", json={"kind": "image", "content": "not-an-image"}).status_code == 422


def test_overview_declares_missing_orders_for_eli_z(db, store):
    db.add(Integration(store_id=store.id, provider="elizeta", status="connected", external_shop_id="26"))
    db.commit()
    o = _client().get(f"/api/overview?store_id={store.id}&days=30").json()
    assert o["kpis"] is None and "Eli Z" in o["orders_missing"]


def test_overview_kpis_on_demo(db):
    from app.services.demo import reset_demo

    s = reset_demo(db, days=120)
    o = _client().get(f"/api/overview?store_id={s.id}&days=90").json()
    k = o["kpis"]
    assert k["orders"] > 0 and 0 < k["orders_with_offer_share"] < 1 and k["aov_with_discount"] and k["aov_without_discount"]
    assert len(o["top_codes"]) <= 10 and o["top_codes"][0]["orders"] >= o["top_codes"][-1]["orders"]
    assert [x["name"] for x in o["live_offers"]] == ["Autumn Opener"]
    perf = _client().get(f"/api/stores/{s.id}/offer-performance?days=730").json()["events"]
    assert all("store_code" in r and "duration_days" in r for r in perf)


def test_calendar_level_column():
    assert parse_level("Storewide / bestsellers") == ("store", [])
    assert parse_level("https://www.northfashionstore.com/collections/best-sellers") == ("collection", ["Best Sellers"])
    assert parse_level("https://x.com/products/spooky-ebook?variant=1") == ("product", ["Spooky Ebook"])
    assert parse_level("-") == (None, [])


def test_calendar_codes_and_status_from_real_cells():
    """Cells as written in the marketing calendar's Events sheet (MAU, Oct–Dec 2026)."""
    from app.services.offers_import import CALENDAR_STATUS, parse_codes

    assert parse_codes("SPOOKLAB (new) | OWLPASS (existing 15/20%)") == ["SPOOKLAB", "OWLPASS"]
    assert parse_codes("HEADSTART (new) | ROBINPASS (existing 22%)") == ["HEADSTART", "ROBINPASS"]
    assert parse_codes("N/A (automatic)") == [] and parse_codes("ONESTAR") == ["ONESTAR"]
    assert CALENDAR_STATUS.get("live") == "live" and "sent" not in CALENDAR_STATUS and "off" not in CALENDAR_STATUS


def test_level_targets_one_per_line_with_catalog_names():
    cell = ("https://www.thelittlelearnerstoys.com/products/photo-reel-viewfinder\n"
            "https://www.thelittlelearnerstoys.com/products/tiny-biker-anti-tip-light-sound-motorcycle\n"
            "Wishcard and Wrapping Paper")
    level, targets = parse_level(cell, {"tiny-biker-anti-tip-light-sound-motorcycle": "Tiny Biker Anti-Tip Light & Sound Motorcycle"})
    assert level == "product"
    assert targets == ["Photo Reel Viewfinder", "Tiny Biker Anti-Tip Light & Sound Motorcycle", "Wishcard and Wrapping Paper"]
