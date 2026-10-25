from collections import defaultdict
from datetime import date
from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analytics import simulations as S
from app.analytics.basket_analysis import basket_overview, gift_candidates, simulate_quantity, simulate_threshold
from app.analytics.dataset import load_dataset
from app.analytics.product_analysis import co_purchase, product_performance, product_weekly_units
from app.analytics.store_analysis import kpis, store_summary
from app.api.common import DB, Period, get_store, to_dict
from app.core.errors import ConflictError, ValidationError
from app.db.base import utcnow
from app.models import Integration, Recommendation, Simulation, Store, StoreObjective
from app.models.enums import Objective
from app.models.store import DEFAULT_COMMERCIAL_SETTINGS
from app.offers.monitoring import alerts as offer_alerts
from app.services import integrations as integ_svc
from app.services.audit import audit
from app.services.sync import freshness

router = APIRouter(prefix="/api", tags=["stores"])


class StoreIn(BaseModel):
    code: str = Field(min_length=2, max_length=32, pattern=r"^[A-Za-z0-9_\-]+$")
    name: str = Field(min_length=1, max_length=200)
    platform: str = "shopify"
    currency: str = Field(default="USD", min_length=3, max_length=3)
    timezone: str = "UTC"
    notes: str | None = None


class StorePatch(BaseModel):
    name: str | None = None
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    timezone: str | None = None
    status: str | None = Field(default=None, pattern="^(active|inactive)$")
    notes: str | None = None
    commercial_settings: dict[str, Any] | None = None
    constraints: dict[str, Any] | None = None


class ObjectiveIn(BaseModel):
    objective: Objective
    params: dict[str, Any] = {}
    notes: str | None = None


def _store_card(db: Session, s: Store) -> dict:
    integ = db.scalar(select(Integration).where(Integration.store_id == s.id))
    d = to_dict(s)
    d["objectives"] = [to_dict(o) for o in s.objectives]
    d["integration"] = {"status": integ.status if integ else "not_configured",
                        "shop_domain": integ.shop_domain if integ else None}
    d["freshness"] = freshness(db, s)
    return d


@router.get("/stores")
def list_stores(include_demo: bool = True, db: Session = DB):
    q = select(Store).order_by(Store.is_demo, Store.code)
    if not include_demo:
        q = q.where(Store.is_demo.is_(False))
    return [_store_card(db, s) for s in db.scalars(q)]


@router.post("/stores", status_code=201)
def create_store(body: StoreIn, db: Session = DB):
    code = body.code.upper()
    if db.scalar(select(Store).where(Store.code == code)):
        raise ConflictError(f"Store code {code} already exists.")
    s = Store(code=code, name=body.name, platform=body.platform, currency=body.currency.upper(),
              timezone=body.timezone, notes=body.notes, commercial_settings=dict(DEFAULT_COMMERCIAL_SETTINGS))
    db.add(s)
    db.flush()
    audit(db, "store.created", "store", s.id, store_id=s.id, details={"code": code})
    db.commit()
    return _store_card(db, s)


@router.get("/stores/{store_id}")
def get_store_detail(store_id: int, db: Session = DB):
    return _store_card(db, get_store(db, store_id))


@router.patch("/stores/{store_id}")
def update_store(store_id: int, body: StorePatch, db: Session = DB):
    s = get_store(db, store_id)
    changes = body.model_dump(exclude_unset=True)
    if "commercial_settings" in changes:
        merged = {**(s.commercial_settings or {})}
        for k, v in changes.pop("commercial_settings").items():
            if k not in DEFAULT_COMMERCIAL_SETTINGS and k not in ("default_gift_cost",
                                                                  "new_customer_discount_threshold",
                                                                  "returning_customer_discount_threshold"):
                raise ValidationError(f"Unknown commercial setting '{k}'.")
            if v is not None and not isinstance(v, (int, float)):
                raise ValidationError(f"Setting '{k}' must be a number or empty.")
            if k.endswith("_pct") and v is not None and not 0 <= v <= 1:
                raise ValidationError(f"'{k}' is a fraction between 0 and 1 (e.g. 0.15 for 15%).")
            merged[k] = v
        s.commercial_settings = merged
    for k, v in changes.items():
        setattr(s, k, v.upper() if k == "currency" and v else v)
    audit(db, "store.updated", "store", s.id, store_id=s.id, details=body.model_dump(exclude_unset=True))
    db.commit()
    return _store_card(db, s)


@router.put("/stores/{store_id}/objectives")
def set_objectives(store_id: int, body: list[ObjectiveIn], db: Session = DB):
    s = get_store(db, store_id)
    if len({o.objective for o in body}) != len(body):
        raise ValidationError("Each objective can only appear once.")
    for o in list(s.objectives):
        db.delete(o)
    db.flush()
    for i, o in enumerate(body, start=1):
        db.add(StoreObjective(store_id=s.id, objective=o.objective, priority=i, params=o.params, notes=o.notes))
    audit(db, "store.objectives_set", "store", s.id, store_id=s.id, details={"objectives": [o.objective for o in body]})
    db.commit()
    db.refresh(s)
    return [to_dict(o) for o in s.objectives]


# ---- portfolio -----------------------------------------------------------------------------
@router.get("/portfolio")
def portfolio(include_demo: bool = False, period: tuple[date, date] = Period, db: Session = DB):
    start, end = period
    q = select(Store).where(Store.status == "active")
    if not include_demo:
        q = q.where(Store.is_demo.is_(False))
    stores = db.scalars(q.order_by(Store.code)).all()
    cards, totals = [], defaultdict(lambda: {"net_sales": 0.0, "orders": 0, "discounts": 0.0, "stores": 0})
    from app.analytics.dataset import previous_period

    ps, pe = previous_period(start, end)
    all_alerts = []
    for s in stores:
        k = kpis(load_dataset(db, s, start, end))
        pk = kpis(load_dataset(db, s, ps, pe))
        integ = db.scalar(select(Integration).where(Integration.store_id == s.id))
        cards.append({"store": {"id": s.id, "code": s.code, "name": s.name, "currency": s.currency,
                                "is_demo": s.is_demo},
                      "kpis": k, "previous": {"net_sales": pk["net_sales"], "orders": pk["orders"], "aov": pk["aov"]},
                      "freshness": freshness(db, s),
                      "integration_status": integ.status if integ else "not_configured"})
        if not s.is_demo:  # demo data is listed but never added to real totals
            t = totals[s.currency]
            t["net_sales"] += k["net_sales"]
            t["orders"] += k["orders"]
            t["discounts"] += k["discounts"]
            t["stores"] += 1
        all_alerts.extend(offer_alerts(db, s, today=end)[:6])
    unverifiable = [a for a in all_alerts if a["kind"] == "unverifiable"]
    if len(unverifiable) > 1:
        all_alerts = [a for a in all_alerts if a["kind"] != "unverifiable"]
        all_alerts.append({**unverifiable[0], "store_code": "", "store_id": None,
                           "title": f"Offers in {len(unverifiable)} stores cannot be verified against the store",
                           "detail": "Not connected or discounts not synced: "
                                     + ", ".join(a["store_code"] for a in unverifiable) + "."})
    rq = select(Recommendation).where(Recommendation.status == "open").order_by(Recommendation.priority_score.desc())
    if not include_demo:
        rq = rq.where(Recommendation.is_demo.is_(False))
    recs = db.scalars(rq.limit(8)).all()
    codes = {s.id: s.code for s in db.scalars(select(Store))}
    return {
        "period": {"start": start.isoformat(), "end": end.isoformat()},
        "previous_period": {"start": ps.isoformat(), "end": pe.isoformat()},
        "stores": cards,
        # Different currencies are never summed together.
        "totals_by_currency": {cur: {**v, "net_sales": round(v["net_sales"], 2), "discounts": round(v["discounts"], 2)}
                               for cur, v in totals.items()},
        "top_recommendations": [{**to_dict(r), "store_code": codes.get(r.store_id)} for r in recs],
        "alerts": sorted(all_alerts, key=lambda a: {"critical": 0, "warning": 1, "info": 2}[a["severity"]])[:15],
    }


# ---- analytics -----------------------------------------------------------------------------
@router.get("/stores/{store_id}/summary")
def summary(store_id: int, period: tuple[date, date] = Period, db: Session = DB):
    return store_summary(db, get_store(db, store_id), *period)


@router.get("/stores/{store_id}/products")
def products(store_id: int, period: tuple[date, date] = Period, db: Session = DB):
    return product_performance(db, get_store(db, store_id), *period)


@router.get("/stores/{store_id}/products/{product_id}/weekly")
def product_weekly(store_id: int, product_id: int, weeks: int = Query(26, ge=4, le=156), db: Session = DB):
    return product_weekly_units(db, get_store(db, store_id), product_id, weeks)


@router.get("/stores/{store_id}/copurchase")
def copurchase(store_id: int, period: tuple[date, date] = Period, min_pair_orders: int | None = None,
               db: Session = DB):
    return co_purchase(db, get_store(db, store_id), *period, min_pair_orders=min_pair_orders)


@router.get("/stores/{store_id}/basket")
def basket(store_id: int, period: tuple[date, date] = Period, db: Session = DB):
    s = get_store(db, store_id)
    out = basket_overview(db, s, *period)
    out["gift_candidates"] = gift_candidates(db, s, *period)
    return out


# ---- simulations ---------------------------------------------------------------------------
class SimulationIn(BaseModel):
    kind: str = Field(pattern="^(threshold|quantity|price_change|bundle|discount_depth|gift_vs_discount)$")
    inputs: dict[str, Any]
    save: bool = False
    name: str | None = None
    start: date | None = None
    end: date | None = None
    recommendation_id: int | None = None


@router.post("/stores/{store_id}/simulations")
def simulate(store_id: int, body: SimulationIn, db: Session = DB):
    s = get_store(db, store_id)
    from datetime import timedelta

    end = body.end or date.today()
    start = body.start or end - timedelta(days=89)
    i = body.inputs
    try:
        if body.kind == "threshold":
            out = simulate_threshold(db, s, start, end, threshold=float(i["threshold"]),
                                     incentive_cost=float(i["incentive_cost"]), margin_pct=_opt(i.get("margin_pct")),
                                     reach_window=float(i.get("reach_window", 0.25)),
                                     overshoot_pct=float(i.get("overshoot_pct", 0.05)),
                                     already_offered=bool(i.get("already_offered", False)))
        elif body.kind == "quantity":
            out = simulate_quantity(db, s, start, end, min_units=int(i["min_units"]),
                                    discount_pct=float(i["discount_pct"]), margin_pct=_opt(i.get("margin_pct")))
        elif body.kind == "price_change":
            out = S.price_change(current_price=float(i["current_price"]), unit_cost=_opt(i.get("unit_cost")),
                                 units=int(i["units"]), new_price=float(i["new_price"]))
        elif body.kind == "bundle":
            out = S.bundle(price_a=float(i["price_a"]), price_b=float(i["price_b"]), cost_a=_opt(i.get("cost_a")),
                           cost_b=_opt(i.get("cost_b")), orders_with_a=int(i["orders_with_a"]),
                           orders_with_both=int(i["orders_with_both"]),
                           bundle_discount_pct=float(i["bundle_discount_pct"]))
        elif body.kind == "discount_depth":
            out = S.discount_depth(avg_order_value=float(i["avg_order_value"]), margin_pct=float(i["margin_pct"]),
                                   current_pct=float(i["current_pct"]), proposed_pct=float(i["proposed_pct"]),
                                   current_redemptions=int(i["current_redemptions"]))
        else:
            out = S.gift_vs_discount(qualifying_aov=float(i["qualifying_aov"]), discount_pct=float(i["discount_pct"]),
                                     gift_unit_cost=float(i["gift_unit_cost"]),
                                     gift_perceived_value=_opt(i.get("gift_perceived_value")))
    except KeyError as exc:
        raise ValidationError(f"Missing input: {exc.args[0]}") from exc
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"Invalid input: {exc}") from exc
    if body.save:
        sim = Simulation(store_id=s.id, kind=body.kind, name=body.name or body.kind, inputs=i,
                         assumptions=out.get("assumptions", []), outputs=out, created_at=utcnow(),
                         recommendation_id=body.recommendation_id)
        db.add(sim)
        db.commit()
        out["simulation_id"] = sim.id
    return out


def _opt(v):
    return None if v in (None, "") else float(v)


@router.get("/stores/{store_id}/simulations")
def list_simulations(store_id: int, db: Session = DB):
    return [to_dict(x) for x in db.scalars(select(Simulation).where(Simulation.store_id == store_id)
                                           .order_by(Simulation.created_at.desc()).limit(100))]


@router.get("/stores/{store_id}/variants")
def variants(store_id: int, db: Session = DB):
    from app.models import Product, Variant

    get_store(db, store_id)
    rows = db.execute(select(Variant, Product.title).join(Product, Product.id == Variant.product_id)
                      .where(Variant.store_id == store_id).order_by(Product.title, Variant.title)).all()
    return [{"id": v.id, "product_id": v.product_id, "product_title": t, "title": v.title, "sku": v.sku,
             "price": v.price, "inventory": v.inventory_quantity} for v, t in rows]


@router.get("/stores/{store_id}/integration")
def store_integration(store_id: int, db: Session = DB):
    get_store(db, store_id)
    integ = db.scalar(select(Integration).where(Integration.store_id == store_id))
    return integ_svc.describe(integ)
