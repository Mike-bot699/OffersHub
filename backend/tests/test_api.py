import io

import httpx
import pytest
from fastapi.testclient import TestClient

from tests.fake_shopify import FakeShopify, order_rows, sample_catalog


@pytest.fixture()
def client(db, monkeypatch):
    from app.main import create_app

    return TestClient(create_app())


def test_health_and_meta(client):
    assert client.get("/api/health").json()["ok"]
    meta = client.get("/api/meta").json()
    assert meta["encryption_configured"] and meta["write_actions_enabled"] is False
    assert meta["ai_available"] is False


def test_store_crud_and_validation(client):
    r = client.post("/api/stores", json={"code": "wb", "name": "Wjacket", "currency": "usd"})
    assert r.status_code == 201 and r.json()["code"] == "WB" and r.json()["currency"] == "USD"
    assert client.post("/api/stores", json={"code": "WB", "name": "dup"}).status_code == 409
    sid = r.json()["id"]
    bad = client.patch(f"/api/stores/{sid}", json={"commercial_settings": {"payment_fee_pct": 3}})
    assert bad.status_code == 422 and "fraction" in bad.json()["error"]["message"]
    ok = client.patch(f"/api/stores/{sid}", json={"commercial_settings": {"payment_fee_pct": 0.029}})
    assert ok.json()["commercial_settings"]["payment_fee_pct"] == 0.029
    objs = client.put(f"/api/stores/{sid}/objectives",
                      json=[{"objective": "increase_aov"}, {"objective": "maximize_contribution_margin"}])
    assert [o["priority"] for o in objs.json()] == [1, 2]
    assert client.get(f"/api/stores/{sid}").json()["integration"]["status"] == "not_configured"
    assert client.get("/api/stores/999").status_code == 404


def test_integration_connect_never_leaks_secret(client, monkeypatch):
    fake = FakeShopify(products_jsonl=sample_catalog(),
                       orders_jsonl=order_rows(1, processed="2026-10-01T10:00:00Z",
                                               lines=[("gid://shopify/ProductVariant/11", 1, 89.0, 0)]))
    from app.services import integrations as svc

    monkeypatch.setattr(svc, "http_factory", lambda: httpx.Client(transport=fake.transport()))
    monkeypatch.setattr("app.connectors.shopify.client.time.sleep", lambda s: None)
    monkeypatch.setattr("app.connectors.shopify.connector.time.sleep", lambda s: None)
    sid = client.post("/api/stores", json={"code": "TS", "name": "T"}).json()["id"]
    r = client.put(f"/api/stores/{sid}/integration", json={"shop_domain": "test-shop", "auth_method": "admin_token",
                                                           "access_token": "shpat_supersecret"})
    assert r.status_code == 200 and r.json()["status"] == "connected"
    assert "supersecret" not in r.text
    for path in (f"/api/stores/{sid}", f"/api/stores/{sid}/integration", "/api/audit", "/api/stores"):
        assert "supersecret" not in client.get(path).text
    # synchronous sync through the service (the HTTP endpoint starts a background thread)
    from app.db.session import SessionLocal
    from app.services.sync import run_sync

    with SessionLocal() as s:
        assert run_sync(s, sid).status == "success"
    jobs = client.get(f"/api/stores/{sid}/sync-jobs").json()
    assert jobs["jobs"][0]["status"] == "success"
    d = client.post(f"/api/stores/{sid}/integration/disconnect", json={"confirm_store_code": "TS", "purge_data": True})
    assert d.json()["status"] == "disconnected" and not d.json()["has_credentials"]


def test_demo_flow_and_portfolio_excludes_demo(client):
    r = client.post("/api/demo/reset")
    sid = r.json()["store_id"]
    assert client.get("/api/portfolio").json()["stores"] == []
    port = client.get("/api/portfolio?include_demo=true").json()
    assert port["stores"][0]["store"]["is_demo"] is True
    assert port["totals_by_currency"] == {}  # demo is listed but never summed into real totals
    s = client.get(f"/api/stores/{sid}/summary?days=60").json()
    assert s["kpis"]["orders"] > 0
    assert client.get(f"/api/stores/{sid}/products?days=60").json()["products"]
    b = client.get(f"/api/stores/{sid}/basket?days=60").json()
    assert b["distribution"] and "gift_candidates" in b
    sim = client.post(f"/api/stores/{sid}/simulations", json={
        "kind": "threshold", "inputs": {"threshold": 120, "incentive_cost": 4}, "save": True}).json()
    assert sim["simulation_id"] and sim["scenarios"]
    assert client.post(f"/api/stores/{sid}/simulations", json={"kind": "bundle", "inputs": {}}).status_code == 422
    recs = client.get(f"/api/recommendations?store_id={sid}").json()
    assert recs and all(r["store_code"] == "DEMO" for r in recs)
    assert client.get("/api/recommendations").json() == []  # demo excluded by default
    exp = client.post(f"/api/recommendations/{recs[0]['id']}/experiment").json()
    assert exp["status"] == "hypothesis"
    assert client.post(f"/api/experiments/{exp['id']}/transition", json={"to": "closed"}).status_code == 422
    assert client.post(f"/api/recommendations/{recs[0]['id']}/ai-commentary").status_code == 503
    # demo stores cannot be synced or receive live-change proposals
    assert client.post(f"/api/stores/{sid}/sync", json={}).status_code == 422


def test_workbook_upload_dry_run_then_commit(client):
    from tests.test_imports import _workbook

    content = _workbook()
    files = {"file": ("offers.xlsx", io.BytesIO(content), "application/octet-stream")}
    r = client.post("/api/imports/offers-workbook", files=files, data={"dry_run": "true"})
    assert r.status_code == 200 and r.json()["events_parsed"] == 6 and client.get("/api/stores").json() == []
    files = {"file": ("offers.xlsx", io.BytesIO(content), "application/octet-stream")}
    r = client.post("/api/imports/offers-workbook", files=files, data={"dry_run": "false"})
    assert r.json()["events_created"] == 6
    events = client.get("/api/events").json()
    assert len(events) == 6 and {e["verification_status"] for e in events} == {"documented_only"}
    ev = events[0]
    v = client.post(f"/api/events/{ev['id']}/verify").json()
    assert v["status"] == "unverifiable"
    assert client.get("/api/alerts").status_code == 200
    assert client.get("/api/offer-history").json()["events_used"] >= 1


def test_password_auth(db, monkeypatch):
    monkeypatch.setenv("EGI_AUTH_MODE", "password")
    monkeypatch.setenv("EGI_AUTH_PASSWORD", "correct horse")
    from app.core.config import get_settings

    get_settings.cache_clear()
    from app.main import create_app

    c = TestClient(create_app())
    assert c.get("/api/health").status_code == 200
    assert c.get("/api/stores").status_code == 401
    monkeypatch.setattr("app.api.auth.time.sleep", lambda s: None)
    assert c.post("/api/auth/login", json={"password": "nope"}).status_code == 401
    assert c.post("/api/auth/login", json={"password": "correct horse"}).status_code == 200
    assert c.get("/api/stores").status_code == 200
    get_settings.cache_clear()
