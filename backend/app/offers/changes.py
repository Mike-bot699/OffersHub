"""Change log of the offer fields that live docs show, so a doc can point out what changed recently."""

from datetime import date
from typing import Any

from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.models import EventChange, PromoEvent

TRACKED = {
    "name": "Name", "start_date": "Start date", "end_date": "End date", "offer_text": "Offer",
    "offer_description": "Offer", "coupon_codes": "Code", "threshold_amount": "Threshold", "display": "Displayed",
    "free_shipping": "Free shipping", "giveaway_product": "Give-away product", "free_gift": "Bonus",
}


def _text(v: Any) -> str | None:
    if v is None or v == [] or v == "":
        return None
    if isinstance(v, list):
        return ", ".join(str(x) for x in v)
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, float):
        return f"{v:g}"
    return str(v)


def snapshot(ev: PromoEvent) -> dict[str, str | None]:
    return {f: _text(getattr(ev, f)) for f in TRACKED}


def record(db: Session, ev: PromoEvent, before: dict[str, str | None], source: str) -> int:
    """Store the tracked fields that differ from `before` (a snapshot taken before the change)."""
    if ev.id is None:
        return 0
    now, n = utcnow(), 0
    for f, old in before.items():
        new = _text(getattr(ev, f))
        if new != old:
            db.add(EventChange(event_id=ev.id, at=now, field=f, old=old, new=new, source=source))
            n += 1
    return n
