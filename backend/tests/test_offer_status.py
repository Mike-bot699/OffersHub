from datetime import UTC, date, datetime, timedelta

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from app.core.errors import ValidationError
from app.db.base import utcnow
from app.models import Coupon, Integration, PromoEvent, SyncState
from app.offers.status import acknowledge_conflict, describe, refresh, set_status, sync_store_offers

TODAY = date.today()


def _connected(db, store):
    db.add(Integration(store_id=store.id, provider="shopify", status="connected", granted_scopes=["read_discounts"]))
    db.add(SyncState(store_id=store.id, resource="discounts", last_success_at=utcnow()))
    db.flush()


def _coupon(db, store, code, status, sid="gid://shopify/DiscountCodeNode/1", **kw):
    c = Coupon(store_id=store.id, code=code, source="shopify", source_id=sid, status=status, value_type="percent",
               value=10, title=kw.pop("title", f"{code} title"), last_synced_at=utcnow(), channel="code", **kw)
    db.add(c)
    db.flush()
    return c


def _event(db, store, status="in_progress", codes=("FALL10",), **kw):
    ev = PromoEvent(store_id=store.id, name=kw.pop("name", "Autumn"), status=None, coupon_codes=list(codes),
                    start_date=kw.pop("start_date", TODAY - timedelta(days=3)),
                    end_date=kw.pop("end_date", TODAY + timedelta(days=3)), source="manual", **kw)
    db.add(ev)
    db.flush()
    set_status(db, ev, status, source="manual")
    return ev


def test_status_values_are_validated_and_history_is_kept(db, store):
    ev = _event(db, store)
    with pytest.raises(ValidationError):
        set_status(db, ev, "running", source="manual")
    set_status(db, ev, "live", source="manual", note="launched")
    set_status(db, ev, "paused", source="manual", note="stock issue")
    hist = ev.status_history
    assert [(h["from"], h["to"]) for h in hist] == [(None, "in_progress"), ("in_progress", "live"), ("live", "paused")]
    assert all(h["at"] for h in hist) and hist[-1]["note"] == "stock issue"
    assert ev.status_changed_at is not None


def test_dates_never_change_the_status(db, store):
    ev = _event(db, store, status="scheduled", start_date=TODAY - timedelta(days=2), end_date=TODAY - timedelta(days=1))
    refresh(db, ev, store)
    assert ev.status == "scheduled"  # start and end both passed: still not Live, not Completed
    prompts = describe(ev)["review_prompts"]
    assert any("Start date" in p for p in prompts)
    live = _event(db, store, status="live", name="Old live", end_date=TODAY - timedelta(days=5))
    refresh(db, live, store)
    assert live.status == "live" and any("End date" in p for p in describe(live)["review_prompts"])


def test_technical_status_without_store_is_unknown(db, store):
    ev = _event(db, store, status="live")
    refresh(db, ev, store)
    assert ev.technical_status == "unknown" and "Not verified" in ev.technical_evidence
    assert ev.status_conflict is None  # unknown never creates a conflict


def test_technical_status_from_synced_store(db, store):
    _connected(db, store)
    _coupon(db, store, "FALL10", "active")
    _coupon(db, store, "OLD5", "expired", sid="gid://shopify/DiscountCodeNode/2")
    _coupon(db, store, "GONE", "deleted", sid="gid://shopify/DiscountCodeNode/3")
    cases = {("FALL10",): "active", ("OLD5",): "inactive", ("GONE",): "unavailable", ("NOPE",): "unavailable",
             (): "unknown"}
    for codes, expected in cases.items():
        ev = _event(db, store, codes=codes, name=str(codes))
        refresh(db, ev, store)
        assert ev.technical_status == expected, (codes, ev.technical_evidence)


def test_conflict_detected_reviewed_and_cleared(db, store):
    _connected(db, store)
    _coupon(db, store, "FALL10", "expired")
    ev = _event(db, store, status="live")
    refresh(db, ev, store)
    assert ev.status_conflict and "marked Live" in ev.status_conflict
    assert describe(ev)["conflict_open"] and describe(ev)["needs_review"]
    acknowledge_conflict(db, ev, "Shopify sync lag, checked manually")
    assert not describe(ev)["conflict_open"] and ev.status_history[-1]["source"] == "review"
    set_status(db, ev, "completed", source="manual")
    refresh(db, ev, store)
    assert ev.status_conflict is None
    ev2 = _event(db, store, status="completed", name="done", codes=("LIVE1",))
    _coupon(db, store, "LIVE1", "active", sid="gid://shopify/DiscountCodeNode/9")
    refresh(db, ev2, store)
    assert "still redeemable" in ev2.status_conflict


def test_store_detected_offers_follow_the_store(db, store):
    _connected(db, store)
    _coupon(db, store, "SUMMER20", "active", sid="gid://shopify/DiscountCodeNode/20", title="Summer sale",
            starts_at=datetime(2026, 6, 1, tzinfo=UTC))
    _coupon(db, store, "SOON", "scheduled", sid="gid://shopify/DiscountCodeNode/21")
    _coupon(db, store, "DONE", "expired", sid="gid://shopify/DiscountCodeNode/22",
            ends_at=datetime.now(UTC) - timedelta(days=10))
    _coupon(db, store, "ANCIENT", "expired", sid="gid://shopify/DiscountCodeNode/23",
            ends_at=datetime.now(UTC) - timedelta(days=500))
    documented = _event(db, store, status="live", codes=("SUMMER20",), name="Documented summer")
    res = sync_store_offers(db, store)
    assert res["linked_to_documented"] == 1 and res["detected_offers_created"] == 2  # ANCIENT skipped
    assert documented.shopify_discount_id == "gid://shopify/DiscountCodeNode/20"
    assert documented.sync_status_from_store is False  # documented offers stay under the user's control
    detected = {e.coupon_codes[0]: e for e in db.query(PromoEvent).filter(PromoEvent.sync_status_from_store.is_(True))}
    assert detected["SOON"].status == "scheduled" and detected["DONE"].status == "completed"
    assert all(h["source"] == "store_sync" for h in detected["DONE"].status_history)
    # running again creates nothing new
    assert sync_store_offers(db, store)["detected_offers_created"] == 0


def test_api_status_endpoints_and_filters(db, store):
    from app.main import create_app

    c = TestClient(create_app())
    ev = c.post("/api/events", json={"store_id": store.id, "name": "Gift week", "status": "scheduled",
                                     "start_date": str(TODAY + timedelta(days=5)), "coupon_codes": ["gift"]}).json()
    assert ev["status"] == "scheduled" and ev["status_history"][0]["to"] == "scheduled"
    assert ev["technical_status"] == "unknown" and ev["technical_verified"] is False
    assert c.post(f"/api/events/{ev['id']}/status", json={"status": "running"}).status_code == 422
    r = c.post(f"/api/events/{ev['id']}/status", json={"status": "live", "note": "launched early"}).json()
    assert r["status"] == "live" and r["status_history"][-1]["note"] == "launched early"
    c.post("/api/events", json={"store_id": store.id, "name": "Drafty", "status": "in_progress"})
    db.add(PromoEvent(store_id=store.id, name="Unknown", status=None, start_date=TODAY, end_date=TODAY))
    db.commit()
    names = lambda q: sorted(e["name"] for e in c.get(f"/api/events?store_id={store.id}&status={q}").json())
    assert names("live") == ["Gift week"]
    assert names("in_progress,not_set") == ["Drafty", "Unknown"]
    assert c.get(f"/api/events?store_id={store.id}&status=bogus").status_code == 422
    live = c.get(f"/api/live-offers?store_id={store.id}").json()
    assert {e["name"] for e in live} == {"Gift week", "Drafty", "Unknown"}
    assert c.post(f"/api/events/{ev['id']}/status/review", json={}).status_code == 422  # no conflict to review
    assert any(a["action"] == "offer.status_changed" for a in c.get("/api/audit").json())


def test_migration_maps_old_statuses(tmp_path):
    from alembic import command
    from alembic.config import Config

    from app.core.config import BACKEND_DIR, get_settings

    url = f"sqlite:///{tmp_path / 'mig.db'}"
    import os

    old = os.environ.get("EGI_DATABASE_URL")
    os.environ["EGI_DATABASE_URL"] = url
    get_settings.cache_clear()
    try:
        cfg = Config(str(BACKEND_DIR / "alembic.ini"))
        command.upgrade(cfg, "b326968c24a4")
        eng = sa.create_engine(url)
        with eng.begin() as cx:
            cx.execute(sa.text("INSERT INTO stores (code, name, platform, currency, timezone, status, is_demo, "
                               "commercial_settings, constraints) VALUES ('S','S','shopify','USD','UTC','active',0,'{}','{}')"))
            for i, st in enumerate(["ended", "live", "planned", "not_launched", "undocumented", "cancelled"]):
                cx.execute(sa.text(
                    "INSERT INTO promo_events (store_id, name, status, channels, coupon_codes, eligible_products, "
                    "targets, verification_status, data_issues, source) VALUES "
                    f"(1, 'e{i}', '{st}', '[]', '[]', '[]', '{{}}', 'documented_only', '[]', 'manual')"))
        command.upgrade(cfg, "head")
        with eng.connect() as cx:
            got = dict(cx.execute(sa.text("SELECT name, status FROM promo_events")).all())
        assert got == {"e0": "completed", "e1": None, "e2": None, "e3": "cancelled", "e4": None, "e5": "cancelled"}
    finally:
        if old is None:
            os.environ.pop("EGI_DATABASE_URL", None)
        else:
            os.environ["EGI_DATABASE_URL"] = old
        get_settings.cache_clear()
