"""Synthetic demo store.

Creates a store flagged `is_demo=True` with a deterministic, plausible catalogue,
order history, costs, traffic, offers and coupons. Its purpose is to exercise the
application end-to-end without live credentials. It is labelled DEMO everywhere,
excluded from the real portfolio by default, and cannot be connected to a platform.
"""

import math
import random
from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.models import (
    CostRecord,
    Coupon,
    Customer,
    Order,
    OrderLine,
    PriceHistory,
    Product,
    PromoEvent,
    Recommendation,
    Store,
    StoreObjective,
    TrafficDaily,
    Variant,
)
from app.models.enums import DataSource, Objective
from app.offers.status import set_status
from app.services.sync import reindex_all_customers

DEMO_CODE = "DEMO"

CATALOG = [
    # title, type, base price, unit cost, popularity weight, variants
    ("Oversized Windbreaker Jacket", "Jacket", 89.0, 24.0, 30, ["S", "M", "L", "XL"]),
    ("Quilted Puffer Coat", "Jacket", 129.0, 41.0, 12, ["S", "M", "L"]),
    ("Linen Two-Piece Set", "Set", 74.0, 19.0, 10, ["S", "M", "L"]),
    ("Thermal Wool Trousers", "Trousers", 59.0, 17.0, 8, ["S", "M", "L"]),
    ("Knit Beanie", "Accessory", 19.0, 3.2, 9, ["One size"]),
    ("Wool Scarf", "Accessory", 25.0, 4.1, 7, ["Grey", "Camel", "Red"]),
    ("Waterproof Travel Bag", "Accessory", 35.0, 6.5, 4, ["One size"]),
    ("Rain Boots", "Footwear", 69.0, 22.0, 5, ["37", "38", "39", "40"]),
    ("Leather Gloves", "Accessory", 39.0, 9.0, 4, ["S/M", "L/XL"]),
    ("Fleece Lined Leggings", "Trousers", 34.0, 7.5, 6, ["S", "M", "L"]),
    ("Packable Rain Poncho", "Accessory", 22.0, 4.0, 2, ["One size"]),
    ("Cashmere Blend Sweater", "Knitwear", 99.0, 38.0, 3, ["S", "M", "L"]),
]
# Products often bought together (index pairs) to give co-purchase structure
AFFINITY = {0: [4, 5], 1: [8, 5], 2: [9], 3: [9], 7: [10]}
CODES = [("WELCOME15", 0.15, None), ("FALL10", 0.10, None), ("VIP20", 0.20, 99.0), ("BFWEEK", 0.20, 99.0)]


def _season(d: date) -> float:
    # outerwear-like seasonality: peak Nov, trough Jun, plus a Black-Friday spike
    base = 1 + 0.45 * math.cos((d.timetuple().tm_yday - 320) / 365 * 2 * math.pi)
    if d.month == 11 and 22 <= d.day <= 30:
        base *= 2.2
    return base


def reset_demo(db: Session, *, days: int = 540, seed: int = 7, daily_orders: float = 28.0) -> Store:
    existing = db.scalar(select(Store).where(Store.code == DEMO_CODE))
    if existing:
        if not existing.is_demo:
            raise RuntimeError("A non-demo store uses the DEMO code; refusing to overwrite it.")
        db.execute(delete(Recommendation).where(Recommendation.store_id == existing.id))
        db.delete(existing)
        db.flush()
    rng = random.Random(seed)
    store = Store(code=DEMO_CODE, name="Demo Outerwear Co. (synthetic)", platform="demo", currency="USD",
                  timezone="America/New_York", is_demo=True, status="active",
                  notes="Synthetic data for demonstration. Not a real business.",
                  commercial_settings={
                      "free_shipping_threshold": 99.0, "avg_shipping_cost_per_order": 7.5,
                      "payment_fee_pct": 0.029, "payment_fee_fixed": 0.30, "default_cogs_pct": None,
                      "fulfillment_cost_per_order": 2.0, "min_margin_pct": 0.55, "default_gift_cost": 4.1,
                      "new_customer_discount_pct": 0.15, "returning_customer_discount_pct": None,
                  })
    db.add(store)
    db.flush()
    db.add_all([
        StoreObjective(store_id=store.id, objective=Objective.MAXIMIZE_CONTRIBUTION, priority=1),
        StoreObjective(store_id=store.id, objective=Objective.INCREASE_AOV, priority=2),
        StoreObjective(store_id=store.id, objective=Objective.REDUCE_DISCOUNT_RELIANCE, priority=3),
    ])
    now = utcnow()
    variants: list[list[Variant]] = []
    for i, (title, ptype, price, cost, _, vnames) in enumerate(CATALOG):
        p = Product(store_id=store.id, source=DataSource.DEMO, source_id=f"demo-product-{i}", title=title,
                    handle=title.lower().replace(" ", "-"), product_type=ptype, vendor="Demo", status="ACTIVE",
                    tags=[], source_created_at=now - timedelta(days=days + 30), last_synced_at=now)
        db.add(p)
        db.flush()
        vs = []
        for j, vn in enumerate(vnames):
            stock = rng.choice([0, 3, 12, 40, 80, 150]) if j == len(vnames) - 1 else rng.randint(25, 220)
            v = Variant(store_id=store.id, product_id=p.id, source=DataSource.DEMO, source_id=f"demo-variant-{i}-{j}",
                        title=vn, sku=f"D{i:02d}-{j}", price=price, compare_at_price=round(price * 1.4, 0),
                        inventory_quantity=stock, inventory_tracked=True, available_for_sale=stock > 0)
            db.add(v)
            db.flush()
            db.add(PriceHistory(store_id=store.id, variant_id=v.id, price=price, compare_at_price=v.compare_at_price,
                                observed_at=now - timedelta(days=days), source=DataSource.DEMO))
            if i != 11:  # leave one product without cost to demonstrate partial coverage
                db.add(CostRecord(store_id=store.id, variant_id=v.id, sku=v.sku, unit_cost=cost, currency="USD",
                                  effective_from=(now - timedelta(days=days + 30)).date(), source=DataSource.DEMO,
                                  created_at=now))
            vs.append(v)
        variants.append(vs)

    weights = [c[4] for c in CATALOG]
    customers: list[Customer] = []
    # Explicit primary keys avoid a database round-trip per order while generating.
    from sqlalchemy import func

    next_cust = (db.scalar(select(func.max(Customer.id))) or 0) + 1
    next_order = (db.scalar(select(func.max(Order.id))) or 0) + 1
    start = date.today() - timedelta(days=days)
    oid = 0
    for d_i in range(days):
        day = start + timedelta(days=d_i)
        season = _season(day)
        trend = 1 + 0.15 * d_i / days
        n_orders = max(0, int(rng.gauss(daily_orders * season * trend, 4)))
        sessions = int(n_orders / rng.uniform(0.016, 0.022))
        db.add(TrafficDaily(store_id=store.id, day=day, sessions=sessions, added_to_cart=int(sessions * 0.07),
                            reached_checkout=int(sessions * 0.035), converted=n_orders, source=DataSource.DEMO))
        bf = day.month == 11 and 22 <= day.day <= 30
        for _ in range(n_orders):
            oid += 1
            at = datetime.combine(day, time(rng.randint(0, 23), rng.randint(0, 59)), tzinfo=UTC)
            if customers and rng.random() < 0.24:
                cust = rng.choice(customers)
            else:
                cust = Customer(id=next_cust, store_id=store.id, source=DataSource.DEMO,
                                source_id=f"demo-cust-{len(customers)}", first_order_at=at)
                next_cust += 1
                db.add(cust)
                customers.append(cust)
            first = rng.choices(range(len(CATALOG)), weights=weights)[0]
            items = [first]
            if rng.random() < 0.28:
                items.append(rng.choice(AFFINITY.get(first, [4, 5, 9])))
            if rng.random() < 0.12:
                items.append(rng.choices(range(len(CATALOG)), weights=weights)[0])
            qtys = [1 if rng.random() < 0.85 else 2 for _ in items]
            code = None
            r = rng.random()
            if bf and r < 0.55:
                code = CODES[3]
            elif r < 0.10:
                code = CODES[0]
            elif r < 0.17 and day.month in (9, 10):
                code = CODES[1]
            elif r < 0.19:
                code = CODES[2]
            lines = []
            gross = 0.0
            for idx, q in zip(items, qtys, strict=True):
                v = rng.choice([x for x in variants[idx] if x.inventory_quantity] or variants[idx])
                lines.append((idx, v, q))
                gross += CATALOG[idx][2] * q
            if code and code[2] and gross < code[2]:
                code = None
            disc_pct = code[1] if code else 0.0
            discounts = round(gross * disc_pct, 2)
            shipping = 0.0 if gross - discounts >= 99 else 6.95
            order = Order(id=next_order, store_id=store.id, source=DataSource.DEMO, source_id=f"demo-order-{oid}", name=f"#D{oid}",
                          processed_at=at, source_updated_at=at, currency="USD", gross_sales=round(gross, 2),
                          total_discounts=discounts, subtotal=round(gross - discounts, 2), total_shipping=shipping,
                          total_tax=0.0, total_price=round(gross - discounts + shipping, 2), units=sum(qtys),
                          discount_codes=[code[0]] if code else [], discount_applications=[
                              {"type": "DiscountCodeApplication", "code": code[0], "value_type": "percent",
                               "value": code[1] * 100}] if code else [],
                          financial_status="PAID", fulfillment_status="FULFILLED", channel="web",
                          customer_id=cust.id)
            next_order += 1
            db.add(order)
            refunded = 0.0
            for k, (idx, v, q) in enumerate(lines):
                line_disc = round(CATALOG[idx][2] * q * disc_pct, 2)
                ref_q = 1 if rng.random() < (0.07 if CATALOG[idx][1] in ("Jacket", "Footwear") else 0.02) else 0
                ref_amt = round((CATALOG[idx][2] - line_disc / q) * ref_q, 2)
                refunded += ref_amt
                db.add(OrderLine(store_id=store.id, order_id=order.id, source_id=f"demo-line-{oid}-{k}",
                                 product_id=v.product_id, variant_id=v.id, sku=v.sku, title=CATALOG[idx][0],
                                 variant_title=v.title, quantity=q, original_unit_price=CATALOG[idx][2],
                                 total_discount=line_disc, refunded_quantity=ref_q, refunded_amount=ref_amt))
            order.refunded_merchandise = round(refunded, 2)
            order.total_refunded = round(refunded, 2)
        if d_i % 90 == 0:
            db.flush()
    db.flush()
    reindex_all_customers(db, store.id)

    today = date.today()
    db.add_all([
        Coupon(store_id=store.id, code=c, source=DataSource.DEMO, title=f"{int(p * 100)}% off", value_type="percent",
               value=p * 100, min_subtotal=m, status="active", channel="code") for c, p, m in CODES
    ])
    db.add_all([
        PromoEvent(store_id=store.id, name="Black Friday Week", offer_text="20% off orders over $99",
                   offer_type="percentage_discount", start_date=date(today.year - 1, 11, 22),
                   end_date=date(today.year - 1, 11, 30), status="completed", coupon_codes=["BFWEEK"], threshold_amount=99,
                   channels=["site", "email"], display="full_site", activation="manual_code", source=DataSource.DEMO,
                   verification_status="unverifiable"),
        PromoEvent(store_id=store.id, name="VIP Early Access", offer_text="20% off for VIP customers over $99",
                   offer_type="percentage_discount", start_date=today - timedelta(days=20),
                   end_date=today + timedelta(days=10), status="paused", coupon_codes=["VIP20"], threshold_amount=99,
                   channels=["email"], display="email_only", activation="manual_code", source=DataSource.DEMO,
                   verification_status="unverifiable"),
        PromoEvent(store_id=store.id, name="Holiday Gift Guide bundle", offer_text="Jacket + scarf bundle at 10% off",
                   offer_type="bundle", start_date=today + timedelta(days=21), end_date=today + timedelta(days=45),
                   status="in_progress", coupon_codes=[], channels=["site"], display="full_site",
                   activation="automatic", source=DataSource.DEMO, verification_status="unverifiable"),
        PromoEvent(store_id=store.id, name="Spring Rain Promo", offer_text="15% off rainwear",
                   offer_type="percentage_discount", start_date=today - timedelta(days=200),
                   end_date=today - timedelta(days=190), status="cancelled", coupon_codes=["RAIN15"],
                   channels=["email"], display="email_only", activation="manual_code", source=DataSource.DEMO,
                   verification_status="unverifiable", notes="Cancelled: stock of rain boots arrived late."),
        PromoEvent(store_id=store.id, name="Autumn Opener", offer_text="Extra 10% off all orders",
                   offer_type="percentage_discount", start_date=today - timedelta(days=10),
                   end_date=today + timedelta(days=4), status="live", coupon_codes=["FALL10"],
                   channels=["site", "email"], display="full_site", activation="manual_code", source=DataSource.DEMO,
                   verification_status="unverifiable", targets={"offer_orders": 150}),
        PromoEvent(store_id=store.id, name="Black Friday Week", offer_text="Free scarf over $120",
                   offer_type="free_gift", start_date=date(today.year, 11, 22) if today < date(today.year, 11, 22)
                   else today + timedelta(days=30), end_date=date(today.year, 11, 30)
                   if today < date(today.year, 11, 22) else today + timedelta(days=37), status="scheduled",
                   coupon_codes=[], threshold_amount=120, bonus="Wool Scarf", channels=["site", "email"],
                   display="full_site", activation="automatic", source=DataSource.DEMO,
                   verification_status="unverifiable"),
    ])
    db.flush()
    from app.offers.status import sync_store_offers

    for ev in db.scalars(select(PromoEvent).where(PromoEvent.store_id == store.id)):
        target, ev.status = ev.status, None
        set_status(db, ev, target, source="demo", actor="demo data", note="Synthetic demo offer")
    sync_store_offers(db, store)
    db.commit()
    return store
