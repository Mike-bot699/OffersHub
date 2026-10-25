"""Recommendation engine: build context → run detectors → apply learning → prioritise → persist."""

import logging
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analytics.basket_analysis import basket_overview, gift_candidates
from app.analytics.product_analysis import co_purchase, product_performance
from app.analytics.store_analysis import store_summary
from app.db.base import utcnow
from app.models import Integration, LearningRecord, MetricSnapshot, Recommendation, Store
from app.models.enums import EvidenceStrength, Objective, StrategyType
from app.offers.monitoring import alerts as offer_alerts
from app.offers.monitoring import offer_history_stats
from app.services import integrations as integ_svc
from app.strategy.detectors import DETECTORS, Context
from app.strategy.frameworks import cite
from app.strategy.types import RecDraft

log = logging.getLogger(__name__)

ALIGNMENT: dict[str, set[str]] = {
    Objective.MAXIMIZE_CONTRIBUTION: {StrategyType.PRICING, StrategyType.DISCOUNT_REDUCTION, StrategyType.FREE_GIFT,
                                      StrategyType.BUNDLE, StrategyType.CROSS_SELL, StrategyType.INVENTORY,
                                      StrategyType.MERCHANDISING, StrategyType.CONVERSION, StrategyType.FREE_SHIPPING},
    Objective.GROW_REVENUE_WITH_MARGIN_FLOOR: {StrategyType.CROSS_SELL, StrategyType.BUNDLE, StrategyType.FREE_GIFT,
                                               StrategyType.SPEND_THRESHOLD, StrategyType.QUANTITY_INCENTIVE,
                                               StrategyType.CONVERSION, StrategyType.RETENTION, StrategyType.INVENTORY},
    Objective.INCREASE_AOV: {StrategyType.FREE_GIFT, StrategyType.SPEND_THRESHOLD, StrategyType.FREE_SHIPPING,
                             StrategyType.BUNDLE, StrategyType.CROSS_SELL, StrategyType.QUANTITY_INCENTIVE,
                             StrategyType.GAMIFICATION},
    Objective.IMPROVE_CONVERSION: {StrategyType.CONVERSION, StrategyType.INVENTORY, StrategyType.FREE_SHIPPING,
                                   StrategyType.MERCHANDISING},
    Objective.INCREASE_REPEAT: {StrategyType.RETENTION, StrategyType.SEGMENTATION},
    Objective.REDUCE_DISCOUNT_RELIANCE: {StrategyType.DISCOUNT_REDUCTION, StrategyType.FREE_GIFT, StrategyType.BUNDLE,
                                         StrategyType.PROMOTION},
    Objective.GROW_SELECTED_PRODUCTS: {StrategyType.CROSS_SELL, StrategyType.BUNDLE, StrategyType.MERCHANDISING,
                                       StrategyType.PRODUCT_EXPANSION, StrategyType.INVENTORY},
    Objective.DIVERSIFY_REVENUE: {StrategyType.DIVERSIFICATION, StrategyType.PRODUCT_EXPANSION,
                                  StrategyType.CROSS_SELL, StrategyType.BUNDLE},
}
CONFLICTS: dict[str, set[str]] = {
    Objective.REDUCE_DISCOUNT_RELIANCE: {StrategyType.QUANTITY_INCENTIVE},
    Objective.MAXIMIZE_CONTRIBUTION: {StrategyType.QUANTITY_INCENTIVE},
}
EVIDENCE_W = {"high": 1.0, "medium": 0.6, "low": 0.3}
FEAS_W = {"low": 1.0, "medium": 0.6, "high": 0.3}
RISK_W = {"low": 0.1, "medium": 0.3, "high": 0.5}
CONF_ORDER = ["low", "medium", "high"]


def build_context(db: Session, store: Store, start: date, end: date) -> Context:
    integ = db.scalar(select(Integration).where(Integration.store_id == store.id))
    return Context(
        db=db, store=store, start=start, end=end,
        summary=store_summary(db, store, start, end),
        products=product_performance(db, store, start, end),
        copurchase=co_purchase(db, store, start, end),
        basket=basket_overview(db, store, start, end),
        gifts=gift_candidates(db, store, start, end),
        history=offer_history_stats(db, [store.id]),
        alerts=offer_alerts(db, store, today=end),
        objectives=[o.objective for o in store.objectives if o.active],
        integration=integ_svc.describe(integ) if integ else None,
    )


def score(draft: RecDraft, ctx: Context) -> tuple[float, dict]:
    annual_net = (ctx.summary["kpis"]["net_sales"] or 0) * ctx.annualize
    impact = min(1.0, (draft.impact_hint or 0) / (0.03 * annual_net)) if annual_net > 0 else 0.0
    ev = EVIDENCE_W.get(draft.confidence, 0.3)
    feas = FEAS_W.get(draft.complexity, 0.6)
    risk = RISK_W.get(draft.risk_level, 0.3)
    base = 0.45 * impact + 0.25 * ev + 0.15 * feas + 0.15 * (1 - risk)
    align, matched = 1.0, None
    objectives = ctx.objectives
    for i, obj in enumerate(objectives):
        if draft.strategy_type in CONFLICTS.get(obj, set()):
            align, matched = 0.6, f"conflicts with objective '{obj}'"
            break
        if draft.strategy_type in ALIGNMENT.get(obj, set()):
            align, matched = (1.3 if i == 0 else 1.15), f"supports {'primary' if i == 0 else 'secondary'} objective '{obj}'"
            break
    value = base * align * 100
    if draft.blocks_analysis:
        value = max(value, 70.0)
    return round(value, 1), {"impact": round(impact, 3), "evidence": ev, "feasibility": feas, "risk": risk,
                             "alignment": align,
                             "alignment_note": matched or ("no objective set" if not objectives
                                                           else "not linked to a stated objective"),
                             "weights": "0.45 impact + 0.25 evidence + 0.15 feasibility + 0.15 (1 − risk), × alignment"}


def apply_learning(db: Session, draft: RecDraft, store: Store) -> list[dict]:
    records = db.scalars(select(LearningRecord).where(
        LearningRecord.active.is_(True), LearningRecord.strategy_type == draft.strategy_type,
        (LearningRecord.store_id == store.id) | (LearningRecord.store_id.is_(None)))).all()
    prior = []
    for r in records:
        prior.append({"id": r.id, "finding": r.finding, "direction": r.direction,
                      "evidence_strength": r.evidence_strength, "experiment_id": r.experiment_id})
        reliable = r.evidence_strength in (EvidenceStrength.STRONG, EvidenceStrength.MODERATE)
        if not reliable:
            continue  # weak or inconclusive findings are shown, never used to change the recommendation
        idx = CONF_ORDER.index(draft.confidence)
        if r.direction == "positive":
            draft.confidence = CONF_ORDER[min(2, idx + 1)]
            draft.confidence_reason += f" Raised: a previous {r.evidence_strength} experiment supports this strategy."
            draft.impact_hint = (draft.impact_hint or 0) * 1.15
        elif r.direction == "negative":
            draft.confidence = CONF_ORDER[max(0, idx - 1)]
            draft.confidence_reason += f" Lowered: a previous {r.evidence_strength} experiment found a negative result."
            draft.risks.append(f"Prior evidence: {r.finding}")
            draft.impact_hint = (draft.impact_hint or 0) * 0.7
    return prior


def run_store(db: Session, store: Store, *, start: date | None = None, end: date | None = None,
              days: int = 90) -> dict:
    end = end or date.today()
    start = start or end - timedelta(days=days - 1)
    ctx = build_context(db, store, start, end)
    drafts: list[RecDraft] = []
    errors = []
    for det in DETECTORS:
        try:
            drafts.extend(det(ctx))
        except Exception as exc:
            log.exception("Detector %s failed for %s", det.__name__, store.code)
            errors.append({"detector": det.__name__, "error": f"{type(exc).__name__}: {exc}"})

    now = utcnow()
    seen = set()
    for d in drafts:
        prior = apply_learning(db, d, store)
        value, breakdown = score(d, ctx)
        fp = f"{store.id}:{d.detector}:{d.key}"
        seen.add(fp)
        rec = db.scalar(select(Recommendation).where(Recommendation.fingerprint == fp))
        if rec is None:
            rec = Recommendation(fingerprint=fp, store_id=store.id, status="open")
            db.add(rec)
        elif rec.status == "stale":
            rec.status = "open"
        rec.detector, rec.strategy_type, rec.scope_type, rec.scope = d.detector, d.strategy_type, d.scope_type, d.scope
        rec.title, rec.problem, rec.proposed_action = d.title, d.problem, d.proposed_action
        rec.objective = ctx.objectives[0] if ctx.objectives else None
        rec.economic_rationale = d.economic_rationale
        rec.evidence = [e.as_dict() for e in d.evidence] + [
            {"label": f"Principle: {f['name']}", "value": f["principle"], "source": f["source"], "basis": "sourced"}
            for f in cite(*d.frameworks)]
        rec.estimated_impact = d.estimated_impact
        rec.assumptions, rec.missing_data = d.assumptions, d.missing_data
        rec.confidence, rec.confidence_reason = d.confidence, d.confidence_reason.strip()
        rec.risks, rec.complexity = d.risks, d.complexity
        rec.recommended_test, rec.success_metrics, rec.next_action = d.recommended_test, d.success_metrics, d.next_action
        rec.prior_evidence = prior
        rec.priority_score, rec.score_breakdown = value, breakdown
        rec.analysis_start, rec.analysis_end = start, end
        rec.generated_at = now
        rec.is_demo = store.is_demo
    for rec in db.scalars(select(Recommendation).where(Recommendation.store_id == store.id,
                                                       Recommendation.status == "open")):
        if rec.fingerprint not in seen:
            rec.status = "stale"
            rec.status_note = f"Not supported by the analysis of {start}–{end}."
    db.add(MetricSnapshot(store_id=store.id, taken_at=now, period_start=start, period_end=end,
                          metrics=ctx.summary["kpis"], data_quality=ctx.summary["data_quality"]))
    db.commit()
    return {"store": store.code, "period": {"start": start.isoformat(), "end": end.isoformat()},
            "recommendations": len(drafts), "detector_errors": errors}


def run_portfolio(db: Session, *, include_demo: bool = False, days: int = 90) -> list[dict]:
    q = select(Store).where(Store.status == "active")
    if not include_demo:
        q = q.where(Store.is_demo.is_(False))
    return [run_store(db, s, days=days) for s in db.scalars(q)]
