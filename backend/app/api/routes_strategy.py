from datetime import date
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.actions import workflow
from app.api.common import DB, get_store, to_dict
from app.core.errors import NotFoundError, ValidationError
from app.experiments.evaluation import allowed_transitions, evaluate, transition
from app.models import (
    ActionProposal,
    AuditLog,
    Experiment,
    LearningRecord,
    MetricSnapshot,
    Recommendation,
    Store,
)
from app.models.enums import StrategyType
from app.services.audit import audit
from app.strategy import engine, llm
from app.strategy.frameworks import FRAMEWORKS

router = APIRouter(prefix="/api", tags=["strategy"])


# ---- recommendations -----------------------------------------------------------------------
class RunIn(BaseModel):
    store_id: int | None = None
    days: int = Field(default=90, ge=14, le=730)
    include_demo: bool = False


class RecPatch(BaseModel):
    status: str = Field(pattern="^(open|accepted|dismissed)$")
    note: str | None = None


@router.get("/recommendations")
def recommendations(store_id: int | None = None, status: str | None = "open", strategy_type: str | None = None,
                    include_demo: bool = False, db: Session = DB):
    q = select(Recommendation, Store.code, Store.currency).join(Store, Store.id == Recommendation.store_id)
    if store_id:
        q = q.where(Recommendation.store_id == store_id)
    elif not include_demo:
        q = q.where(Recommendation.is_demo.is_(False))
    if status:
        q = q.where(Recommendation.status == status)
    if strategy_type:
        q = q.where(Recommendation.strategy_type == strategy_type)
    q = q.order_by(Recommendation.priority_score.desc())
    return [to_dict(r) | {"store_code": code, "currency": cur} for r, code, cur in db.execute(q)]


@router.post("/recommendations/run")
def run(body: RunIn, db: Session = DB):
    if body.store_id:
        return [engine.run_store(db, get_store(db, body.store_id), days=body.days)]
    return engine.run_portfolio(db, include_demo=body.include_demo, days=body.days)


@router.get("/recommendations/{rec_id}")
def get_rec(rec_id: int, db: Session = DB):
    r = _rec(db, rec_id)
    return to_dict(r) | {"store_code": db.get(Store, r.store_id).code if r.store_id else None,
                         "ai_available": llm.available()}


@router.patch("/recommendations/{rec_id}")
def patch_rec(rec_id: int, body: RecPatch, db: Session = DB):
    r = _rec(db, rec_id)
    r.status, r.status_note = body.status, body.note
    audit(db, f"recommendation.{body.status}", "recommendation", r.id, store_id=r.store_id,
          details={"note": body.note})
    db.commit()
    return to_dict(r)


@router.post("/recommendations/{rec_id}/ai-commentary")
def ai_commentary(rec_id: int, db: Session = DB):
    r = _rec(db, rec_id)
    text = llm.commentary(r, db.get(Store, r.store_id) if r.store_id else None)
    db.commit()
    return {"ai_commentary": text, "ai_model": r.ai_model}


@router.post("/recommendations/{rec_id}/experiment", status_code=201)
def rec_to_experiment(rec_id: int, db: Session = DB):
    r = _rec(db, rec_id)
    if r.store_id is None:
        raise ValidationError("Portfolio-level recommendations cannot become a store experiment.")
    design = "ab_test" if r.recommended_test and ("random" in r.recommended_test.lower()
                                                  or "holdout" in r.recommended_test.lower()) else "before_after"
    exp = Experiment(store_id=r.store_id, title=r.title, hypothesis=f"{r.proposed_action} — expected because: "
                     f"{r.economic_rationale}", strategy_type=r.strategy_type,
                     product_scope=(r.scope or {}).get("product_ids", []), status="hypothesis", design=design,
                     target_metrics=r.success_metrics, methodology=r.recommended_test, recommendation_id=r.id,
                     baseline={"estimated_impact": r.estimated_impact, "assumptions": r.assumptions},
                     history=[{"at": date.today().isoformat(), "from": "opportunity", "to": "hypothesis",
                               "note": f"Created from recommendation #{r.id}"}])
    db.add(exp)
    r.status = "in_experiment"
    db.flush()
    audit(db, "experiment.created", "experiment", exp.id, store_id=exp.store_id, details={"from_recommendation": r.id})
    db.commit()
    return to_dict(exp)


@router.get("/frameworks")
def frameworks():
    return FRAMEWORKS


@router.get("/stores/{store_id}/snapshots")
def snapshots(store_id: int, db: Session = DB):
    return [to_dict(s) for s in db.scalars(select(MetricSnapshot).where(MetricSnapshot.store_id == store_id)
                                           .order_by(MetricSnapshot.taken_at.desc()).limit(50))]


def _rec(db, rec_id) -> Recommendation:
    r = db.get(Recommendation, rec_id)
    if r is None:
        raise NotFoundError("Recommendation not found.")
    return r


# ---- experiments ---------------------------------------------------------------------------
class ExperimentIn(BaseModel):
    store_id: int
    title: str = Field(min_length=1, max_length=300)
    hypothesis: str = Field(min_length=1)
    strategy_type: StrategyType
    product_scope: list[Any] = []
    design: str = Field(default="before_after", pattern="^(ab_test|holdout|geo_split|before_after)$")
    primary_metric: str = "contribution_per_session"
    target_metrics: list[Any] = []
    baseline_start: date | None = None
    baseline_end: date | None = None
    start_date: date | None = None
    end_date: date | None = None
    methodology: str | None = None


class ExperimentPatch(BaseModel):
    model_config = {"extra": "forbid"}
    title: str | None = None
    hypothesis: str | None = None
    design: str | None = Field(default=None, pattern="^(ab_test|holdout|geo_split|before_after)$")
    primary_metric: str | None = None
    target_metrics: list[Any] | None = None
    baseline_start: date | None = None
    baseline_end: date | None = None
    start_date: date | None = None
    end_date: date | None = None
    results: dict[str, Any] | None = None
    methodology: str | None = None
    costs: float | None = None
    confounders: list[Any] | None = None
    limitations: str | None = None
    interpretation: str | None = None
    decision: str | None = Field(default=None, pattern="^(adopt|reject|iterate|extend)$")
    follow_up: str | None = None


class TransitionIn(BaseModel):
    to: str
    note: str | None = None


@router.get("/experiments")
def list_experiments(store_id: int | None = None, include_demo: bool = False, db: Session = DB):
    q = select(Experiment, Store.code, Store.is_demo).join(Store, Store.id == Experiment.store_id)
    if store_id:
        q = q.where(Experiment.store_id == store_id)
    elif not include_demo:
        q = q.where(Store.is_demo.is_(False))
    return [to_dict(e) | {"store_code": c, "is_demo": d, "allowed_transitions": allowed_transitions(e.status)}
            for e, c, d in db.execute(q.order_by(Experiment.updated_at.desc()))]


@router.post("/experiments", status_code=201)
def create_experiment(body: ExperimentIn, db: Session = DB):
    get_store(db, body.store_id)
    e = Experiment(**body.model_dump(), status="hypothesis",
                   history=[{"at": date.today().isoformat(), "from": None, "to": "hypothesis", "note": "created"}])
    db.add(e)
    db.flush()
    audit(db, "experiment.created", "experiment", e.id, store_id=e.store_id)
    db.commit()
    return _exp_out(db, e)


@router.get("/experiments/{exp_id}")
def get_experiment(exp_id: int, db: Session = DB):
    e = _exp(db, exp_id)
    return _exp_out(db, e)


@router.patch("/experiments/{exp_id}")
def patch_experiment(exp_id: int, body: ExperimentPatch, db: Session = DB):
    e = _exp(db, exp_id)
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(e, k, v)
    audit(db, "experiment.updated", "experiment", e.id, store_id=e.store_id,
          details={"fields": sorted(body.model_dump(exclude_unset=True))})
    db.commit()
    return _exp_out(db, e)


@router.post("/experiments/{exp_id}/evaluate")
def evaluate_experiment(exp_id: int, db: Session = DB):
    e = _exp(db, exp_id)
    result = evaluate(db, e)
    audit(db, "experiment.evaluated", "experiment", e.id, store_id=e.store_id,
          details={"evidence_strength": result["evidence_strength"]})
    db.commit()
    return result


@router.post("/experiments/{exp_id}/transition")
def transition_experiment(exp_id: int, body: TransitionIn, db: Session = DB):
    e = _exp(db, exp_id)
    prev = e.status
    transition(db, e, body.to, body.note)
    audit(db, "experiment.transition", "experiment", e.id, store_id=e.store_id,
          details={"from": prev, "to": body.to, "note": body.note})
    db.commit()
    return _exp_out(db, e)


@router.get("/learning")
def learning(store_id: int | None = None, db: Session = DB):
    q = select(LearningRecord).order_by(LearningRecord.created_at.desc())
    if store_id:
        q = q.where((LearningRecord.store_id == store_id) | LearningRecord.store_id.is_(None))
    return [to_dict(r) for r in db.scalars(q)]


def _exp_out(db, e: Experiment) -> dict:
    store = db.get(Store, e.store_id)
    return to_dict(e) | {"allowed_transitions": allowed_transitions(e.status), "store_code": store.code,
                         "is_demo": store.is_demo}


def _exp(db, exp_id) -> Experiment:
    e = db.get(Experiment, exp_id)
    if e is None:
        raise NotFoundError("Experiment not found.")
    return e


# ---- actions -------------------------------------------------------------------------------
class ProposalIn(BaseModel):
    store_id: int
    kind: str
    payload: dict[str, Any]
    title: str
    expected_impact: dict[str, Any] = {}
    assumptions: list[str] = []
    risks: list[str] = []
    recommendation_id: int | None = None
    experiment_id: int | None = None


class ApproveIn(BaseModel):
    confirm_store_code: str


class RejectIn(BaseModel):
    reason: str = Field(min_length=1)


@router.get("/actions/kinds")
def action_kinds():
    return workflow.ACTION_KINDS


@router.get("/actions")
def list_actions(store_id: int | None = None, db: Session = DB):
    q = select(ActionProposal, Store.code).join(Store, Store.id == ActionProposal.store_id)
    if store_id:
        q = q.where(ActionProposal.store_id == store_id)
    return [to_dict(a) | {"store_code": c} for a, c in db.execute(q.order_by(ActionProposal.created_at.desc()))]


@router.post("/actions", status_code=201)
def create_action(body: ProposalIn, db: Session = DB):
    store = get_store(db, body.store_id)
    p = workflow.create_proposal(db, store, body.kind, body.payload, title=body.title,
                                 expected_impact=body.expected_impact, assumptions=body.assumptions,
                                 risks=body.risks, recommendation_id=body.recommendation_id,
                                 experiment_id=body.experiment_id)
    db.commit()
    return to_dict(p)


@router.post("/actions/{action_id}/approve")
def approve_action(action_id: int, body: ApproveIn, db: Session = DB):
    p = _action(db, action_id)
    workflow.approve(db, p, confirm_store_code=body.confirm_store_code)
    db.commit()
    return to_dict(p)


@router.post("/actions/{action_id}/reject")
def reject_action(action_id: int, body: RejectIn, db: Session = DB):
    p = _action(db, action_id)
    workflow.reject(db, p, body.reason)
    db.commit()
    return to_dict(p)


@router.post("/actions/{action_id}/execute")
def execute_action(action_id: int, db: Session = DB):
    p = _action(db, action_id)
    try:
        workflow.execute(db, p)
    finally:
        db.commit()  # the blocked attempt is audited
    return to_dict(p)


@router.post("/actions/{action_id}/applied-manually")
def applied_manually(action_id: int, db: Session = DB):
    p = _action(db, action_id)
    workflow.mark_applied_manually(db, p)
    db.commit()
    return to_dict(p)


@router.post("/actions/{action_id}/verify")
def verify_action(action_id: int, db: Session = DB):
    p = _action(db, action_id)
    res = workflow.verify_applied(db, p)
    db.commit()
    return res


def _action(db, action_id) -> ActionProposal:
    p = db.get(ActionProposal, action_id)
    if p is None:
        raise NotFoundError("Proposal not found.")
    return p


@router.get("/audit")
def audit_log(store_id: int | None = None, limit: int = 200, db: Session = DB):
    q = select(AuditLog).order_by(AuditLog.at.desc()).limit(min(limit, 1000))
    if store_id:
        q = q.where(AuditLog.store_id == store_id)
    return [to_dict(a) for a in db.scalars(q)]
