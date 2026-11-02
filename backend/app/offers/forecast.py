"""Forecast estimate from the same event last year.

The event's `compare_codes` (last year's codes, read from the calendar notes, e.g. "2025 BF2025 at
20%…", or set by hand) are looked up in the store's sales-by-discount data. With daily data the
orders are scaled to this year's length (orders per active day × days); without it, the code's
total is used as is. AOV = last year's net sales / orders. No data for those codes: no estimate.
"""

import re
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import DiscountReport, DiscountReportRow, PromoEvent

NOTE_CODE = re.compile(r"\b(20\d\d)\s*:?\s+(?:ran\s+)?([A-Z][A-Z0-9]{3,})\b")


def codes_from_notes(notes: str | None, year: int | None) -> list[str]:
    """Last year's codes cited in the notes ("2025 BF2025 …", "2025: BOO15 ran …")."""
    if not notes or not year:
        return []
    return list(dict.fromkeys(c for y, c in NOTE_CODE.findall(notes) if int(y) == year - 1 and re.search(r"\d", c)))


YEAR = timedelta(days=364)  # 52 weeks: last year's same weekdays (Black Friday moves with them)
MARGIN = timedelta(days=21)


def last_year(db: Session, store_id: int, codes: list[str], start: date, end: date) -> dict | None:
    """Last year's figures for the codes in the same season: daily rows within three weeks of this
    event's dates a year earlier, or a report without days that covers those dates. Uses outside
    that season (e.g. a late redemption in January) are not last year's event."""
    wanted = {c.upper() for c in codes}
    if not wanted:
        return None
    ly_start, ly_end = start - YEAR, end - YEAR
    reports = db.scalars(select(DiscountReport).where(DiscountReport.store_id == store_id)
                         .order_by(DiscountReport.by_day.desc(), DiscountReport.period_end.desc())).all()
    for rep in reports:
        if not rep.by_day and not (rep.period_start <= ly_start and rep.period_end >= ly_end):
            continue
        rows = [r for r in db.scalars(select(DiscountReportRow).where(DiscountReportRow.report_id == rep.id))
                if r.name.upper() in wanted and (not rep.by_day or (r.day and ly_start - MARGIN <= r.day <= ly_end + MARGIN))]
        if not rows:
            continue
        orders = sum(r.orders for r in rows)
        if not orders:
            continue
        net = sum(r.net_sales or 0 for r in rows)
        days = sorted({r.day for r in rows if r.day and r.orders})
        span = (days[-1] - days[0]).days + 1 if days else None
        found = sorted({r.name.upper() for r in rows})
        return {"codes": found, "orders": orders, "aov": net / orders if orders else None, "days": span,
                "by_day": bool(days)}
    return None


def estimate(db: Session, e: PromoEvent, cache: dict | None = None) -> dict | None:
    if not e.compare_codes or not e.start_date or not e.end_date:
        return None
    key = (e.store_id, tuple(sorted(e.compare_codes)), e.start_date, e.end_date)
    if cache is not None and key in cache:
        ly = cache[key]
    else:
        ly = last_year(db, e.store_id, e.compare_codes, e.start_date, e.end_date)
        if cache is not None:
            cache[key] = ly
    if not ly:
        return _from_usage(db, e)
    days = (e.end_date - e.start_date).days + 1
    orders = round(ly["orders"] / ly["days"] * days) if ly["by_day"] and ly["days"] else ly["orders"]
    basis = f"last year {', '.join(ly['codes'])}: {ly['orders']:,} orders" + (f" in {ly['days']} days" if ly["days"] else "")
    return {"orders": max(orders, 1), "aov": ly["aov"], "basis": basis}



def _from_usage(db: Session, e: PromoEvent) -> dict | None:
    """Fallback without last year's sales: the codes' total uses in the store's discount list
    (Shopify export or Eli Z). One use is one order; no AOV; uses after the event are included."""
    from app.models import Coupon

    wanted = {c.upper() for c in e.compare_codes}
    best: dict[str, Coupon] = {}
    for c in db.scalars(select(Coupon).where(Coupon.store_id == e.store_id, Coupon.code.in_(wanted))):
        if c.usage_count and (c.code not in best or c.usage_count > best[c.code].usage_count):
            best[c.code] = c
    if not best:
        return None
    uses = sum(c.usage_count for c in best.values())
    fmt = lambda d: f"{d.day} {d.strftime('%b')}"
    # When the code ran longer than the event, its total includes uses after it: say so.
    windows = [f"{c.code} active {fmt(c.starts_at)} to {fmt(c.ends_at)}" for c in best.values()
               if c.starts_at and c.ends_at and (c.ends_at - c.starts_at).days + 1 > (e.end_date - e.start_date).days + 1 + 21]
    basis = f"last year {', '.join(sorted(best))}: used {uses:,} times in total"
    if windows:
        basis += f" ({'; '.join(windows)}, uses after the event included)"
    return {"orders": uses, "aov": None, "basis": basis}
