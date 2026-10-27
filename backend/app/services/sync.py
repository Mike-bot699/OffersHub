"""Synchronisation engine: pulls DTOs from a connector and upserts them per store.

Guarantees:
- Idempotent upserts keyed on (store_id, source, source_id) — re-running never duplicates.
- Watermarks advance only after a resource finishes successfully, so a failed run is
  retried from the same point next time.
- A failure in one resource does not discard the others; the job is marked `partial`.
- Missing permissions are recorded as warnings, never as silent gaps.
"""

import logging
import threading
from collections import defaultdict
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.connectors.base import Capability, CouponDTO, OrderDTO, ProductDTO
from app.core.errors import AppError, CapabilityNotSupported, ConflictError
from app.core.security import redact
from app.db.base import utcnow
from app.models import (
    CostRecord,
    Coupon,
    Customer,
    Integration,
    Order,
    OrderLine,
    PriceHistory,
    Product,
    Store,
    SyncJob,
    SyncState,
    Variant,
)
from app.models.enums import DataSource, IntegrationStatus, SyncStatus
from app.services import integrations as integration_service
from app.services.audit import audit

log = logging.getLogger(__name__)

RESOURCES = ("products", "orders", "discounts")
WATERMARK_OVERLAP = timedelta(minutes=10)
COMMIT_EVERY = 250

_locks: dict[int, threading.Lock] = defaultdict(threading.Lock)


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def get_state(db: Session, store_id: int, resource: str) -> SyncState:
    state = db.scalar(select(SyncState).where(SyncState.store_id == store_id, SyncState.resource == resource))
    if state is None:
        state = SyncState(store_id=store_id, resource=resource)
        db.add(state)
        db.flush()
    return state


def run_sync(db: Session, store_id: int, *, trigger: str = "manual", resources: Iterable[str] = RESOURCES,
             full: bool = False) -> SyncJob:
    lock = _locks[store_id]
    if not lock.acquire(blocking=False):
        raise ConflictError("A synchronisation is already running for this store.")
    try:
        return _run_sync_locked(db, store_id, trigger=trigger, resources=list(resources), full=full)
    finally:
        lock.release()


def _run_sync_locked(db: Session, store_id: int, *, trigger: str, resources: list[str], full: bool) -> SyncJob:
    store = db.get(Store, store_id)
    integ = integration_service.require_integration(db, store_id)
    job = SyncJob(store_id=store_id, integration_id=integ.id, trigger=trigger, resources=resources,
                  status=SyncStatus.RUNNING, started_at=utcnow(), stats={}, warnings=[])
    db.add(job)
    db.commit()

    if integ.status not in (IntegrationStatus.CONNECTED, IntegrationStatus.CONFIGURED, IntegrationStatus.ERROR) \
            or not integ.credentials_encrypted:
        return _finish(db, job, SyncStatus.FAILED, error="Integration is not connected.")

    try:
        connector = integration_service.build_connector(db, integ)
        caps = connector.capabilities()
    except AppError as exc:
        integ.status = IntegrationStatus.ERROR
        integ.last_error = redact(exc.message)
        return _finish(db, job, SyncStatus.FAILED, error=integ.last_error)

    failures = 0
    try:
        for resource in resources:
            state = get_state(db, store_id, resource)
            state.last_attempt_at = utcnow()
            try:
                if resource == "products":
                    stats = _sync_products(db, store, connector, state, full)
                elif resource == "orders":
                    if Capability.READ_ALL_ORDERS not in caps:
                        job.warnings = [*job.warnings, "read_all_orders not granted: Shopify only returns the last "
                                        "60 days of orders. Trends, seasonality and repeat rate will be limited."]
                    if Capability.READ_CUSTOMERS not in caps:
                        job.warnings = [*job.warnings, "read_customers not granted: repeat-purchase analysis "
                                        "is unavailable."]
                    stats = _sync_orders(db, store, integ, connector, state, full)
                elif resource == "discounts":
                    stats = _sync_discounts(db, store, connector)
                    db.flush()
                    _mark_discounts_synced(db, store.id)
                    from app.offers.status import sync_store_offers

                    stats["offers"] = sync_store_offers(db, store)
                else:
                    continue
                state.last_success_at = utcnow()
                state.last_status = SyncStatus.SUCCESS
                job.stats = {**job.stats, resource: stats}
                db.commit()
            except CapabilityNotSupported as exc:
                db.rollback()
                state = get_state(db, store_id, resource)
                state.last_status = "skipped"
                job.warnings = [*job.warnings, f"{resource}: {exc.message}"]
                job.stats = {**job.stats, resource: {"skipped": True}}
                db.commit()
            except AppError as exc:
                db.rollback()
                failures += 1
                state = get_state(db, store_id, resource)
                state.last_status = SyncStatus.FAILED
                job.stats = {**job.stats, resource: {"error": redact(exc.message)}}
                db.commit()
                log.warning("Sync %s failed for store %s: %s", resource, store.code, exc.message)
            except Exception as exc:
                db.rollback()
                failures += 1
                log.exception("Unexpected sync error for %s/%s", store.code, resource)
                state = get_state(db, store_id, resource)
                state.last_status = SyncStatus.FAILED
                job.stats = {**job.stats, resource: {"error": f"Unexpected error: {type(exc).__name__}: "
                                                              f"{redact(str(exc))[:300]}"}}
                db.commit()
    finally:
        connector.close()

    if failures == 0:
        integ.status = IntegrationStatus.CONNECTED
        integ.last_error = None
        status = SyncStatus.SUCCESS
    elif failures < len(resources):
        status = SyncStatus.PARTIAL
    else:
        status = SyncStatus.FAILED
        integ.last_error = "Last synchronisation failed; see sync history."
    return _finish(db, job, status)


def _finish(db: Session, job: SyncJob, status: SyncStatus, error: str | None = None) -> SyncJob:
    job.status = status
    job.finished_at = utcnow()
    if error:
        job.error = error
    audit(db, f"sync.{status}", "sync_job", job.id, store_id=job.store_id, actor="system",
          details={"trigger": job.trigger, "stats": job.stats})
    db.commit()
    return job


# ---- products ------------------------------------------------------------------------------
def _sync_products(db: Session, store: Store, connector, state: SyncState, full: bool) -> dict:
    since = None if full or state.watermark is None else _aware(state.watermark) - WATERMARK_OVERLAP
    fetched = upserted = price_changes = cost_updates = 0
    max_updated = _aware(state.watermark)
    for i, dto in enumerate(connector.fetch_products(since), start=1):
        fetched += 1
        pc, cu = upsert_product(db, store.id, dto, DataSource.SHOPIFY)
        price_changes += pc
        cost_updates += cu
        upserted += 1
        if dto.updated_at and (max_updated is None or dto.updated_at > max_updated):
            max_updated = dto.updated_at
        if i % COMMIT_EVERY == 0:
            db.commit()
    state.watermark = max_updated
    state.record_count = db.query(Product).filter(Product.store_id == store.id).count()
    return {"fetched": fetched, "upserted": upserted, "price_changes": price_changes, "cost_updates": cost_updates,
            "mode": "full" if since is None else "incremental"}


def upsert_product(db: Session, store_id: int, dto: ProductDTO, source: str) -> tuple[int, int]:
    now = utcnow()
    product = db.scalar(select(Product).where(Product.store_id == store_id, Product.source == source,
                                              Product.source_id == dto.source_id))
    if product is None:
        product = Product(store_id=store_id, source=source, source_id=dto.source_id, title=dto.title)
        db.add(product)
    product.title = dto.title
    product.handle = dto.handle
    product.product_type = dto.product_type
    product.vendor = dto.vendor
    product.status = dto.status
    product.tags = dto.tags
    product.source_created_at = dto.created_at
    product.source_updated_at = dto.updated_at
    product.last_synced_at = now
    db.flush()

    existing = {v.source_id: v for v in db.scalars(select(Variant).where(Variant.product_id == product.id))}
    price_changes = cost_updates = 0
    for v in dto.variants:
        variant = existing.get(v.source_id)
        is_new = variant is None
        if is_new:
            variant = Variant(store_id=store_id, product_id=product.id, source=source, source_id=v.source_id)
            db.add(variant)
        changed_price = is_new or variant.price != v.price or variant.compare_at_price != v.compare_at_price
        variant.title = v.title
        variant.sku = v.sku
        variant.price = v.price
        variant.compare_at_price = v.compare_at_price
        variant.inventory_quantity = v.inventory_quantity
        variant.inventory_tracked = v.inventory_tracked
        variant.available_for_sale = v.available_for_sale
        variant.inventory_item_source_id = v.inventory_item_id
        variant.source_updated_at = v.updated_at
        db.flush()
        if changed_price:
            db.add(PriceHistory(store_id=store_id, variant_id=variant.id, price=v.price,
                                compare_at_price=v.compare_at_price, observed_at=v.updated_at or now, source=source))
            price_changes += 0 if is_new else 1
        if v.unit_cost is not None:
            latest = db.scalar(select(CostRecord).where(CostRecord.variant_id == variant.id,
                                                        CostRecord.source == source)
                               .order_by(CostRecord.created_at.desc()).limit(1))
            if latest is None or abs(latest.unit_cost - v.unit_cost) > 1e-9:
                db.add(CostRecord(store_id=store_id, variant_id=variant.id, sku=v.sku, unit_cost=v.unit_cost,
                                  currency=v.unit_cost_currency, effective_from=now.date(), source=source,
                                  created_at=now))
                cost_updates += 1
    return price_changes, cost_updates


# ---- orders --------------------------------------------------------------------------------
def _sync_orders(db: Session, store: Store, integ: Integration, connector, state: SyncState, full: bool) -> dict:
    if full or state.watermark is None:
        since, created_since = None, utcnow() - timedelta(days=integ.initial_history_days)
    else:
        since, created_since = _aware(state.watermark) - WATERMARK_OVERLAP, None

    product_map = {p.source_id: p.id for p in db.scalars(select(Product).where(Product.store_id == store.id))}
    variant_map = {v.source_id: v.id for v in db.scalars(select(Variant).where(Variant.store_id == store.id))}
    fetched = 0
    refunded_ids: list[str] = []
    touched_customers: set[int] = set()
    currency_mismatch = 0
    max_updated = _aware(state.watermark)
    for i, dto in enumerate(connector.fetch_orders(since, created_since), start=1):
        fetched += 1
        if dto.currency and dto.currency != store.currency:
            currency_mismatch += 1
        order = upsert_order(db, store.id, dto, DataSource.SHOPIFY, product_map, variant_map)
        if order.customer_id:
            touched_customers.add(order.customer_id)
        if dto.has_refunds:
            refunded_ids.append(dto.source_id)
        if dto.updated_at and (max_updated is None or dto.updated_at > max_updated):
            max_updated = dto.updated_at
        if i % COMMIT_EVERY == 0:
            db.commit()
    db.flush()

    refund_lines = 0
    if refunded_ids:
        refund_lines = apply_refund_lines(db, store.id, connector.fetch_refund_lines(refunded_ids))
    reindex_customers(db, store.id, touched_customers)
    state.watermark = max_updated
    state.record_count = db.query(Order).filter(Order.store_id == store.id).count()
    stats = {"fetched": fetched, "refunded_orders": len(refunded_ids), "refund_lines": refund_lines,
             "mode": "full" if since is None else "incremental"}
    if currency_mismatch:
        stats["note"] = (f"{currency_mismatch} orders are in a currency different from the store currency; "
                         "amounts are taken in shop currency.")
    return stats


def upsert_order(db: Session, store_id: int, dto: OrderDTO, source: str,
                 product_map: dict[str, int], variant_map: dict[str, int]) -> Order:
    order = db.scalar(select(Order).where(Order.store_id == store_id, Order.source == source,
                                          Order.source_id == dto.source_id))
    if order is None:
        order = Order(store_id=store_id, source=source, source_id=dto.source_id, processed_at=dto.processed_at,
                      currency=dto.currency)
        db.add(order)
    merch_lines = [ln for ln in dto.lines if not ln.is_gift_card]
    order.name = dto.name
    order.processed_at = dto.processed_at
    order.source_updated_at = dto.updated_at
    order.currency = dto.currency
    order.gross_sales = round(sum(ln.original_unit_price * ln.quantity for ln in merch_lines), 2)
    order.total_discounts = dto.total_discounts
    order.subtotal = dto.subtotal
    order.total_shipping = dto.total_shipping
    order.total_tax = dto.total_tax
    order.total_price = dto.total_price
    order.total_refunded = dto.total_refunded
    order.units = sum(ln.quantity for ln in merch_lines)
    order.discount_codes = dto.discount_codes
    order.discount_applications = dto.discount_applications
    order.financial_status = dto.financial_status
    order.fulfillment_status = dto.fulfillment_status
    order.cancelled_at = dto.cancelled_at
    order.is_test = dto.is_test
    order.channel = dto.channel
    if dto.customer_source_id:
        order.customer_id = _customer_id(db, store_id, source, dto.customer_source_id, dto.processed_at)
    db.flush()

    existing = {ln.source_id: ln for ln in order.lines}
    seen = set()
    for li in dto.lines:
        seen.add(li.source_id)
        line = existing.get(li.source_id)
        if line is None:
            line = OrderLine(store_id=store_id, order_id=order.id, source_id=li.source_id, title=li.title,
                             quantity=li.quantity, original_unit_price=li.original_unit_price)
            order.lines.append(line)
        line.title = li.title
        line.variant_title = li.variant_title
        line.sku = li.sku
        line.quantity = li.quantity
        line.original_unit_price = li.original_unit_price
        line.total_discount = li.total_discount
        line.product_source_id = li.product_source_id
        line.variant_source_id = li.variant_source_id
        line.product_id = product_map.get(li.product_source_id or "")
        line.variant_id = variant_map.get(li.variant_source_id or "")
        line.is_gift_card = li.is_gift_card
        if li.refunded_quantity:
            line.refunded_quantity = li.refunded_quantity
            line.refunded_amount = li.refunded_amount
    for sid, line in existing.items():
        if sid not in seen:
            order.lines.remove(line)
    return order


def _customer_id(db: Session, store_id: int, source: str, source_id: str, at: datetime) -> int:
    cust = db.scalar(select(Customer).where(Customer.store_id == store_id, Customer.source == source,
                                            Customer.source_id == source_id))
    if cust is None:
        cust = Customer(store_id=store_id, source=source, source_id=source_id, first_order_at=at)
        db.add(cust)
        db.flush()
    elif cust.first_order_at is None or _aware(at) < _aware(cust.first_order_at):
        cust.first_order_at = at
    return cust.id


def apply_refund_lines(db: Session, store_id: int, refund_lines) -> int:
    by_order: dict[str, list] = defaultdict(list)
    for r in refund_lines:
        by_order[r.order_source_id].append(r)
    n = 0
    for order_sid, items in by_order.items():
        order = db.scalar(select(Order).where(Order.store_id == store_id, Order.source_id == order_sid))
        if order is None:
            continue
        lines = {ln.source_id: ln for ln in order.lines}
        for ln in order.lines:
            ln.refunded_quantity = 0
            ln.refunded_amount = 0.0
        total = 0.0
        for r in items:
            line = lines.get(r.line_source_id)
            if line:
                line.refunded_quantity += r.quantity
                line.refunded_amount = round(line.refunded_amount + r.amount, 2)
                total += r.amount
                n += 1
        order.refunded_merchandise = round(total, 2)
    return n


def reindex_customers(db: Session, store_id: int, customer_ids: set[int]) -> None:
    """Set customer_order_index (1 = first order) for the affected customers.
    Cancelled and test orders are excluded from the sequence."""
    if len(customer_ids) > 200:
        reindex_all_customers(db, store_id)
        return
    for cid in customer_ids:
        orders = db.scalars(select(Order).where(Order.store_id == store_id, Order.customer_id == cid)
                            .order_by(Order.processed_at)).all()
        idx = 0
        for o in orders:
            if o.cancelled_at or o.is_test:
                o.customer_order_index = None
                continue
            idx += 1
            o.customer_order_index = idx


def reindex_all_customers(db: Session, store_id: int) -> None:
    """Single-pass version of reindex_customers for a whole store."""
    db.flush()
    rows = db.execute(select(Order.id, Order.customer_id, Order.cancelled_at, Order.is_test)
                      .where(Order.store_id == store_id, Order.customer_id.is_not(None))
                      .order_by(Order.customer_id, Order.processed_at, Order.id)).all()
    updates, current, idx = [], None, 0
    for oid, cid, cancelled, test in rows:
        if cid != current:
            current, idx = cid, 0
        if cancelled or test:
            updates.append({"id": oid, "customer_order_index": None})
        else:
            idx += 1
            updates.append({"id": oid, "customer_order_index": idx})
    if updates:
        from sqlalchemy import update

        db.execute(update(Order), updates)


# ---- discounts -----------------------------------------------------------------------------
def _mark_discounts_synced(db: Session, store_id: int) -> None:
    """Record discount-sync success before refreshing offers, so they can use it as evidence."""
    st = get_state(db, store_id, "discounts")
    st.last_success_at = utcnow()


def _sync_discounts(db: Session, store: Store, connector) -> dict:
    now = utcnow()
    seen: set[str] = set()
    n = 0
    for dto in connector.fetch_discounts():
        upsert_coupon(db, store.id, dto, DataSource.SHOPIFY, now)
        seen.add(dto.code)
        n += 1
    # Codes that disappeared from Shopify are marked deleted, not removed: history matters.
    gone = 0
    for c in db.scalars(select(Coupon).where(Coupon.store_id == store.id, Coupon.source == DataSource.SHOPIFY)):
        if c.code not in seen and c.status != "deleted":
            c.status = "deleted"
            gone += 1
    return {"fetched": n, "marked_deleted": gone}


def upsert_coupon(db: Session, store_id: int, dto: CouponDTO, source: str, now: datetime) -> Coupon:
    c = db.scalar(select(Coupon).where(Coupon.store_id == store_id, Coupon.source == source, Coupon.code == dto.code))
    if c is None:
        c = Coupon(store_id=store_id, source=source, code=dto.code)
        db.add(c)
    c.source_id = dto.source_id
    c.title = dto.title
    c.discount_class = dto.discount_class
    c.value_type = dto.value_type
    c.value = dto.value
    c.min_subtotal = dto.min_subtotal
    c.min_quantity = dto.min_quantity
    c.starts_at = dto.starts_at
    c.ends_at = dto.ends_at
    c.status = dto.status
    c.usage_count = dto.usage_count
    c.usage_limit = dto.usage_limit
    c.once_per_customer = dto.once_per_customer
    c.channel = "automatic" if dto.is_automatic else "code"
    c.raw = dto.raw
    c.last_synced_at = now
    db.flush()
    return c


# ---- freshness -----------------------------------------------------------------------------
def freshness(db: Session, store: Store) -> dict:
    """Describe how current each resource is. `stale` = older than 2× the sync interval."""
    integ = db.scalar(select(Integration).where(Integration.store_id == store.id))
    states = {s.resource: s for s in db.scalars(select(SyncState).where(SyncState.store_id == store.id))}
    interval = timedelta(minutes=(integ.sync_interval_minutes if integ else 360))
    now = utcnow()
    out = {}
    for r in RESOURCES:
        s = states.get(r)
        last = _aware(s.last_success_at) if s else None
        if store.is_demo:
            status = "demo"
        elif integ is None or integ.status in (IntegrationStatus.NOT_CONFIGURED, IntegrationStatus.DISCONNECTED):
            status = "not_connected"
        elif last is None:
            status = "never_synced"
        elif now - last > 2 * interval:
            status = "stale"
        else:
            status = "fresh"
        out[r] = {"status": status, "last_success_at": last.isoformat() if last else None,
                  "last_status": s.last_status if s else None, "records": s.record_count if s else 0}
    return out
