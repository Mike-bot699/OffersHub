"""Live offer docs (managed by signed-in users) and their public, token-addressed view."""

import re
import secrets
from datetime import date
from typing import Any, Literal

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.common import DB, get_store
from app.core.errors import NotFoundError
from app.db.base import utcnow
from app.models import ExtraActivity, OfferDoc, PromoEvent
from app.offers.doc_export import to_docx
from app.offers.docs import DEFAULT_STATUSES, assemble, doc_range, sections
from app.offers.status import business_today
from app.services.audit import audit

router = APIRouter(prefix="/api", tags=["docs"])
Status = Literal["in_progress", "scheduled", "live", "paused", "completed", "cancelled", "not_set"]


class ForecastIn(BaseModel):
    label: str | None = Field(default=None, max_length=60)
    revenue: float | None = Field(default=None, gt=0)
    currency: Literal["USD", "EUR", "GBP"] = "USD"
    fx_rate: float | None = Field(default=None, gt=0)
    fx_note: str | None = Field(default=None, max_length=80)
    aov: float | None = Field(default=None, gt=0)
    current_aov: float | None = Field(default=None, gt=0)
    growth_pct: float | None = Field(default=None, ge=-100, le=1000)


class DocIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    tags: list[str] = []
    store_ids: list[int] = []
    range_mode: Literal["fixed", "next_days", "this_month", "next_month"] = "fixed"
    start_date: date | None = None
    end_date: date | None = None
    days: int | None = Field(default=None, ge=1, le=366)
    statuses: list[Status] = DEFAULT_STATUSES
    sections: dict[str, bool] = {}
    focus: dict[str, Any] = {}
    forecast: ForecastIn = ForecastIn()
    custom_top: str | None = Field(default=None, max_length=20000)
    custom_bottom: str | None = Field(default=None, max_length=20000)


def _doc_out(d: OfferDoc, today: date) -> dict:
    start, end = doc_range(d, today)
    return {"id": d.id, "token": d.token, "name": d.name, "tags": d.tags, "store_ids": d.store_ids,
            "range_mode": d.range_mode, "start_date": d.start_date.isoformat() if d.start_date else None,
            "end_date": d.end_date.isoformat() if d.end_date else None, "days": d.days, "statuses": d.statuses,
            "sections": sections(d), "focus": d.focus, "forecast": d.forecast or {}, "custom_top": d.custom_top, "custom_bottom": d.custom_bottom,
            "changes_since": (d.changes_since or d.created_at).isoformat(),
            "range": [start.isoformat(), end.isoformat()], "updated_at": d.updated_at.isoformat()}


def _get(db: Session, doc_id: int) -> OfferDoc:
    d = db.get(OfferDoc, doc_id)
    if d is None:
        raise NotFoundError("Doc not found.")
    return d


def _apply(d: OfferDoc, body: DocIn) -> None:
    for k, v in body.model_dump().items():
        setattr(d, k, v)
    d.tags = list(dict.fromkeys(" ".join(t.split())[:40] for t in body.tags if t.strip()))
    d.statuses = body.statuses or DEFAULT_STATUSES
    d.forecast = body.forecast.model_dump(exclude_none=True)
    d.updated_at = utcnow()


@router.get("/offer-docs")
def list_docs(db: Session = DB):
    today = business_today()
    return [_doc_out(d, today) for d in db.scalars(select(OfferDoc).order_by(OfferDoc.updated_at.desc()))]


@router.post("/offer-docs", status_code=201)
def create_doc(body: DocIn, db: Session = DB):
    d = OfferDoc(token=secrets.token_urlsafe(24), created_at=utcnow())
    _apply(d, body)
    db.add(d)
    db.flush()
    audit(db, "doc.created", "offer_doc", d.id, details={"name": d.name})
    db.commit()
    return _doc_out(d, business_today())


@router.put("/offer-docs/{doc_id}")
def update_doc(doc_id: int, body: DocIn, db: Session = DB):
    d = _get(db, doc_id)
    _apply(d, body)
    audit(db, "doc.updated", "offer_doc", d.id, details={"name": d.name})
    db.commit()
    return _doc_out(d, business_today())


@router.post("/offer-docs/{doc_id}/new-link")
def new_link(doc_id: int, db: Session = DB):
    """A new secret link; the old one stops working at once."""
    d = _get(db, doc_id)
    d.token = secrets.token_urlsafe(24)
    audit(db, "doc.link_regenerated", "offer_doc", d.id)
    db.commit()
    return _doc_out(d, business_today())


@router.post("/offer-docs/{doc_id}/reset-changes")
def reset_changes(doc_id: int, db: Session = DB):
    """The changes box starts again from now: earlier offer edits are no longer shown as updates."""
    d = _get(db, doc_id)
    d.changes_since = utcnow()
    audit(db, "doc.changes_reset", "offer_doc", d.id)
    db.commit()
    return _doc_out(d, business_today())


@router.delete("/offer-docs/{doc_id}", status_code=204)
def delete_doc(doc_id: int, db: Session = DB):
    d = _get(db, doc_id)
    audit(db, "doc.deleted", "offer_doc", d.id, details={"name": d.name})
    db.delete(d)
    db.commit()
    return Response(status_code=204)


@router.get("/offer-docs/{doc_id}/preview")
def preview_doc(doc_id: int, db: Session = DB):
    return assemble(db, _get(db, doc_id), business_today())


# ---- public, by secret link ------------------------------------------------------------------
def _by_token(db: Session, token: str) -> OfferDoc:
    d = db.scalar(select(OfferDoc).where(OfferDoc.token == token)) if re.fullmatch(r"[\w-]{20,64}", token) else None
    if d is None:
        raise NotFoundError("This link does not exist or was replaced by a new one.")
    return d


@router.get("/public/docs/{token}")
def public_doc(token: str, response: Response, db: Session = DB):
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Robots-Tag"] = "noindex"
    return assemble(db, _by_token(db, token), business_today())


@router.get("/public/docs/{token}/banner/{event_id}")
def public_banner(token: str, event_id: int, db: Session = DB):
    """Only the banners of offers the doc shows."""
    data = assemble(db, _by_token(db, token), business_today())
    if not any(e["id"] == event_id and e["banner"] for e in data["events"]):
        raise NotFoundError("No banner for this offer in this doc.")
    e = db.get(PromoEvent, event_id)
    return {"kind": e.banner_kind, "content": e.banner_content, "filename": e.banner_filename}


@router.get("/public/docs/{token}/docx")
def public_docx(token: str, request: Request, db: Session = DB):
    d = _by_token(db, token)
    data = assemble(db, d, business_today())
    link = str(request.base_url).rstrip("/") + f"/d/{token}"
    name = re.sub(r"[^\w .-]+", "", d.name).strip() or "offers"
    return Response(to_docx(data, link), headers={"Content-Disposition": f'attachment; filename="{name}.docx"'},
                    media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document")


# ---- extra activities ------------------------------------------------------------------------
class ActivityIn(BaseModel):
    store_id: int
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None
    price: str | None = Field(default=None, max_length=100)
    code: str | None = Field(default=None, max_length=128)
    start_date: date | None = None
    end_date: date | None = None
    active: bool = True


def _act_out(a: ExtraActivity) -> dict:
    return {"id": a.id, "store_id": a.store_id, "name": a.name, "description": a.description, "price": a.price,
            "code": a.code, "start_date": a.start_date.isoformat() if a.start_date else None,
            "end_date": a.end_date.isoformat() if a.end_date else None, "active": a.active}


@router.get("/extra-activities")
def list_activities(store_id: int | None = None, db: Session = DB):
    q = select(ExtraActivity).order_by(ExtraActivity.store_id, ExtraActivity.id)
    if store_id:
        q = q.where(ExtraActivity.store_id == store_id)
    return [_act_out(a) for a in db.scalars(q)]


@router.post("/extra-activities", status_code=201)
def create_activity(body: ActivityIn, db: Session = DB):
    get_store(db, body.store_id)
    a = ExtraActivity(**body.model_dump(), created_at=utcnow())
    db.add(a)
    db.flush()
    audit(db, "extra_activity.created", "extra_activity", a.id, store_id=a.store_id)
    db.commit()
    return _act_out(a)


@router.put("/extra-activities/{act_id}")
def update_activity(act_id: int, body: ActivityIn, db: Session = DB):
    a = db.get(ExtraActivity, act_id)
    if a is None:
        raise NotFoundError("Extra activity not found.")
    get_store(db, body.store_id)
    for k, v in body.model_dump().items():
        setattr(a, k, v)
    audit(db, "extra_activity.updated", "extra_activity", a.id, store_id=a.store_id)
    db.commit()
    return _act_out(a)


@router.delete("/extra-activities/{act_id}", status_code=204)
def delete_activity(act_id: int, db: Session = DB):
    a = db.get(ExtraActivity, act_id)
    if a is None:
        raise NotFoundError("Extra activity not found.")
    db.delete(a)
    db.commit()
    return Response(status_code=204)
