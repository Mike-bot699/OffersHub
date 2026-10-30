"""Shopify admin "Discounts" export (Discounts → Export): the store's discounts with status.

Each row becomes a store coupon with source `shopify_export`, which then serves as evidence for
the technical status of offers (Active / Expired in Shopify on the import date).

Declared choices:
- Shopify exports one page (50 discounts) unless "All discounts" is chosen; a file of exactly 50
  rows is flagged as possibly partial, and a code missing from the export is never reported as
  "not found in Shopify" — it stays not verified.
- "Minimum Purchase Requirements" holds an amount or an item count: values under 20 are read as a
  number of items (e.g. BUY3 → 3), larger values as an order amount.
- Names containing spaces are app or automatic discounts (e.g. "Kaching Bundles - …"); codes
  have no spaces.
"""

import csv
import io
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import ValidationError
from app.db.base import utcnow
from app.models import Coupon, Store
from app.services.audit import audit

SOURCE = "shopify_export"
PAGE_SIZE = 50
VALUE_TYPES = {"percentage": "percent", "fixed_amount": "fixed", "app": None}


def _num(v: str | None) -> float | None:
    try:
        return float(v) if v not in (None, "") else None
    except ValueError:
        return None


def _dt(v: str | None) -> datetime | None:
    if not v:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S %z", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d"):
        try:
            return datetime.strptime(v.strip(), fmt)
        except ValueError:
            continue
    raise ValidationError(f"Unreadable date '{v}'.")


@dataclass
class DiscountListImport:
    dry_run: bool
    rows: int
    active: int
    expired: int
    scheduled: int
    automatic: int
    created: int = 0
    updated: int = 0
    possibly_partial: bool = False
    warnings: list[str] = field(default_factory=list)


def import_discount_list(db: Session, store: Store, content: bytes, filename: str, *, dry_run: bool = True) -> DiscountListImport:
    if store.is_demo:
        raise ValidationError("Imports into the demo store are disabled.")
    text = content.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames or "Name" not in reader.fieldnames or "Status" not in reader.fieldnames:
        raise ValidationError("This is not the Shopify Discounts export: it needs the Name and Status columns.")
    rows = [r for r in reader if (r.get("Name") or "").strip()]
    if not rows:
        raise ValidationError("The file has no discounts.")
    status = [(r.get("Status") or "").strip().lower() for r in rows]
    rep = DiscountListImport(dry_run=dry_run, rows=len(rows), active=status.count("active"),
                             expired=status.count("expired"), scheduled=status.count("scheduled"),
                             automatic=sum(1 for r in rows if " " in r["Name"].strip()))
    if len(rows) == PAGE_SIZE:
        rep.possibly_partial = True
        rep.warnings.append("The file has exactly 50 discounts, one Shopify page: export again choosing "
                            "\"All discounts\" to include every code. Codes missing from the file stay not verified.")
    now = utcnow()
    existing = {c.code: c for c in db.scalars(select(Coupon).where(Coupon.store_id == store.id, Coupon.source == SOURCE))}
    for r in rows:
        name = r["Name"].strip()
        code = name.upper()[:128]
        mp = _num(r.get("Minimum Purchase Requirements"))
        fields = {
            "title": name[:300], "discount_class": (r.get("Discount Class") or None),
            "value_type": "free_shipping" if (r.get("Type") or "") == "Free Shipping"
            else "bxgy" if (r.get("Type") or "") == "Buy X Get Y" else VALUE_TYPES.get(r.get("Value Type") or "", None),
            "value": abs(_num(r.get("Value")) or 0) or None,
            "min_subtotal": mp if mp is not None and mp >= 20 else None,
            "min_quantity": int(mp) if mp is not None and mp < 20 else None,
            "starts_at": _dt(r.get("Start")), "ends_at": _dt(r.get("End")),
            "status": (r.get("Status") or "").strip().lower() or None,
            "usage_count": int(_num(r.get("Times Used In Total")) or 0),
            "usage_limit": int(_num(r.get("Usage Limit Per Code"))) if _num(r.get("Usage Limit Per Code")) else None,
            "once_per_customer": (r.get("Applies Once Per Customer") or "").strip() == "1",
            "channel": "automatic" if " " in name else "code", "last_synced_at": now,
        }
        if dry_run:
            continue
        c = existing.get(code)
        if c is None:
            c = Coupon(store_id=store.id, code=code, source=SOURCE)
            db.add(c)
            existing[code] = c
            rep.created += 1
        else:
            rep.updated += 1
        for k, v in fields.items():
            setattr(c, k, v)
    if not dry_run:
        # Whether the latest export covers every discount: only then is "missing" proof of absence.
        store.constraints = {**(store.constraints or {}), "discount_export": {
            "complete": not rep.possibly_partial, "imported_at": now.isoformat(), "rows": len(rows)}}
        db.flush()
        audit(db, "import.discount_list", "store", store.id, store_id=store.id,
              details={"filename": filename, "rows": len(rows), "possibly_partial": rep.possibly_partial})
        from app.offers.status import sync_store_offers

        sync_store_offers(db, store)  # refresh every offer's technical status with the new evidence
    return rep
