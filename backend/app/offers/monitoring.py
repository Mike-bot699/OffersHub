"""Offer monitoring: verification against the store, measured results, and alerts.

Two kinds of truth are kept apart:
  documented  — what this app / the spreadsheet says an offer is
  verified    — what the connected store actually has configured (synced discount codes)
A configuration error is only flagged when the store's discount data has been synced;
otherwise the event is marked `unverifiable`.
"""

from collections import defaultdict
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analytics import metrics as M
from app.analytics.dataset import load_dataset
from app.db.base import utcnow
from app.models import Coupon, Integration, PromoEvent, Store, SyncState
from app.models.enums import DataSource, IntegrationStatus, VerificationStatus


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def discounts_synced(db: Session, store: Store) -> bool:
    integ = db.scalar(select(Integration).where(Integration.store_id == store.id))
    if integ is None or integ.status not in (IntegrationStatus.CONNECTED, IntegrationStatus.ERROR):
        return False
    st = db.scalar(select(SyncState).where(SyncState.store_id == store.id, SyncState.resource == "discounts"))
    return bool(st and st.last_success_at)


def verify_event(db: Session, ev: PromoEvent, store: Store, *, synced: bool | None = None) -> dict:
    """Compare a documented event with the store's synced discount configuration."""
    synced = discounts_synced(db, store) if synced is None else synced
    findings: list[str] = []
    if not synced:
        status = VerificationStatus.UNVERIFIABLE
        findings.append("Store discounts have not been synced from a connected platform.")
    elif not ev.coupon_codes:
        status = VerificationStatus.UNVERIFIABLE
        findings.append("No coupon code documented; automatic discounts are matched by title only, "
                        "which is not reliable enough to verify.")
        autos = db.scalars(select(Coupon).where(Coupon.store_id == store.id, Coupon.source == DataSource.SHOPIFY,
                                                Coupon.channel == "automatic")).all()
        name = (ev.name or "").lower()
        hits = [c for c in autos if c.title and (c.title.lower() in name or name in c.title.lower())]
        if hits:
            findings.append(f"Possible automatic discount match: {', '.join(c.title for c in hits[:3])}.")
    else:
        live = {c.code: c for c in db.scalars(select(Coupon).where(Coupon.store_id == store.id,
                                                                   Coupon.source == DataSource.SHOPIFY))}
        status = VerificationStatus.VERIFIED
        for code in ev.coupon_codes:
            c = live.get(code.upper())
            if c is None or c.status == "deleted":
                status = VerificationStatus.NOT_FOUND
                findings.append(f"Code {code} is not configured in the store.")
                continue
            mech_val = _documented_value(ev)
            if mech_val is not None and c.value_type == "percent" and c.value is not None and abs(c.value - mech_val) > 0.01:
                status = VerificationStatus.MISMATCH
                findings.append(f"{code}: store gives {c.value:g}% but the offer says {mech_val:g}%.")
            if ev.threshold_amount and (c.min_subtotal or 0) + 0.01 < ev.threshold_amount:
                status = VerificationStatus.MISMATCH
                findings.append(f"{code}: store minimum is {c.min_subtotal or 0:g}, offer threshold is "
                                f"{ev.threshold_amount:g}.")
            if ev.start_date and c.starts_at and _aware(c.starts_at).date() > ev.start_date:
                status = VerificationStatus.MISMATCH
                findings.append(f"{code}: starts {c.starts_at:%Y-%m-%d}, after the event start {ev.start_date}.")
            if ev.end_date and c.ends_at and _aware(c.ends_at).date() < ev.end_date:
                status = VerificationStatus.MISMATCH
                findings.append(f"{code}: ends {c.ends_at:%Y-%m-%d}, before the event end {ev.end_date}.")
            if ev.end_date and c.ends_at is None and ev.end_date < date.today():
                findings.append(f"{code}: has no end date in the store although the event ended {ev.end_date} "
                                "(still redeemable).")
        if status == VerificationStatus.VERIFIED and not findings:
            findings.append("All documented codes found with matching value, minimum and dates.")
    ev.verification_status = status
    ev.verified_at = utcnow() if synced else None
    return {"status": status, "findings": findings}


def _documented_value(ev: PromoEvent) -> float | None:
    import re

    m = re.search(r"(\d{1,2}(?:[.,]\d+)?)\s*%", ev.offer_text or "")
    if m and "up to" not in (ev.offer_text or "").lower():
        return float(m.group(1).replace(",", "."))
    return None


def measure_event(db: Session, ev: PromoEvent, store: Store) -> dict | None:
    """Measured results from synced orders: orders using the event's codes during its window.
    This is exposure (orders that used the code), not incremental impact."""
    if not ev.start_date or not ev.coupon_codes:
        return None
    end = min(ev.end_date or date.today(), date.today())
    if end < ev.start_date:
        return None
    ds = load_dataset(db, store, ev.start_date, end)
    if not ds.orders:
        return {"available": False, "reason": "No synced orders in the event window."}
    codes = {c.upper() for c in ev.coupon_codes}
    offer = [o for o in ds.orders if codes & set(o.codes)]
    other = [o for o in ds.orders if not (codes & set(o.codes))]
    return {
        "available": True,
        "window": {"start": ev.start_date.isoformat(), "end": end.isoformat()},
        "offer_orders": len(offer),
        "total_orders": len(ds.orders),
        "offer_order_share": round(len(offer) / len(ds.orders), 4),
        "offer_net_sales": round(sum(o.net for o in offer), 2),
        "discount_cost": round(sum(o.discounts for o in offer), 2),
        "aov_offer_orders": round(M.aov(sum(o.net for o in offer), len(offer)) or 0, 2) if offer else None,
        "aov_other_orders": round(M.aov(sum(o.net for o in other), len(other)) or 0, 2) if other else None,
        "source": "demo orders" if store.is_demo else "synced store orders",
        "note": "Orders that used the code are not all caused by the offer.",
    }


def alerts(db: Session, store: Store, *, today: date | None = None, horizon_days: int = 21) -> list[dict]:
    today = today or date.today()
    events = db.scalars(select(PromoEvent).where(PromoEvent.store_id == store.id)).all()
    coupons = db.scalars(select(Coupon).where(Coupon.store_id == store.id)).all()
    synced = discounts_synced(db, store)
    out: list[dict] = []

    def add(severity, kind, title, detail, event=None, **extra):
        out.append({"severity": severity, "kind": kind, "title": title, "detail": detail,
                    "event_id": event.id if event else None, "store_id": store.id, "store_code": store.code, **extra})

    from app.offers.status import STATUS_LABELS, review_prompts

    for ev in events:
        if ev.status_conflict and ev.status_conflict != ev.status_conflict_ack:
            add("critical" if ev.status == "live" else "warning", "status_conflict",
                f"{ev.name}: offer status and store disagree", ev.status_conflict, ev)
        for p in review_prompts(ev, today):
            if ev.status is None and ev.end_date and ev.end_date < today - timedelta(days=60):
                continue  # old undocumented history is listed in the database, not as an alert
            add("warning", "status_review", f"{ev.name} ({STATUS_LABELS.get(ev.status)}): review status", p, ev)
    active = [e for e in events if e.status not in ("cancelled", "completed")]
    for ev in active:
        if ev.start_date and today <= ev.start_date <= today + timedelta(days=horizon_days):
            missing = []
            if not ev.end_date:
                missing.append("end date")
            if not ev.offer_text:
                missing.append("offer description")
            if ev.activation == "manual_code" and not ev.coupon_codes:
                missing.append("coupon code")
            if not ev.channels:
                missing.append("channels")
            if not ev.targets:
                missing.append("performance target")
            days = (ev.start_date - today).days
            add("warning" if missing else "info", "upcoming", f"{ev.name} starts in {days} day(s)",
                f"Missing: {', '.join(missing)}." if missing else "Configuration documented.", ev)
            if synced and ev.coupon_codes:
                v = verify_event(db, ev, store, synced=True)
                if v["status"] in (VerificationStatus.NOT_FOUND, VerificationStatus.MISMATCH):
                    add("critical", "config_mismatch", f"{ev.name}: store configuration does not match",
                        " ".join(v["findings"]), ev)
        if ev.start_date and ev.end_date and ev.start_date <= today <= ev.end_date:
            if synced and ev.coupon_codes:
                v = verify_event(db, ev, store, synced=True)
                if v["status"] in (VerificationStatus.NOT_FOUND, VerificationStatus.MISMATCH):
                    add("critical", "config_mismatch", f"LIVE: {ev.name} is misconfigured", " ".join(v["findings"]), ev)
            target = (ev.targets or {}).get("offer_orders")
            if target:
                res = measure_event(db, ev, store)
                if res and res.get("available"):
                    elapsed = (today - ev.start_date).days + 1
                    span = (ev.end_date - ev.start_date).days + 1
                    expected = target * elapsed / span
                    if res["offer_orders"] < 0.6 * expected:
                        add("warning", "underpacing", f"{ev.name} is behind target",
                            f"{res['offer_orders']} offer orders vs ~{expected:.0f} expected by now "
                            f"(target {target} over {span} days).", ev)
            if ev.end_date and 0 <= (ev.end_date - today).days <= 2:
                add("info", "ending", f"{ev.name} ends {ev.end_date}", "Plan the evaluation and the code expiry.", ev)

    # Overlapping discount events in the same store
    dated = sorted((e for e in active if e.start_date and e.end_date and e.offer_type not in ("message_only",)),
                   key=lambda e: e.start_date)
    for i, a in enumerate(dated):
        for b in dated[i + 1:]:
            if b.start_date > a.end_date:
                break
            if a.end_date >= today - timedelta(days=1) or b.end_date >= today - timedelta(days=1):
                add("warning", "overlap", f"Overlapping offers: {a.name} / {b.name}",
                    f"{a.start_date}–{a.end_date} overlaps {b.start_date}–{b.end_date}. Check stacking rules "
                    "(combinesWith) so customers cannot combine both.", a, other_event_id=b.id)

    # Same code reused by different events
    code_events: dict[str, list[PromoEvent]] = defaultdict(list)
    for e in active:
        for c in e.coupon_codes or []:
            code_events[c].append(e)
    for code, evs in code_events.items():
        upcoming = [e for e in evs if e.end_date and e.end_date >= today]
        if len(evs) > 1 and upcoming:
            add("info", "code_reuse", f"Code {code} is reused across {len(evs)} events",
                "Reusing a code makes past results hard to separate and keeps old links redeemable.", upcoming[0])

    # Store-side coupons: evergreen deep codes and expired-but-active
    for c in coupons:
        if c.source != DataSource.SHOPIFY or c.status != "active":
            continue
        if c.ends_at is None and c.value_type == "percent" and (c.value or 0) >= 20 and not c.min_subtotal:
            add("warning", "evergreen_code", f"Code {c.code} gives {c.value:g}% with no end date or minimum",
                f"Used {c.usage_count or 0} times. Evergreen deep codes leak to coupon sites and train customers "
                "to wait for discounts.", coupon_id=c.id)
        if c.ends_at and _aware(c.ends_at).date() < today:
            add("info", "expired_active", f"Code {c.code} past its end date but reported active", "", coupon_id=c.id)

    # Codes used in recent orders that are not documented anywhere
    recent = load_dataset(db, store, today - timedelta(days=30), today)
    used: dict[str, int] = defaultdict(int)
    for o in recent.orders:
        for c in o.codes:
            used[c] += 1
    documented = {c.code for c in coupons} | {c for e in events for c in (e.coupon_codes or [])}
    for code, n in sorted(used.items(), key=lambda kv: -kv[1]):
        if code not in documented and n >= 3:
            add("warning", "undocumented_code", f"Code {code} used {n}× in 30 days but not documented",
                "Add it to an offer/event or check whether it leaked.", code=code)

    # Discount pressure: last 30 days vs the 90 days before
    prev = load_dataset(db, store, today - timedelta(days=120), today - timedelta(days=31))
    if recent.orders and prev.orders:
        r_rate = M.discount_rate(sum(o.discounts for o in recent.orders), sum(o.gross for o in recent.orders)) or 0
        p_rate = M.discount_rate(sum(o.discounts for o in prev.orders), sum(o.gross for o in prev.orders)) or 0
        if r_rate > 0.08 and p_rate > 0 and r_rate > p_rate * 1.3:
            add("warning", "discount_pressure", "Discount rate is rising",
                f"Last 30 days {r_rate:.1%} of gross sales given as discount vs {p_rate:.1%} in the prior 90 days.")
    if not synced and not store.is_demo:
        add("info", "unverifiable", "Offers cannot be verified against the store",
            "Connect the store and sync discounts to check that documented codes are configured correctly.")
    order = {"critical": 0, "warning": 1, "info": 2}
    out.sort(key=lambda a: order.get(a["severity"], 3))
    return out


def offer_history_stats(db: Session, store_ids: list[int]) -> dict:
    """Aggregate recorded results of past events (documented, not causal) by offer type and display."""
    events = db.scalars(select(PromoEvent).where(PromoEvent.store_id.in_(store_ids),
                                                 PromoEvent.status == "completed",
                                                 PromoEvent.total_orders.is_not(None),
                                                 PromoEvent.offer_orders.is_not(None))).all()
    groups: dict[tuple, list[float]] = defaultdict(list)
    for e in events:
        if not e.total_orders or e.offer_orders is None or e.offer_orders > e.total_orders:
            continue
        share = e.offer_orders / e.total_orders
        groups[("display", e.display or "unknown")].append(share)
        groups[("type", e.offer_type or "unknown")].append(share)
    out = {}
    for (dim, key), vals in groups.items():
        vals.sort()
        out.setdefault(dim, []).append({"key": key, "events": len(vals),
                                        "median_offer_order_share": round(M.percentile(vals, 0.5), 4),
                                        "p25": round(M.percentile(vals, 0.25), 4),
                                        "p75": round(M.percentile(vals, 0.75), 4)})
    for dim in out:
        out[dim].sort(key=lambda r: -r["events"])
    return {"events_used": len(events), "by": out,
            "note": "Recorded share of orders that used each event's code. Documented results; not verified, "
                    "not incremental."}
