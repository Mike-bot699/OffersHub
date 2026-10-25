"""Store-scoped, period-scoped order dataset used by every analysis.

Loads one store's orders (never more than one store), excludes test and cancelled
orders, resolves unit costs with their provenance, and records data-quality facts
(cost coverage, refund detail, customer IDs, traffic) that analyses must surface.
"""

from bisect import bisect_right
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.models import CostRecord, Order, Store, TrafficDaily

COST_SOURCE_PRIORITY = {"manual": 3, "csv_import": 2, "shopify": 1}


@dataclass(slots=True)
class LineRec:
    product_id: int | None
    variant_id: int | None
    title: str
    variant_title: str | None
    qty: int
    unit_price: float
    discount: float
    refunded_qty: int
    refunded_amount: float
    unit_cost: float | None
    cost_basis: str | None  # "recorded" | "assumption" | None

    @property
    def gross(self) -> float:
        return self.unit_price * self.qty

    @property
    def net(self) -> float:
        return self.gross - self.discount

    @property
    def kept_qty(self) -> int:
        return max(0, self.qty - self.refunded_qty)

    @property
    def cogs(self) -> float | None:
        return None if self.unit_cost is None else self.unit_cost * self.kept_qty


@dataclass(slots=True)
class OrderRec:
    id: int
    at: datetime
    local_date: date
    gross: float
    discounts: float
    returns: float
    returns_estimated: bool
    shipping: float
    tax: float
    paid: float
    units: int
    codes: list[str]
    automatic_discounts: list[str]
    customer_id: int | None
    customer_order_index: int | None
    lines: list[LineRec] = field(default_factory=list)

    @property
    def net(self) -> float:
        return self.gross - self.discounts

    @property
    def has_discount(self) -> bool:
        return self.discounts > 0.005


@dataclass
class Dataset:
    store: Store
    start: date
    end: date
    orders: list[OrderRec]
    quality: dict = field(default_factory=dict)
    traffic: dict[date, int] = field(default_factory=dict)  # store-level sessions per local day

    @property
    def n_orders(self) -> int:
        return len(self.orders)


def store_tz(store: Store) -> ZoneInfo:
    try:
        return ZoneInfo(store.timezone or "UTC")
    except ZoneInfoNotFoundError:
        return ZoneInfo("UTC")


def period_bounds_utc(store: Store, start: date, end: date) -> tuple[datetime, datetime]:
    tz = store_tz(store)
    lo = datetime.combine(start, time.min, tzinfo=tz).astimezone(UTC)
    hi = datetime.combine(end + timedelta(days=1), time.min, tzinfo=tz).astimezone(UTC)
    return lo, hi


class CostResolver:
    """Picks the unit cost effective on the order date; prefers manual/CSV over platform cost."""

    def __init__(self, db: Session, store: Store):
        self.by_variant: dict[int, list[tuple[date, int, float]]] = defaultdict(list)
        for c in db.scalars(select(CostRecord).where(CostRecord.store_id == store.id,
                                                     CostRecord.variant_id.is_not(None))):
            eff = c.effective_from or date(1970, 1, 1)
            self.by_variant[c.variant_id].append((eff, COST_SOURCE_PRIORITY.get(c.source, 0), c.unit_cost))
        for v in self.by_variant.values():
            v.sort()
        self.default_pct = (store.commercial_settings or {}).get("default_cogs_pct")

    def resolve(self, variant_id: int | None, on: date, unit_price: float) -> tuple[float | None, str | None]:
        recs = self.by_variant.get(variant_id) if variant_id else None
        if recs:
            i = bisect_right(recs, (on, 99, float("inf")))
            chosen = recs[i - 1] if i > 0 else recs[0]
            return chosen[2], "recorded"
        if self.default_pct is not None:
            return unit_price * float(self.default_pct), "assumption"
        return None, None


def load_dataset(db: Session, store: Store, start: date, end: date) -> Dataset:
    tz = store_tz(store)
    lo, hi = period_bounds_utc(store, start, end)
    rows = db.scalars(
        select(Order)
        .where(Order.store_id == store.id, Order.processed_at >= lo, Order.processed_at < hi,
               Order.is_test.is_(False), Order.cancelled_at.is_(None))
        .options(selectinload(Order.lines))
        .order_by(Order.processed_at)
    ).all()
    costs = CostResolver(db, store)
    orders: list[OrderRec] = []
    net_total = net_with_cost = net_assumed = 0.0
    lines_total = lines_with_product = 0
    returns_estimated = 0
    with_customer = 0
    for o in rows:
        at = o.processed_at if o.processed_at.tzinfo else o.processed_at.replace(tzinfo=UTC)
        local = at.astimezone(tz).date()
        recs = []
        for ln in o.lines:
            if ln.is_gift_card:
                continue
            cost, basis = costs.resolve(ln.variant_id, local, ln.original_unit_price)
            rec = LineRec(ln.product_id, ln.variant_id, ln.title, ln.variant_title, ln.quantity,
                          ln.original_unit_price, ln.total_discount, ln.refunded_quantity, ln.refunded_amount,
                          cost, basis)
            recs.append(rec)
            lines_total += 1
            lines_with_product += 1 if ln.product_id else 0
            net_total += rec.net
            if basis == "recorded":
                net_with_cost += rec.net
            elif basis == "assumption":
                net_assumed += rec.net
        net = o.gross_sales - o.total_discounts
        if o.refunded_merchandise > 0:
            ret, est = o.refunded_merchandise, False
        elif o.total_refunded > 0:
            # Refund detail unavailable: cap at merchandise value; includes shipping/tax refunds.
            ret, est = min(o.total_refunded, max(net, 0.0)), True
            returns_estimated += 1
        else:
            ret, est = 0.0, False
        if o.customer_id:
            with_customer += 1
        orders.append(OrderRec(
            id=o.id, at=at, local_date=local, gross=o.gross_sales, discounts=o.total_discounts, returns=ret,
            returns_estimated=est, shipping=o.total_shipping, tax=o.total_tax, paid=o.total_price, units=o.units,
            codes=list(o.discount_codes or []),
            automatic_discounts=[d.get("title") for d in (o.discount_applications or [])
                                 if d.get("type") == "AutomaticDiscountApplication" and d.get("title")],
            customer_id=o.customer_id, customer_order_index=o.customer_order_index, lines=recs,
        ))

    traffic: dict[date, int] = {}
    for day, sess in db.execute(select(TrafficDaily.day, func.sum(TrafficDaily.sessions)).where(
            TrafficDaily.store_id == store.id, TrafficDaily.product_id.is_(None),
            TrafficDaily.day >= start, TrafficDaily.day <= end).group_by(TrafficDaily.day)):
        traffic[day] = int(sess or 0)
    sessions = sum(traffic.values())
    traffic_days = len(traffic)
    first_order = db.scalar(select(func.min(Order.processed_at)).where(Order.store_id == store.id))
    n = len(orders)
    quality = {
        "orders": n,
        "cost_coverage": (net_with_cost / net_total) if net_total > 0 else None,
        "cost_assumed_share": (net_assumed / net_total) if net_total > 0 else None,
        "lines_matched_to_products": (lines_with_product / lines_total) if lines_total else None,
        "returns_estimated_orders": returns_estimated,
        "customer_id_coverage": (with_customer / n) if n else None,
        "sessions": int(sessions) if sessions else None,
        "traffic_days": traffic_days,
        "period_days": (end - start).days + 1,
        "history_start": first_order.date().isoformat() if first_order else None,
    }
    return Dataset(store=store, start=start, end=end, orders=orders, quality=quality, traffic=traffic)


def previous_period(start: date, end: date) -> tuple[date, date]:
    span = (end - start).days + 1
    return start - timedelta(days=span), start - timedelta(days=1)


def year_ago(start: date, end: date) -> tuple[date, date]:
    return start - timedelta(days=364), end - timedelta(days=364)  # same weekday alignment
