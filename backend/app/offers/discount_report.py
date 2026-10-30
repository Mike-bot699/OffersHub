"""Shopify "Sales by discount code" report: import and summary.

Accepts the Shopify Analytics export (JSONL or CSV) for one store and period, optionally split
by day. The report counts an order once per discount it used, so an order with a code and an
automatic bundle appears in two rows: per-discount figures are exact, the sum of orders across
discounts can exceed the number of distinct discounted orders. The summary says so.

Two clean-ups, declared in the output rather than applied silently:
- single-use codes generated in bulk (same prefix, e.g. KNO-XXXXXXXX, five or more names with one
  order each) are grouped into one line, "KNO-* (single-use codes)";
- names containing TEST are test orders: kept in the file, excluded from totals and rankings.
"""

import csv
import hashlib
import io
import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.errors import ValidationError
from app.db.base import utcnow
from app.models import DiscountReport, DiscountReportRow, PromoEvent, Store
from app.services.audit import audit

FIELDS = {  # normalised header -> row field
    "discount_name": "name", "discount_code": "name", "discount": "name", "name": "name",
    "discount_method": "method", "method": "method",
    "discount_type": "discount_type", "type": "discount_type",
    "discount_class": "discount_class", "class": "discount_class",
    "applied_discounts": "discounts", "discounts": "discounts", "discount_amount": "discounts",
    "discounted_orders": "orders", "orders": "orders",
    "gross_sales": "gross_sales", "sales_reversals": "reversals", "returns": "reversals",
    "net_sales": "net_sales", "day": "day", "date": "day",
}
SINGLE_USE = re.compile(r"^([A-Z]{2,6})-[A-Z0-9_\-]{6,}$")
FILENAME_PERIOD = re.compile(r"(\d{4}-\d{2}-\d{2})\D+(\d{4}-\d{2}-\d{2})")


def _key(h: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", h.strip().lower()).strip("_")


def _num(v) -> float | None:
    if v in (None, ""):
        return None
    try:
        return float(re.sub(r"[^0-9.\-]", "", str(v)) or "x")
    except ValueError:
        return None


def _rows(content: bytes, filename: str) -> list[dict]:
    text = content.decode("utf-8-sig", errors="replace")
    if filename.lower().endswith((".jsonl", ".json", ".ndjson")) or text.lstrip().startswith("{"):
        raw = []
        for i, line in enumerate(text.splitlines(), start=1):
            if line.strip():
                try:
                    raw.append(json.loads(line))
                except json.JSONDecodeError as exc:
                    raise ValidationError(f"Line {i} is not valid JSON.") from exc
    elif filename.lower().endswith(".csv"):
        raw = list(csv.DictReader(io.StringIO(text)))
    else:
        raise ValidationError("Upload the Shopify export as .jsonl or .csv.")
    out = map_rows(raw)
    if not out:
        raise ValidationError("No discount rows found: the file needs a discount_name (or 'Discount name') column.")
    return out


def map_rows(raw: list[dict]) -> list[dict]:
    """Rows from any source (file or platform API) to the report's fields; rows without a name are dropped."""
    out = []
    for r in raw:
        row = {}
        for k, v in r.items():
            f = FIELDS.get(_key(str(k)))
            if f and f not in row:
                row[f] = v
        if row.get("name"):
            out.append(row)
    return out


def period_from_filename(filename: str) -> tuple[date, date] | None:
    m = FILENAME_PERIOD.search(filename or "")
    if not m:
        return None
    return date.fromisoformat(m.group(1)), date.fromisoformat(m.group(2))


@dataclass
class DiscountImport:
    dry_run: bool
    rows: int
    period_start: str
    period_end: str
    by_day: bool
    codes: int
    automatic: int
    test_rows: list[str] = field(default_factory=list)
    replaced_previous: bool = False
    report_id: int | None = None


def import_discount_report(db: Session, store: Store, content: bytes, filename: str, *, start: date | None,
                           end: date | None, dry_run: bool = True) -> DiscountImport:
    if store.is_demo:
        raise ValidationError("Imports into the demo store are disabled.")
    rows = _rows(content, filename)
    guessed = period_from_filename(filename)
    start = start or (guessed[0] if guessed else None)
    end = end or (guessed[1] if guessed else None)
    if not start or not end:
        raise ValidationError("Set the report period (start and end date): it is not in the file name.")
    if end < start:
        raise ValidationError("End date is before start date.")
    parsed = parse_rows(rows)
    rep = DiscountImport(dry_run=dry_run, rows=len(parsed), period_start=start.isoformat(), period_end=end.isoformat(),
                         by_day=any(p["day"] for p in parsed),
                         codes=len({p["name"] for p in parsed if p["method"] != "automatic"}),
                         automatic=len({p["name"] for p in parsed if p["method"] == "automatic"}),
                         test_rows=sorted({p["name"] for p in parsed if "TEST" in p["name"].upper()}))
    existing = db.scalar(select(DiscountReport).where(DiscountReport.store_id == store.id,
                                                      DiscountReport.period_start == start,
                                                      DiscountReport.period_end == end))
    rep.replaced_previous = existing is not None
    if dry_run:
        return rep
    if existing:
        _drop(db, existing)
    report = save_report(db, store, parsed, start, end, source="shopify_export", filename=filename,
                         sha=hashlib.sha256(content).hexdigest())
    rep.report_id = report.id
    return rep


def parse_rows(rows: list[dict]) -> list[dict]:
    parsed = []
    for r in rows:
        day = None
        if r.get("day"):
            try:
                day = date.fromisoformat(str(r["day"])[:10])
            except ValueError as exc:
                raise ValidationError(f"Unreadable day '{r['day']}'.") from exc
        parsed.append({
            "day": day, "name": str(r["name"]).strip()[:300], "method": (str(r.get("method") or "").strip() or None),
            "discount_type": (str(r.get("discount_type") or "").strip() or None),
            "discount_class": (str(r.get("discount_class") or "").strip() or None),
            "orders": int(_num(r.get("orders")) or 0), "discounts": abs(_num(r.get("discounts")) or 0.0),
            "gross_sales": _num(r.get("gross_sales")), "reversals": _num(r.get("reversals")), "net_sales": _num(r.get("net_sales"))})
    return parsed


def _drop(db: Session, report: DiscountReport) -> None:
    db.execute(delete(DiscountReportRow).where(DiscountReportRow.report_id == report.id))
    db.delete(report)
    db.flush()


def save_report(db: Session, store: Store, parsed: list[dict], start: date, end: date, *, source: str,
                filename: str | None, sha: str | None) -> DiscountReport:
    report = DiscountReport(store_id=store.id, period_start=start, period_end=end, source=source,
                            filename=(filename or "")[:255] or None, file_sha256=sha,
                            by_day=any(p["day"] for p in parsed), created_at=utcnow())
    db.add(report)
    db.flush()
    db.add_all([DiscountReportRow(report_id=report.id, store_id=store.id, **p) for p in parsed])
    audit(db, "import.discount_report", "discount_report", report.id, store_id=store.id,
          details={"source": source, "filename": filename, "rows": len(parsed), "period": [start.isoformat(), end.isoformat()]})
    db.flush()
    return report


def replace_platform_report(db: Session, store: Store, parsed: list[dict], start: date, end: date) -> DiscountReport:
    """The platform report is one rolling report per store, replaced at every sync."""
    for old in db.scalars(select(DiscountReport).where(DiscountReport.store_id == store.id, DiscountReport.source == "elizeta")):
        _drop(db, old)
    return save_report(db, store, parsed, start, end, source="elizeta", filename=None, sha=None)


def _r(v, nd=2):
    return None if v is None else round(v, nd)


def latest_report(db: Session, store: Store, start: date | None = None, end: date | None = None) -> DiscountReport | None:
    """The report that best covers the period: the most recent one overlapping it, else the latest."""
    q = select(DiscountReport).where(DiscountReport.store_id == store.id)
    if start and end:
        hit = db.scalar(q.where(DiscountReport.period_start <= end, DiscountReport.period_end >= start)
                        .order_by(DiscountReport.period_end.desc(), DiscountReport.created_at.desc()).limit(1))
        if hit:
            return hit
    return db.scalar(q.order_by(DiscountReport.period_end.desc(), DiscountReport.created_at.desc()).limit(1))


def summarize(db: Session, report: DiscountReport, *, top: int | None = None) -> dict:
    rows = db.scalars(select(DiscountReportRow).where(DiscountReportRow.report_id == report.id)).all()
    by_name: dict[str, dict] = defaultdict(lambda: {"orders": 0, "discounts": 0.0, "gross_sales": 0.0, "net_sales": 0.0,
                                                    "method": None, "discount_class": None, "last_day": None})
    for r in rows:
        a = by_name[r.name]
        a["orders"] += r.orders
        a["discounts"] += r.discounts
        a["gross_sales"] += r.gross_sales or 0.0
        a["net_sales"] += r.net_sales or 0.0
        a["method"], a["discount_class"] = r.method, r.discount_class
        if r.day and (a["last_day"] is None or r.day > a["last_day"]):
            a["last_day"] = r.day
    tests = {n for n in by_name if "TEST" in n.upper()}
    # Bulk single-use codes: same prefix, at least five names, one order each.
    prefixes: dict[str, list[str]] = defaultdict(list)
    for n, a in by_name.items():
        m = SINGLE_USE.match(n)
        if m and a["orders"] <= 1 and n not in tests:
            prefixes[m.group(1)].append(n)
    grouped = {n: f"{p}-* (single-use codes)" for p, names in prefixes.items() if len(names) >= 5 for n in names}
    lines: dict[str, dict] = {}
    for n, a in by_name.items():
        if n in tests:
            continue
        key = grouped.get(n, n)
        line = lines.setdefault(key, {"name": key, "method": a["method"], "discount_class": a["discount_class"],
                                      "orders": 0, "discounts": 0.0, "gross_sales": 0.0, "net_sales": 0.0,
                                      "codes_in_group": 0, "last_day": None})
        line["orders"] += a["orders"]
        line["discounts"] += a["discounts"]
        line["gross_sales"] += a["gross_sales"]
        line["net_sales"] += a["net_sales"]
        line["codes_in_group"] += 1
        if a["last_day"] and (line["last_day"] is None or a["last_day"] > line["last_day"]):
            line["last_day"] = a["last_day"]
    coupon_info = store_coupons(db, report.store_id)
    placements = store_placements(db, report.store_id)
    # Which offers use each code (codes are stored upper-case on offers).
    offers: dict[str, list[str]] = defaultdict(list)
    for e in db.scalars(select(PromoEvent).where(PromoEvent.store_id == report.store_id)):
        for c in [*(e.coupon_codes or []), *(e.linked_discounts or [])]:
            offers[c.upper()].append(e.name)
    out_lines = []
    for ln in sorted(lines.values(), key=lambda x: (-x["orders"], -x["net_sales"])):
        out_lines.append({
            **ln, "discounts": round(ln["discounts"], 2), "gross_sales": round(ln["gross_sales"], 2),
            "net_sales": round(ln["net_sales"], 2), "aov": _r(ln["net_sales"] / ln["orders"]) if ln["orders"] else None,
            "discount_rate": _r(ln["discounts"] / ln["gross_sales"], 4) if ln["gross_sales"] else None,
            "last_day": ln["last_day"].isoformat() if ln["last_day"] else None,
            "offers": offers.get(ln["name"].upper(), []),
            "store": coupon_info.get(ln["name"].upper()),
            "placements": placements.get(ln["name"].upper(), []),
        })
    codes = [x for x in out_lines if x["method"] != "automatic"]
    auto = [x for x in out_lines if x["method"] == "automatic"]

    def tot(xs):
        o = sum(x["orders"] for x in xs)
        g = sum(x["gross_sales"] for x in xs)
        d = sum(x["discounts"] for x in xs)
        n = sum(x["net_sales"] for x in xs)
        return {"orders": o, "discounts": round(d, 2), "gross_sales": round(g, 2), "net_sales": round(n, 2),
                "aov": _r(n / o) if o else None, "discount_rate": _r(d / g, 4) if g else None}

    return {
        "report": {"id": report.id, "period_start": report.period_start.isoformat(), "period_end": report.period_end.isoformat(),
                   "source": report.source, "filename": report.filename, "by_day": report.by_day,
                   "imported_at": report.created_at.isoformat() if isinstance(report.created_at, datetime) else None},
        "totals": {"all": tot(out_lines), "codes": tot(codes), "automatic": tot(auto)},
        "lines": out_lines[:top] if top else out_lines,
        "excluded_tests": sorted(tests),
        # Discounts in the store list that the report never saw used (e.g. new or unused codes).
        "unused": [{"name": v["title"], "store": v, "offers": offers.get(k, []), "placements": placements.get(k, [])}
                   for k, v in sorted(coupon_info.items())
                   if k not in {n.upper() for n in by_name} and "TEST" not in k],
        "grouped": {g: sum(1 for v in grouped.values() if v == g) for g in set(grouped.values())},
        "note": "Orders are counted once per discount used: an order with a code and an automatic discount appears "
                "in both lines, so the order total across discounts can exceed distinct discounted orders.",
    }


def offer_results(db: Session, rows: list[dict]) -> None:
    """Attach `discount_results` to serialised offers: what their codes (and linked automatic
    discounts) did in the store's latest discount report.

    With a report split by day, only the offer's own dates count and later use is reported as late
    redemptions. Without days, the figures are the code's totals for the whole report period; a
    code used by several offers cannot be split between them and is flagged as shared."""
    by_store: dict[int, list[dict]] = defaultdict(list)
    for r in rows:
        by_store[r["store_id"]].append(r)
    for store_id, items in by_store.items():
        reports = db.scalars(select(DiscountReport).where(DiscountReport.store_id == store_id)
                             .order_by(DiscountReport.period_end.desc(), DiscountReport.created_at.desc())).all()
        if not reports:
            for r in items:
                r["discount_results"] = None
            continue
        events = db.scalars(select(PromoEvent).where(PromoEvent.store_id == store_id)).all()
        cache: dict[int, tuple] = {}

        def load(report: DiscountReport) -> tuple:
            if report.id not in cache:
                by_name: dict[str, list[DiscountReportRow]] = defaultdict(list)
                for ln in db.scalars(select(DiscountReportRow).where(DiscountReportRow.report_id == report.id)):
                    by_name[ln.name.upper()].append(ln)
                users: dict[str, set[int]] = defaultdict(set)
                for e in events:
                    if e.start_date and e.start_date > report.period_end:
                        continue  # starts after the report: cannot share its figures
                    for n in [*(e.coupon_codes or []), *(e.linked_discounts or [])]:
                        users[n.upper()].add(e.id)
                cache[report.id] = (by_name, users)
            return cache[report.id]

        for r in items:
            names = [n for n in [*(r.get("coupon_codes") or []), *(r.get("linked_discounts") or [])] if n]
            if not names:
                r["discount_results"] = None
                continue
            start = date.fromisoformat(r["start_date"]) if r.get("start_date") else None
            end = date.fromisoformat(r["end_date"]) if r.get("end_date") else None
            # The newest report covering the offer's start (a recent platform report may not reach older offers).
            report = next((rp for rp in reports if start and rp.period_start <= start <= rp.period_end), reports[0])
            by_name, users = load(report)
            if start and start > report.period_end:
                r["discount_results"] = {"status": "after_report", "report_end": report.period_end.isoformat()}
                continue
            inside: list[DiscountReportRow] = []
            late: list[DiscountReportRow] = []
            matched, missing = [], []
            for n in names:
                got = by_name.get(n.upper(), [])
                (matched if got else missing).append(n)
                for ln in got:
                    if report.by_day and ln.day and start and end:
                        if start <= ln.day <= end:
                            inside.append(ln)
                        elif ln.day > end:
                            late.append(ln)
                    else:
                        inside.append(ln)
            shared = sorted({u for n in matched for u in users.get(n.upper(), set())} - {r["id"]})
            o = sum(x.orders for x in inside)
            n_sales = sum(x.net_sales or 0 for x in inside)
            r["discount_results"] = {
                "status": "ok" if matched else "no_match",
                "report_period": [report.period_start.isoformat(), report.period_end.isoformat()],
                "by_day": report.by_day, "matched": matched, "missing": missing,
                "orders": o, "discounts": round(sum(x.discounts for x in inside), 2), "net_sales": round(n_sales, 2),
                "aov": _r(n_sales / o) if o else None,
                # Without days a shared code's totals belong to several offers: say so instead of splitting.
                "shared_with": len(shared) if not report.by_day else 0,
                "late": {"orders": sum(x.orders for x in late), "discounts": round(sum(x.discounts for x in late), 2)}
                if report.by_day and end else None,
            }


def store_coupons(db: Session, store_id: int) -> dict[str, dict]:
    """Store discounts (Shopify sync or admin export) by upper-case code, newest evidence first."""
    from app.models import Coupon

    out: dict[str, dict] = {}
    for c in db.scalars(select(Coupon).where(Coupon.store_id == store_id).order_by(Coupon.last_synced_at.desc())):
        if c.code in out:
            continue
        out[c.code] = {"title": c.title or c.code, "status": c.status, "value": c.value, "value_type": c.value_type,
                       "min_subtotal": c.min_subtotal, "min_quantity": c.min_quantity,
                       "starts_at": c.starts_at.isoformat() if c.starts_at else None,
                       "ends_at": c.ends_at.isoformat() if c.ends_at else None, "usage_count": c.usage_count,
                       "source": c.source, "checked_at": c.last_synced_at.isoformat() if c.last_synced_at else None}
    return out


def store_placements(db: Session, store_id: int) -> dict[str, list[str]]:
    from app.models import DiscountPlacement

    return {p.code: p.placements or [] for p in db.scalars(select(DiscountPlacement).where(DiscountPlacement.store_id == store_id))}
