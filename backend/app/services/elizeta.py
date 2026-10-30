"""Eli Z integration: settings, verification, sync of catalog and sales, product tags.

A store reads from Eli Z with its numeric shop id and an API key. The key is encrypted at
rest and never returned; responses show only its last four characters.
"""

import logging
from datetime import date, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.analytics.product_tags import DEFAULT_MIN_RANKED, Sales, compute_tags
from app.connectors import elizeta as ez
from app.core.errors import AppError, ConfigurationError, ValidationError
from app.core.security import decrypt_secrets, encrypt_secrets, encryption_available, redact
from app.db.base import utcnow
from app.models import Integration, Order, OrderLine, Product, SalesReportRow, Store, SyncJob, SyncState, Variant
from app.models.enums import DataSource, IntegrationStatus, SyncStatus
from app.services.audit import audit

log = logging.getLogger(__name__)
PROVIDER = "elizeta"
SALES_WINDOW_DAYS = 30
DISCOUNT_WINDOW_DAYS = 400  # sales by discount: a year of history, so last year's offers can be compared


def get(db: Session, store_id: int) -> Integration | None:
    return db.scalar(select(Integration).where(Integration.store_id == store_id, Integration.provider == PROVIDER))


def key_hint(integ: Integration | None) -> str | None:
    if not integ or not integ.credentials_encrypted:
        return None
    key = decrypt_secrets(integ.credentials_encrypted).get("api_key") or ""
    return f"••••{key[-4:]}" if len(key) >= 8 else "••••"


def configure(db: Session, store: Store, *, shop_id: str | None, api_key: str | None) -> Integration:
    """Save shop id and/or key. A blank key keeps the stored one."""
    if store.is_demo:
        raise ValidationError("The demo store cannot be connected to a platform.")
    integ = get(db, store.id)
    if integ is None:
        integ = Integration(store_id=store.id, provider=PROVIDER, status=IntegrationStatus.NOT_CONFIGURED)
        db.add(integ)
    if shop_id is not None:
        shop_id = shop_id.strip()
        if shop_id and not shop_id.isdigit():
            raise ValidationError("The Eli Z shop id is a number (e.g. 26).")
        integ.external_shop_id = shop_id or None
    if api_key:
        if not encryption_available():
            raise ConfigurationError("EGI_SECRET_KEY is not set on the server, so the API key cannot be stored.")
        integ.credentials_encrypted = encrypt_secrets({"api_key": api_key.strip()})
    store.platform = "elizeta"
    integ.status = IntegrationStatus.CONFIGURED if integ.credentials_encrypted and integ.external_shop_id \
        else IntegrationStatus.NOT_CONFIGURED
    audit(db, "integration.configured", "integration", None, store_id=store.id,
          details={"provider": PROVIDER, "shop_id": integ.external_shop_id, "key_changed": bool(api_key)})
    db.flush()
    return integ


def client(integ: Integration) -> ez.ElizetaClient:
    if not integ.credentials_encrypted or not integ.external_shop_id:
        raise ValidationError("Set the Eli Z shop id and API key first.")
    key = decrypt_secrets(integ.credentials_encrypted)["api_key"]
    return ez.ElizetaClient(integ.external_shop_id, key, http=ez.http_factory())


def verify(db: Session, integ: Integration) -> dict:
    c = client(integ)
    try:
        info = c.verify()
    except AppError as exc:
        integ.status = IntegrationStatus.ERROR
        integ.last_error = redact(exc.message)
        db.flush()
        raise
    finally:
        c.close()
    integ.status = IntegrationStatus.CONNECTED
    integ.last_error = None
    integ.last_verified_at = utcnow()
    integ.shop_info = info
    audit(db, "integration.verified", "integration", integ.id, store_id=integ.store_id, details=info)
    db.flush()
    return info


def disconnect(db: Session, integ: Integration) -> None:
    integ.credentials_encrypted = None
    integ.status = IntegrationStatus.DISCONNECTED
    integ.sync_enabled = False
    audit(db, "integration.disconnected", "integration", integ.id, store_id=integ.store_id,
          details={"provider": PROVIDER})
    db.flush()


def describe(integ: Integration | None) -> dict:
    return {
        "provider": PROVIDER,
        "status": integ.status if integ else IntegrationStatus.NOT_CONFIGURED,
        "shop_id": integ.external_shop_id if integ else None,
        "has_key": bool(integ and integ.credentials_encrypted),
        "key_hint": key_hint(integ),
        "last_verified_at": integ.last_verified_at.isoformat() if integ and integ.last_verified_at else None,
        "last_error": integ.last_error if integ else None,
        "sync_enabled": integ.sync_enabled if integ else False,
        "sync_interval_minutes": integ.sync_interval_minutes if integ else 360,
        "provides": list(ez.CAPABILITIES),
        "not_available": ez.NOT_AVAILABLE,
    }


def _norm(s: str | None) -> str:
    return " ".join("".join(ch.lower() if ch.isalnum() else " " for ch in (s or "")).split())


def _state(db: Session, store_id: int, resource: str) -> SyncState:
    st = db.scalar(select(SyncState).where(SyncState.store_id == store_id, SyncState.resource == resource))
    if st is None:
        st = SyncState(store_id=store_id, resource=resource)
        db.add(st)
        db.flush()
    return st


def sync(db: Session, store: Store, *, trigger: str = "manual", today: date | None = None) -> SyncJob:
    """Catalog (active products) then the last 30 days of sales, then performance tags.
    Each step is recorded; a failed step does not hide the others."""
    from app.services.sync import upsert_product

    integ = get(db, store.id)
    if integ is None:
        raise ValidationError("This store has no Eli Z settings.")
    today = today or date.today()
    job = SyncJob(store_id=store.id, integration_id=integ.id, trigger=trigger, resources=["products", "sales", "discount_codes", "discount_sales", "collections"],
                  status=SyncStatus.RUNNING, started_at=utcnow(), stats={}, warnings=[*ez.NOT_AVAILABLE.values()])
    db.add(job)
    db.commit()
    c = client(integ)
    failures = 0
    try:
        st = _state(db, store.id, "products")
        st.last_attempt_at = utcnow()
        try:
            seen = set()
            for dto in c.fetch_products():
                upsert_product(db, store.id, dto, DataSource.ELIZETA)
                seen.add(dto.source_id)
            # Products no longer active in the catalog are marked so, never deleted (history keeps them).
            gone = 0
            for p in db.scalars(select(Product).where(Product.store_id == store.id,
                                                      Product.source == DataSource.ELIZETA)):
                if p.source_id not in seen and p.status != "NOT_ACTIVE":
                    p.status = "NOT_ACTIVE"
                    gone += 1
            st.last_success_at = utcnow()
            st.last_status = SyncStatus.SUCCESS
            st.record_count = len(seen)
            job.stats = {**job.stats, "products": {"active": len(seen), "no_longer_active": gone}}
            db.commit()
        except AppError as exc:
            db.rollback()
            failures += 1
            st = _state(db, store.id, "products")
            st.last_status = SyncStatus.FAILED
            job.stats = {**job.stats, "products": {"error": redact(exc.message)}}
            db.commit()

        st = _state(db, store.id, "sales")
        st.last_attempt_at = utcnow()
        try:
            start, end = today - timedelta(days=SALES_WINDOW_DAYS), today - timedelta(days=1)
            rows = c.fetch_sales(start, end)
            matched = store_sales(db, store, rows, start, end)
            tagged = refresh_tags(db, store)
            st.last_success_at = utcnow()
            st.last_status = SyncStatus.SUCCESS
            st.record_count = len(rows)
            units_total = sum(r.units for r in rows)
            fields = getattr(c, "last_sales_fields", [])
            job.stats = {**job.stats, "sales": {"rows": len(rows), "matched": matched, "units_total": units_total,
                                                "fields": fields, "tags": tagged}}
            if rows and units_total == 0:
                job.warnings = [*job.warnings, "The sales report has rows but no units in the fields read "
                                f"(sold, sales, units, quantity, qty). Fields in the report: {', '.join(fields)}."]
            db.commit()
        except AppError as exc:
            db.rollback()
            failures += 1
            st = _state(db, store.id, "sales")
            st.last_status = SyncStatus.FAILED
            job.stats = {**job.stats, "sales": {"error": redact(exc.message)}}
            db.commit()

        failures += sync_discounts(db, store, c, job, today)
        failures += sync_collections(db, store, c, job)
    finally:
        c.close()
    if failures == 0:
        integ.status, integ.last_error, job.status = IntegrationStatus.CONNECTED, None, SyncStatus.SUCCESS
    else:
        job.status = SyncStatus.PARTIAL if failures < 5 else SyncStatus.FAILED
        integ.last_error = "Last synchronisation had errors; see sync history."
    job.finished_at = utcnow()
    audit(db, f"sync.{job.status}", "sync_job", job.id, store_id=store.id, actor="system", details=job.stats)
    db.commit()
    return job


def sync_discounts(db: Session, store: Store, c: ez.ElizetaClient, job: SyncJob, today: date) -> int:
    """Discount codes (status evidence), then sales by discount and day. Returns the failed steps.

    An endpoint that answers in an unrecognised shape writes nothing and says which fields it sent."""
    from app.offers import platform_discounts as pd
    from app.offers.status import sync_store_offers

    failures, groups = 0, {}
    st = _state(db, store.id, "discount_codes")
    st.last_attempt_at = utcnow()
    try:
        f = c.fetch_discount_codes()
        if f.rows and not any(r.get("code") for r in f.rows):
            raise ez.ElizetaError(f"Discount codes came back without a code field. Fields: {', '.join(f.fields)}.")
        groups = pd.campaign_map(f.rows)
        stats = pd.store_codes(db, store, f.rows, complete=f.complete)
        if not f.complete:
            job.warnings = [*job.warnings, "The discount code list was cut at the page limit: codes missing from it "
                                           "stay not verified."]
        st.last_success_at, st.last_status, st.record_count = utcnow(), SyncStatus.SUCCESS, len(f.rows)
        job.stats = {**job.stats, "discount_codes": {**stats, "fields": f.fields}}
        db.commit()
    except AppError as exc:
        db.rollback()
        failures += 1
        _state(db, store.id, "discount_codes").last_status = SyncStatus.FAILED
        job.stats = {**job.stats, "discount_codes": {"error": redact(exc.message)}}
        db.commit()

    st = _state(db, store.id, "discount_sales")
    st.last_attempt_at = utcnow()
    try:
        start, end = today - timedelta(days=DISCOUNT_WINDOW_DAYS), today - timedelta(days=1)
        since = pd.read_from(db, store, start, today)
        f = c.fetch_discount_sales(since or start, end)
        from app.offers.discount_report import map_rows

        if f.rows and not map_rows(f.rows):
            raise ez.ElizetaError(f"Sales by discount came back without a discount name field. Fields: {', '.join(f.fields)}.")
        stats = pd.store_sales(db, store, f.rows, start, end, groups, since=since)
        if f.rows and not stats.get("by_day"):
            job.warnings = [*job.warnings, "Sales by discount have no day field: offer results cannot be split by "
                                           f"offer dates. Fields: {', '.join(f.fields)}."]
        st.last_success_at, st.last_status, st.record_count = utcnow(), SyncStatus.SUCCESS, len(f.rows)
        job.stats = {**job.stats, "discount_sales": {**stats, "fields": f.fields}}
        db.commit()
    except AppError as exc:
        db.rollback()
        failures += 1
        _state(db, store.id, "discount_sales").last_status = SyncStatus.FAILED
        job.stats = {**job.stats, "discount_sales": {"error": redact(exc.message)}}
        db.commit()
    sync_store_offers(db, store, today=today)  # technical status from the new evidence
    db.commit()
    return failures


def sync_collections(db: Session, store: Store, c: ez.ElizetaClient, job: SyncJob) -> int:
    from app.offers.collections import store_collections

    st = _state(db, store.id, "collections")
    st.last_attempt_at = utcnow()
    try:
        cols = c.fetch_collections()
        if cols.rows and not any(r.get("title") for r in cols.rows):
            raise ez.ElizetaError(f"Collections came back without a title field. Fields: {', '.join(cols.fields)}.")
        members = c.fetch_collection_products()
        stats = store_collections(db, store, cols.rows, members.rows, complete=cols.complete and members.complete)
        st.last_success_at, st.last_status, st.record_count = utcnow(), SyncStatus.SUCCESS, stats["collections"]
        job.stats = {**job.stats, "collections": {**stats, "fields": cols.fields, "membership_fields": members.fields}}
        db.commit()
        return 0
    except AppError as exc:
        db.rollback()
        _state(db, store.id, "collections").last_status = SyncStatus.FAILED
        job.stats = {**job.stats, "collections": {"error": redact(exc.message)}}
        db.commit()
        return 1


def store_sales(db: Session, store: Store, rows: list[ez.SalesRow], start: date, end: date) -> int:
    """Replace the stored report for the window and match rows to products:
    name first, then external id, then SKU (SKUs are sometimes shared, so they come last).
    A name or SKU shared by several products is ambiguous and left unmatched."""
    db.execute(delete(SalesReportRow).where(SalesReportRow.store_id == store.id, SalesReportRow.source == DataSource.ELIZETA))
    products = db.scalars(select(Product).where(Product.store_id == store.id, Product.source == DataSource.ELIZETA)).all()
    by_title: dict[str, list[int]] = {}
    for p in products:
        by_title.setdefault(_norm(p.title), []).append(p.id)
    by_id = {p.source_id: p.id for p in products}
    by_sku: dict[str, set[int]] = {}
    for v in db.scalars(select(Variant).where(Variant.store_id == store.id, Variant.source == DataSource.ELIZETA)):
        if v.sku:
            by_sku.setdefault(v.sku.strip().upper(), set()).add(v.product_id)
    now = utcnow()
    matched = 0
    for r in rows:
        pid, how = None, None
        t = by_title.get(_norm(r.title), [])
        if len(t) == 1:
            pid, how = t[0], "title"
        elif r.external_id and r.external_id in by_id:
            pid, how = by_id[r.external_id], "external_id"
        elif r.sku and len(by_sku.get(r.sku.strip().upper(), ())) == 1:
            pid, how = next(iter(by_sku[r.sku.strip().upper()])), "sku"
        matched += pid is not None
        db.add(SalesReportRow(store_id=store.id, source=DataSource.ELIZETA, period_start=start, period_end=end,
                              title=r.title[:500], external_id=r.external_id, sku=r.sku, units=r.units,
                              revenue=r.revenue, avg_price=r.avg_price, currency="USD", product_id=pid,
                              match_method=how, fetched_at=now))
    db.flush()
    return matched


def refresh_tags(db: Session, store: Store, *, min_ranked: int | None = None) -> dict:
    """Recompute performance tags from the stored sales report."""
    min_ranked = DEFAULT_MIN_RANKED if min_ranked is None else min_ranked
    setting = (store.constraints or {}).get("tag_min_ranked_products")
    if setting is not None:
        min_ranked = int(setting)
    units: dict[int, int] = {}
    report_rev: dict[int, float] = {}
    for r in db.scalars(select(SalesReportRow).where(SalesReportRow.store_id == store.id,
                                                     SalesReportRow.product_id.is_not(None))):
        units[r.product_id] = units.get(r.product_id, 0) + r.units
        if r.revenue is not None:
            report_rev[r.product_id] = report_rev.get(r.product_id, 0.0) + r.revenue
    prices: dict[int, float] = {}
    for v in db.scalars(select(Variant).where(Variant.store_id == store.id)):
        if v.price is not None and v.product_id not in prices:
            prices[v.product_id] = v.price
    basis = "eli_z_sales_report"
    if not units:
        # No platform report (store not on Eli Z, or the demo): use order lines of the last 30 days.
        since = utcnow() - timedelta(days=SALES_WINDOW_DAYS)
        q = (select(OrderLine.product_id, func.sum(OrderLine.quantity - OrderLine.refunded_quantity))
             .join(Order, Order.id == OrderLine.order_id)
             .where(OrderLine.store_id == store.id, OrderLine.product_id.is_not(None), Order.processed_at >= since,
                    Order.cancelled_at.is_(None), Order.is_test.is_(False))
             .group_by(OrderLine.product_id))
        units = {pid: int(u or 0) for pid, u in db.execute(q)}
        basis = "orders"
    rows = [Sales(pid, u, prices.get(pid), report_rev.get(pid)) for pid, u in units.items()]
    tags = compute_tags(rows, min_ranked=min_ranked)
    now = utcnow()
    counts: dict[str, int] = {}
    for p in db.scalars(select(Product).where(Product.store_id == store.id)):
        t = tags.get(p.id)
        p.perf_tag = t["tag"] if t else None
        p.perf_rank = t["rank"] if t else None
        p.perf_units_30d = t["units"] if t else None
        p.perf_revenue_30d = round(t["revenue"], 2) if t and t["revenue"] is not None else None
        p.perf_computed_at = now
        if p.perf_tag:
            counts[p.perf_tag] = counts.get(p.perf_tag, 0) + 1
    ranked = sum(1 for t in tags.values() if t["rank"])
    db.flush()
    store.constraints = {**(store.constraints or {}), "tags_basis": basis, "tags_computed_at": now.isoformat()}
    return {**counts, "ranked": ranked, "below_minimum": ranked < min_ranked, "min_ranked": min_ranked, "basis": basis}
