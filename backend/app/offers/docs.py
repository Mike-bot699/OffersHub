"""Live offer docs: a saved selection composed again on every visit, so a shared link always
shows the current Offers Plan.

Declared choices:
- Amounts are shown in USD ($), as agreed for docs.
- An offer is in the range when its dates overlap it; undated offers are left out, except
  product-level offers (usually undated), which go to "Product offers".
- The Offer line is the offer's description when written, otherwise its Offer text.
- "Products to focus" is recalculated from the catalog's performance and manual tags.
"""

import calendar
import re
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models import Coupon, EventChange, ExtraActivity, OfferDoc, Product, PromoEvent, Store, TagStyle, Variant
from app.offers.changes import TRACKED
from app.offers.forecast import estimate as forecast_estimate
from app.offers.offer_text import describe as describe_offer

DEFAULT_STATUSES = ["live", "scheduled", "in_progress"]
SECTIONS = {"focus": True, "events": True, "product_offers": True, "extra_activities": True, "bonus": True,
            "forecast": True, "extra_details": True, "changes": True}
CHANGES_DAYS = 14
DISPLAY = {"full_site": "Home page, collection, product page", "email_only": "Email only, not shown on the site",
           "hidden": "Not shown"}
PERF = {"best_seller": "Best Seller", "top_seller": "Top Seller", "popular": "Popular", "free": "Free"}
CHANNELS = {"facebook": "Facebook", "email": "Email", "google": "Google"}


def _aware(dt: datetime | None) -> datetime | None:
    return None if dt is None else dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def doc_range(doc: OfferDoc, today: date) -> tuple[date, date]:
    if doc.range_mode == "next_days":
        return today, today + timedelta(days=max(1, doc.days or 30) - 1)
    if doc.range_mode in ("this_month", "next_month"):
        y, m = today.year, today.month
        if doc.range_mode == "next_month":
            y, m = (y + 1, 1) if m == 12 else (y, m + 1)
        return date(y, m, 1), date(y, m, calendar.monthrange(y, m)[1])
    start = doc.start_date or today
    return start, doc.end_date or start + timedelta(days=29)


def sections(doc: OfferDoc) -> dict[str, bool]:
    return {**SECTIONS, **{k: bool(v) for k, v in (doc.sections or {}).items() if k in SECTIONS}}


EMPTY = {"", "-", "—", "–", "n/a", "na", "none"}


def _clean(v: str | None) -> str | None:
    """Placeholders the calendar uses for "nothing" ("—", "-", "N/A") count as empty."""
    return None if v is None or str(v).strip().lower() in EMPTY else str(v).strip()


def _change_value(field: str, v: str) -> str:
    if field in ("start_date", "end_date"):
        d = date.fromisoformat(v)
        return f"{d.day} {d.strftime('%b')}"
    if field == "display":
        return {"full_site": "Full-Displayed", "email_only": "Email-Only", "hidden": "Hidden"}.get(v, v)
    if field == "threshold_amount":
        return f"${float(v):g}"
    return v


def _changes(items: list) -> list[dict]:
    """Readable recent changes: only values that existed and changed (a first fill is not a change),
    long texts summarised, at most three."""
    out = []
    for c in items:
        if not _clean(c.old) or not _clean(c.new) or c.field == "name":
            continue
        label = TRACKED.get(c.field, c.field).lower()
        old, new = _change_value(c.field, c.old), _change_value(c.field, c.new)
        out.append({"field": label, "old": None, "new": None, "text": f"{label} updated"} if max(len(old), len(new)) > 40
                   else {"field": label, "old": old, "new": new, "text": f"{label} {old} → {new}"})
    seen, uniq = set(), []
    for c in reversed(out):  # the latest change of each field
        if c["field"] not in seen:
            seen.add(c["field"])
            uniq.append(c)
    return list(reversed(uniq))[:3]


def _money(v: float | None) -> str | None:
    return None if v is None else f"${v:,.0f}" if float(v).is_integer() else f"${v:,.2f}"


def _emails(e: PromoEvent) -> list[dict]:
    """The offer's emails (subject + preview text); until some are entered, the one in the calendar's banner text."""
    if e.email_variants:
        return [{"subject": v.get("subject") or "", "preview": v.get("preview") or ""} for v in e.email_variants]
    parts = {k.lower(): v for k, v in _email_parts(e.banner_message)}
    return [{"subject": parts.get("subject", ""), "preview": parts.get("preheader", "")}] if parts.get("subject") else []


def _email_parts(text: str | None) -> list[list[str]]:
    """'Subject: … | Preheader: … | CTA: …' as pairs; other texts are not an email brief."""
    out = []
    for part in (text or "").split("|"):
        k, _, v = part.partition(":")
        if k.strip().lower() in ("subject", "preheader", "cta") and v.strip():
            out.append([k.strip().capitalize() if k.strip().lower() != "cta" else "CTA", v.strip()])
    return out


def _tag_styles(db: Session) -> dict[str, dict]:
    return {t.key: {"bg": t.bg, "fg": t.fg} for t in db.scalars(select(TagStyle))}


def _tags(names: list[str], styles: dict[str, dict]) -> list[dict]:
    return [{"name": n, **styles.get(n.lower(), {})} for n in names or []]


def _days_text(a: date, b: date) -> str:
    fmt = lambda d: f"{d.day} {d.strftime('%b')}"
    n = (b - a).days + 1
    return f"{fmt(a)} to {fmt(b)} · {n} day{'s' if n != 1 else ''}"


def assemble(db: Session, doc: OfferDoc, today: date) -> dict[str, Any]:
    start, end = doc_range(doc, today)
    sec = sections(doc)
    statuses = doc.statuses or DEFAULT_STATUSES
    styles = _tag_styles(db)
    stores = [s for s in db.scalars(select(Store).order_by(Store.code)) if not s.is_demo
              and (not doc.store_ids or s.id in doc.store_ids)]
    by_id = {s.id: s for s in stores}
    wanted = PromoEvent.status.in_([x for x in statuses if x != "not_set"])
    if "not_set" in statuses:  # offers whose status nobody has set yet
        wanted = or_(wanted, PromoEvent.status.is_(None))
    q = select(PromoEvent).where(PromoEvent.store_id.in_(by_id), wanted)
    events, product_offers = [], []
    since = today - timedelta(days=CHANGES_DAYS)
    for e in db.scalars(q.order_by(PromoEvent.start_date)):
        dated = e.start_date and e.end_date
        if e.level == "product":
            if dated and (e.end_date < start or e.start_date > end):
                continue
            product_offers.append(e)
            continue
        if not dated or e.end_date < start or e.start_date > end:
            continue
        events.append(e)
    events.sort(key=lambda e: (by_id[e.store_id].code, e.start_date, e.name))

    # The stores' discount data, so an offer can be described from its codes' values.
    coupons: dict[int, dict[str, dict]] = {}
    for c in db.scalars(select(Coupon).where(Coupon.store_id.in_(by_id)).order_by(Coupon.last_synced_at)):
        coupons.setdefault(c.store_id, {})[c.code] = {"value": c.value, "value_type": c.value_type}
    changes: dict[int, list[EventChange]] = {}
    if sec["changes"] and events:
        # Only changes made after the doc was created (or its changes box reset): edits made while
        # preparing a doc are not news for its readers.
        start_from = _aware(doc.changes_since or doc.created_at)
        for c in db.scalars(select(EventChange).where(EventChange.event_id.in_([e.id for e in events]),
                                                      EventChange.at >= since).order_by(EventChange.at)):
            if start_from is None or _aware(c.at) > start_from:
                changes.setdefault(c.event_id, []).append(c)

    ly_cache: dict = {}

    def _forecast(e: PromoEvent) -> dict | None:
        if e.forecast_orders or e.forecast_uses or e.forecast_aov:
            return {"orders": e.forecast_orders, "uses": e.forecast_uses, "aov": _money(e.forecast_aov), "estimate": False}
        est = forecast_estimate(db, e, ly_cache)
        if est:
            return {"orders": est["orders"], "uses": None, "aov": _money(est["aov"]), "estimate": True, "basis": est["basis"]}
        if e.compare_codes:  # said, not hidden: the comparison is set but last year's sales are not in OffersHub yet
            return {"orders": None, "uses": None, "aov": None, "estimate": True,
                    "missing": f"no sales data yet for last year's {', '.join(e.compare_codes)}"}
        return None

    def event_out(e: PromoEvent) -> dict:
        s = by_id[e.store_id]
        ch = changes.get(e.id, [])
        readable = _changes(ch)
        bonus = {"free_shipping": _clean(e.free_shipping), "giveaway_product": _clean(e.giveaway_product),
                 "free_gift": _clean(e.free_gift)}
        if not any(bonus.values()) and _clean(e.bonus):
            bonus["free_gift"] = _clean(e.bonus)  # older offers: one Bonus text until the three fields are filled
        targets = e.level_targets or []
        return {
            "id": e.id, "store_code": s.code, "store_name": s.name, "name": e.name, "tags": _tags(e.tags, styles),
            "start_date": e.start_date.isoformat(), "end_date": e.end_date.isoformat(),
            "when": _days_text(e.start_date, e.end_date), "status": e.status,
            "display": e.display, "display_text": DISPLAY.get(e.display or ""),
            "banner": bool(e.banner_content) and e.display == "full_site",
            "emails": _emails(e) if e.display == "email_only" else [],
            "offer": _clean(e.offer_description)
            or describe_offer(e.coupon_raw, e.threshold_amount, e.display, coupons.get(e.store_id), e.offer_text, e.coupon_codes)
            or _clean(e.offer_text), "codes": e.coupon_codes or [],
            "threshold": f"{_money(e.threshold_amount)}+" if e.threshold_amount else None,
            "threshold_amount": e.threshold_amount,
            "activation": {"manual_code": "Manual", "automatic": "Automatic"}.get(e.activation or ""),
            "bonus": bonus if sec["bonus"] else None,
            "products": "Storewide" if e.level == "store" or (e.level == "collection" and not targets)
            else _clean(", ".join(targets)),
            "channels": [{"key": c, "name": CHANNELS[c]} for c in e.ad_channels or [] if c in CHANNELS],
            "forecast": _forecast(e) if sec["forecast"] else None,
            "extra_details": _clean(e.extra_details) if sec["extra_details"] else None,
            "changes": {"date": ch[-1].at.date().isoformat(), "items": readable} if readable else None,
        }

    out_products = []
    if sec["product_offers"]:
        titles: dict[tuple[int, str], Product] = {}
        for p in db.scalars(select(Product).where(Product.store_id.in_(by_id))):
            titles.setdefault((p.store_id, p.title.strip().lower()), p)
        matched = [(e, t, titles.get((e.store_id, t.strip().lower()))) for e in product_offers
                   for t in e.level_targets or [e.name]]
        sku_of = skus(db, [p.id for _, _, p in matched if p])
        for e, t, p in matched:
            out_products.append({"store_code": by_id[e.store_id].code, "name": t, "sku": sku_of.get(p.id) if p else None,
                                 "url": _product_url(by_id[e.store_id], p),
                                 "offer": _clean(e.offer_description) or _clean(e.offer_text), "event": e.name})

    focus = []
    if sec["focus"]:
        focus = focus_products(db, doc, list(by_id.values()))

    acts = []
    if sec["extra_activities"]:
        for a in db.scalars(select(ExtraActivity).where(ExtraActivity.store_id.in_(by_id), ExtraActivity.active.is_(True))
                            .order_by(ExtraActivity.store_id, ExtraActivity.id)):
            if (a.start_date and a.start_date > end) or (a.end_date and a.end_date < start):
                continue
            acts.append({"store_code": by_id[a.store_id].code, "name": a.name, "description": _clean(a.description),
                         "price": _clean(a.price), "code": _clean(a.code)})

    return {
        "name": doc.name, "tags": _tags(doc.tags, styles), "start_date": start.isoformat(), "end_date": end.isoformat(),
        "stores": [{"code": s.code, "name": s.name} for s in stores], "statuses": statuses, "sections": sec,
        "custom_top": doc.custom_top, "custom_bottom": doc.custom_bottom,
        "focus": focus, "focus_rule": focus_rule(doc), "events": [event_out(e) for e in events] if sec["events"] else [],
        "product_offers": out_products, "extra_activities": acts,
    }


def skus(db: Session, product_ids: list[int]) -> dict[int, str]:
    """The SKU shown for a product: its variants' SKUs (never the platform product id). One SKU is shown
    as is; several are listed, the first three plus "+N more". A product without SKUs is left out."""
    found: dict[int, list[str]] = {}
    if product_ids:
        for pid, sku in db.execute(select(Variant.product_id, Variant.sku).where(Variant.product_id.in_(product_ids))
                                   .order_by(Variant.product_id, Variant.id)):
            sku = (sku or "").strip()
            if sku and sku not in found.setdefault(pid, []):
                found[pid].append(sku)
    return {pid: ", ".join(v[:3]) + (f" +{len(v) - 3} more" if len(v) > 3 else "") for pid, v in found.items() if v}


def _product_url(store: Store, p: Product | None) -> str | None:
    if not p or not p.handle or not store.product_host:
        return None
    host = re.sub(r"^https?://", "", store.product_host).strip("/")
    return f"https://{host}/products/{p.handle}"


def focus_rule(doc: OfferDoc) -> str:
    f = doc.focus or {}
    parts = [PERF.get(t, t) for t in f.get("perf_tags", [])] + [f'tag "{t}"' for t in f.get("manual_tags", [])]
    return " + ".join(parts) if parts else ("Chosen by hand" if f.get("pin") else "")


def focus_products(db: Session, doc: OfferDoc, stores: list[Store]) -> list[dict]:
    """Products matching the doc's performance or manual tags, plus pinned ones, minus excluded."""
    f = doc.focus or {}
    perf = set(f.get("perf_tags") or [])
    manual = {t.lower() for t in f.get("manual_tags") or []}
    pin = [str(x) for x in f.get("pin") or []]
    exclude = {str(x) for x in f.get("exclude") or []}
    limit = int(f.get("limit") or 20)
    if not (perf or manual or pin):
        return []
    by_id = {s.id: s for s in stores}
    picked: list[Product] = []
    for p in db.scalars(select(Product).where(Product.store_id.in_(by_id), Product.status == "ACTIVE")):
        if str(p.id) in exclude:
            continue
        tags = {str(t).lower() for t in p.manual_tags or []}
        if str(p.id) in pin or (p.perf_tag in perf) or (tags & manual):
            picked.append(p)
    picked.sort(key=lambda p: (str(p.id) not in pin, p.perf_rank if p.perf_rank is not None else 10**9, p.title))
    picked = picked[:limit]
    sku_of = skus(db, [p.id for p in picked])
    return [{"id": p.id, "store_code": by_id[p.store_id].code, "name": p.title, "sku": sku_of.get(p.id),
             "url": _product_url(by_id[p.store_id], p), "perf": PERF.get(p.perf_tag or ""),
             "tags": list(p.manual_tags or [])} for p in picked]
