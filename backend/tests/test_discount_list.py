"""Shopify admin Discounts export: store coupons with status, used as technical-status evidence.
Rows copied from the real MAU export (discounts_export.csv, 30 Oct 2026)."""

from fastapi.testclient import TestClient

from app.models import Coupon, PromoEvent
from app.offers.discount_list import import_discount_list
from app.offers.status import refresh

HEADER = ("Name,Value,Value Type,Type,Discount Class,Minimum Purchase Requirements,Combines with Order Discounts,"
          "Combines with Product Discounts,Combines with Shipping Discounts,Customer Selection,Context,Times Used In Total,"
          "Applies Once Per Customer,Usage Limit Per Code,Status,Start,End\n")
ROWS = [
    "BF2025,-20.0,percentage,Amount Off,order,99.0,,,,all,all,1540,,,Expired,2025-11-20 09:57:42 +0000,2026-01-31 23:59:59 +0000",
    "BUY3,-10.0,percentage,Amount Off,order,3,,,,all,all,51,,,Expired,2025-12-03 00:00:58 +0000,2025-12-03 12:59:11 +0000",
    "FREESHIPPINGCARD,-100.0,percentage,Free Shipping,shipping,,,1,,all,all,8,1,,Active,2025-11-27 11:18:03 +0000,",
    "Kaching Bundles - RC Paper Airplane,,app,App Discount,product,,1,1,1,all,all,61,,,Active,2026-08-10 17:43:37 +0100,",
    "LLT20,-20.0,percentage,Amount Off,order,99.0,,,,all,all,19,,,Active,2026-09-21 16:00:31 +0100,2026-10-31 23:59:59 +0000",
    "SPOOKLAB,-15.0,percentage,Amount Off,order,99.0,,,,all,all,11,,,Active,2026-10-19 17:52:56 +0100,2026-11-15 18:00:00 +0000",
    "PRIME10,-10.0,percentage,Amount Off,order,,,,,all,all,2,,,Expired,2026-10-06 17:12:17 +0100,2026-10-24 17:30:00 +0100",
]


def _csv(rows=ROWS) -> bytes:
    return (HEADER + "\n".join(rows) + "\n").encode()


def test_import_fields(db, store):
    rep = import_discount_list(db, store, _csv(), "discounts_export.csv", dry_run=False)
    assert rep.rows == 7 and rep.active == 4 and rep.expired == 3 and rep.automatic == 1 and not rep.possibly_partial
    c = {x.code: x for x in db.query(Coupon)}
    assert c["BF2025"].value == 20 and c["BF2025"].min_subtotal == 99 and c["BF2025"].usage_count == 1540
    assert c["BUY3"].min_quantity == 3 and c["BUY3"].min_subtotal is None  # "3" is items, not $3
    assert c["FREESHIPPINGCARD"].value_type == "free_shipping" and c["FREESHIPPINGCARD"].ends_at is None
    assert c["KACHING BUNDLES - RC PAPER AIRPLANE"].channel == "automatic"
    rep2 = import_discount_list(db, store, _csv(), "discounts_export.csv", dry_run=False)
    assert rep2.created == 0 and rep2.updated == 7


def test_fifty_rows_flagged_partial(db, store):
    rows = [f"CODE{i:02d},-10.0,percentage,Amount Off,order,,,,,all,all,0,,,Active,2026-10-01 00:00:00 +0000," for i in range(50)]
    assert import_discount_list(db, store, _csv(rows), "d.csv", dry_run=True).possibly_partial


def test_technical_status_from_partial_export(db, store):
    page = ROWS + [f"PAD{i:02d},-10.0,percentage,Amount Off,order,,,,,all,all,0,,,Expired,2025-01-01 00:00:00 +0000,"
                   for i in range(50 - len(ROWS))]  # exactly one Shopify page: possibly partial
    assert import_discount_list(db, store, _csv(page), "discounts_export.csv", dry_run=False).possibly_partial
    mk = lambda name, codes, status=None: PromoEvent(store_id=store.id, name=name, coupon_codes=codes, status=status)
    live, done, gone, completed_but_active = (mk("Halloween", ["SPOOKLAB", "OWLPASS"], "live"), mk("Prime", ["PRIME10"]),
                                              mk("Pumpkin", ["PUMPKIN"]), mk("Anniversary", ["LLT20"], "completed"))
    db.add_all([live, done, gone, completed_but_active])
    db.flush()
    for e in (live, done, gone, completed_but_active):
        refresh(db, e, store)
    assert live.technical_status == "active" and "not in export: OWLPASS" in live.technical_evidence
    assert done.technical_status == "inactive"
    assert gone.technical_status == "unknown" and "another page" in gone.technical_evidence  # never "not found"
    assert completed_but_active.status_conflict and "still redeemable" in completed_but_active.status_conflict


def test_api_and_report_show_store_status(db, store):
    from app.main import create_app

    c = TestClient(create_app())
    r = c.post(f"/api/stores/{store.id}/discount-list/import", files={"file": ("discounts_export.csv", _csv())},
               data={"dry_run": "false"})
    assert r.status_code == 200, r.text
    body = c.get(f"/api/stores/{store.id}/discount-report").json()  # no sales report yet: store list only
    assert body["report"] is None and {u["name"] for u in body["unused"]} >= {"SPOOKLAB", "LLT20"}
    assert next(u for u in body["unused"] if u["name"] == "SPOOKLAB")["store"]["status"] == "active"


def test_complete_export_reports_missing_codes(db, store):
    rows = ROWS + [f"FILL{i:02d},-10.0,percentage,Amount Off,order,,,,,all,all,0,,,Expired,2025-01-01 00:00:00 +0000,2025-02-01 00:00:00 +0000"
                   for i in range(60)]  # more than one page: a complete export
    assert not import_discount_list(db, store, _csv(rows), "all.csv", dry_run=False).possibly_partial
    planned = PromoEvent(store_id=store.id, name="Black Friday for Big Ideas", coupon_codes=["BIGIDEAS"], status="scheduled")
    never = PromoEvent(store_id=store.id, name="Chalk It Up", coupon_codes=["CHALKBOX"], status=None)
    ran = PromoEvent(store_id=store.id, name="Ran without its code", coupon_codes=["GHOST10"], status="completed")
    db.add_all([planned, never, ran])
    db.flush()
    for e in (planned, never, ran):
        refresh(db, e, store)
    # Offers that did not go live are ignored: no "not found" for a code not created yet.
    assert planned.technical_status == "unknown" and never.technical_status == "unknown"
    assert ran.technical_status == "unavailable" and "does not exist in Shopify" in ran.technical_evidence
