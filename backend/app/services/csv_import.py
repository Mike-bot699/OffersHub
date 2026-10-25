"""CSV importers for data Shopify orders alone cannot provide.

- Costs: unit cost per SKU (or Shopify variant ID). Needed for any margin metric.
- Traffic: daily sessions / funnel (e.g. a Shopify Analytics or GA4 export). Needed for
  conversion rate. Store-level rows leave the product column empty.

Rows are matched within ONE store only. Unmatched rows are reported, never guessed.
"""

import csv
import hashlib
import io
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import ConflictError, ValidationError
from app.db.base import utcnow
from app.models import CostRecord, ImportBatch, Product, Store, TrafficDaily, Variant
from app.models.enums import DataSource
from app.services.audit import audit
from app.services.parsing import norm_key, parse_date, parse_number

COST_ALIASES = {
    "sku": ["sku", "variant sku"],
    "variant_id": ["variant id", "variant_id", "shopify variant id"],
    "unit_cost": ["unit cost", "unit_cost", "cost", "cost per item", "cogs"],
    "currency": ["currency"],
    "effective_from": ["effective from", "effective_from", "valid from", "date"],
}
TRAFFIC_ALIASES = {
    "day": ["day", "date"],
    "sessions": ["sessions", "online store sessions", "visits"],
    "added_to_cart": ["sessions with cart additions", "added to cart", "add to cart", "added_to_cart"],
    "reached_checkout": ["sessions that reached checkout", "reached checkout", "checkouts", "reached_checkout"],
    "converted": ["sessions that completed checkout", "converted", "conversions", "orders"],
    "product": ["product", "product handle", "handle", "sku"],
}


@dataclass
class CsvReport:
    kind: str
    filename: str
    dry_run: bool
    rows_total: int = 0
    rows_imported: int = 0
    rows_skipped: int = 0
    issues: list[dict] = field(default_factory=list)
    batch_id: int | None = None


def _read(content: bytes) -> tuple[list[str], list[list[str]]]:
    text = content.decode("utf-8-sig", errors="replace")
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    rows = list(csv.reader(io.StringIO(text), dialect))
    if not rows:
        raise ValidationError("The file is empty.")
    return rows[0], rows[1:]


def _map(headers: list[str], aliases: dict[str, list[str]]) -> dict[str, int]:
    keys = [norm_key(h) for h in headers]
    out = {}
    for field_name, names in aliases.items():
        for n in names:
            if norm_key(n) in keys:
                out[field_name] = keys.index(norm_key(n))
                break
    return out


def _guard(db: Session, store: Store, kind: str, sha: str, force: bool, dry_run: bool) -> None:
    if store.is_demo:
        raise ValidationError("Imports into demo stores are not allowed.")
    if dry_run or force:
        return
    prior = db.scalar(select(ImportBatch).where(ImportBatch.store_id == store.id, ImportBatch.kind == kind,
                                                ImportBatch.file_sha256 == sha, ImportBatch.status == "completed"))
    if prior:
        raise ConflictError(f"This file was already imported for {store.code} on {prior.created_at:%Y-%m-%d}.",
                            details={"batch_id": prior.id})


def import_costs(db: Session, store: Store, content: bytes, filename: str, *, dry_run: bool = True,
                 force: bool = False) -> CsvReport:
    sha = hashlib.sha256(content).hexdigest()
    _guard(db, store, "costs_csv", sha, force, dry_run)
    headers, rows = _read(content)
    cols = _map(headers, COST_ALIASES)
    if "unit_cost" not in cols or not ({"sku", "variant_id"} & cols.keys()):
        raise ValidationError("Costs CSV needs a 'unit_cost' column and either 'sku' or 'variant_id'.")
    variants = db.scalars(select(Variant).where(Variant.store_id == store.id)).all()
    by_sku = {}
    dup_skus = set()
    for v in variants:
        if v.sku:
            k = v.sku.strip().upper()
            if k in by_sku:
                dup_skus.add(k)
            by_sku[k] = v
    by_vid = {v.source_id.rsplit("/", 1)[-1]: v for v in variants}
    rep = CsvReport("costs_csv", filename, dry_run)
    batch = _start_batch(db, store, "costs_csv", filename, sha, dry_run)
    now = utcnow()
    for rn, row in enumerate(rows, start=2):
        if not any(c.strip() for c in row):
            continue
        rep.rows_total += 1
        get = lambda k: row[cols[k]].strip() if k in cols and cols[k] < len(row) else ""
        cost, issue = parse_number(get("unit_cost"))
        if cost is None or cost < 0:
            rep.rows_skipped += 1
            rep.issues.append({"row": rn, "issue": issue or f"invalid unit cost {get('unit_cost')!r}"})
            continue
        variant = None
        if get("variant_id"):
            variant = by_vid.get(get("variant_id").rsplit("/", 1)[-1])
        if variant is None and get("sku"):
            sku = get("sku").upper()
            if sku in dup_skus:
                rep.rows_skipped += 1
                rep.issues.append({"row": rn, "issue": f"SKU {sku} is shared by several variants; use variant_id"})
                continue
            variant = by_sku.get(sku)
        if variant is None:
            rep.rows_skipped += 1
            rep.issues.append({"row": rn, "issue": f"no variant in {store.code} matches "
                                                   f"{get('variant_id') or get('sku')!r}"})
            continue
        eff, _ = parse_date(get("effective_from")) if get("effective_from") else (None, None)
        if variant.price and cost > variant.price:
            rep.issues.append({"row": rn, "issue": f"unit cost {cost} exceeds current price {variant.price} "
                                                   f"for {variant.sku or variant.source_id} — check"})
        rep.rows_imported += 1
        if not dry_run:
            db.add(CostRecord(store_id=store.id, variant_id=variant.id, sku=variant.sku, unit_cost=cost,
                              currency=get("currency") or store.currency, effective_from=eff or now.date(),
                              source=DataSource.CSV_IMPORT, import_batch_id=batch.id, created_at=now))
    return _finish(db, store, batch, rep)


def import_traffic(db: Session, store: Store, content: bytes, filename: str, *, dry_run: bool = True,
                   force: bool = False) -> CsvReport:
    sha = hashlib.sha256(content).hexdigest()
    _guard(db, store, "traffic_csv", sha, force, dry_run)
    headers, rows = _read(content)
    cols = _map(headers, TRAFFIC_ALIASES)
    if "day" not in cols or "sessions" not in cols:
        raise ValidationError("Traffic CSV needs 'day' (or 'date') and 'sessions' columns.")
    products = db.scalars(select(Product).where(Product.store_id == store.id)).all()
    by_handle = {(p.handle or "").lower(): p for p in products if p.handle}
    by_sku = {}
    for v in db.scalars(select(Variant).where(Variant.store_id == store.id)):
        if v.sku:
            by_sku[v.sku.upper()] = v.product_id
    rep = CsvReport("traffic_csv", filename, dry_run)
    batch = _start_batch(db, store, "traffic_csv", filename, sha, dry_run)
    seen: set[tuple[date, int | None]] = set()
    for rn, row in enumerate(rows, start=2):
        if not any(c.strip() for c in row):
            continue
        rep.rows_total += 1
        get = lambda k: row[cols[k]].strip() if k in cols and cols[k] < len(row) else ""
        day, issue = parse_date(get("day"))
        sessions, _ = parse_number(get("sessions"), kind="count")
        if day is None or sessions is None:
            rep.rows_skipped += 1
            rep.issues.append({"row": rn, "issue": issue or "missing day or sessions"})
            continue
        product_id = None
        if get("product"):
            ref = get("product")
            product_id = by_handle.get(ref.lower()) and by_handle[ref.lower()].id or by_sku.get(ref.upper())
            if product_id is None:
                rep.rows_skipped += 1
                rep.issues.append({"row": rn, "issue": f"no product in {store.code} matches {ref!r}"})
                continue
        if (day, product_id) in seen:
            rep.rows_skipped += 1
            rep.issues.append({"row": rn, "issue": f"duplicate row for {day} in the same file"})
            continue
        seen.add((day, product_id))
        nums = {k: parse_number(get(k), kind="count")[0] for k in ("added_to_cart", "reached_checkout", "converted")}
        if nums["converted"] is not None and nums["converted"] > sessions:
            rep.issues.append({"row": rn, "issue": "conversions exceed sessions — check the export"})
        rep.rows_imported += 1
        if dry_run:
            continue
        existing = db.scalar(select(TrafficDaily).where(
            TrafficDaily.store_id == store.id, TrafficDaily.day == day, TrafficDaily.source == DataSource.CSV_IMPORT,
            TrafficDaily.product_id.is_(None) if product_id is None else TrafficDaily.product_id == product_id))
        rec = existing or TrafficDaily(store_id=store.id, product_id=product_id, day=day, sessions=0,
                                       source=DataSource.CSV_IMPORT)
        rec.sessions = int(sessions)
        rec.added_to_cart = int(nums["added_to_cart"]) if nums["added_to_cart"] is not None else None
        rec.reached_checkout = int(nums["reached_checkout"]) if nums["reached_checkout"] is not None else None
        rec.converted = int(nums["converted"]) if nums["converted"] is not None else None
        rec.import_batch_id = batch.id
        if existing is None:
            db.add(rec)
    return _finish(db, store, batch, rep)


def _start_batch(db, store, kind, filename, sha, dry_run):
    if dry_run:
        return None
    batch = ImportBatch(store_id=store.id, kind=kind, filename=filename, file_sha256=sha, status="running",
                        created_at=utcnow())
    db.add(batch)
    db.flush()
    return batch


def _finish(db, store, batch, rep: CsvReport) -> CsvReport:
    if batch is None:
        db.rollback()
        return rep
    batch.status = "completed"
    batch.rows_total = rep.rows_total
    batch.rows_imported = rep.rows_imported
    batch.rows_skipped = rep.rows_skipped
    batch.warnings = rep.issues[:2000]
    rep.batch_id = batch.id
    audit(db, f"import.{rep.kind}", "import_batch", batch.id, store_id=store.id,
          details={"filename": rep.filename, "imported": rep.rows_imported, "skipped": rep.rows_skipped})
    db.commit()
    return rep
