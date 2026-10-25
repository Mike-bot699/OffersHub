"""Experiment lifecycle and evaluation.

Controlled designs (A/B, holdout, geo/market split) are evaluated with significance
tests on user-supplied arm results. Before/after designs are computed from store data
and can never be rated above WEAK evidence: they cannot separate the change from
seasonality, traffic mix, stock or concurrent promotions, which are listed explicitly.
"""

from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analytics import metrics as M
from app.analytics.dataset import load_dataset
from app.analytics.store_analysis import kpis
from app.core.errors import ValidationError
from app.db.base import utcnow
from app.models import Experiment, LearningRecord, PromoEvent, Store
from app.models.enums import EXPERIMENT_FLOW, EvidenceStrength, ExperimentStatus

CONTROLLED = {"ab_test", "holdout", "geo_split"}


def allowed_transitions(status: str) -> list[str]:
    if status in (ExperimentStatus.CLOSED, ExperimentStatus.ABANDONED):
        return []
    flow = [s.value for s in EXPERIMENT_FLOW]
    i = flow.index(status) if status in flow else 0
    nxt = flow[i + 1:i + 2]
    back = flow[i - 1:i] if i > 0 else []
    return nxt + back + [ExperimentStatus.ABANDONED.value]


def transition(db: Session, exp: Experiment, to: str, note: str | None = None) -> Experiment:
    if to not in allowed_transitions(exp.status):
        raise ValidationError(f"Cannot move an experiment from '{exp.status}' to '{to}'.")
    if to == ExperimentStatus.MEASURING and not exp.start_date:
        raise ValidationError("Set the start date before measuring.")
    if to == ExperimentStatus.EVALUATED and not exp.evaluation:
        raise ValidationError("Run the evaluation before marking the experiment evaluated.")
    if to == ExperimentStatus.CLOSED:
        if not exp.decision:
            raise ValidationError("Record a decision (adopt / reject / iterate / extend) before closing.")
        record_learning(db, exp)
    exp.history = [*(exp.history or []), {"at": utcnow().isoformat(), "from": exp.status, "to": to, "note": note}]
    exp.status = to
    return exp


def evaluate(db: Session, exp: Experiment) -> dict:
    store = db.get(Store, exp.store_id)
    if exp.design in CONTROLLED:
        result = _evaluate_controlled(exp)
    else:
        result = _evaluate_before_after(db, store, exp)
    exp.evaluation = result
    exp.evidence_strength = result["evidence_strength"]
    return result


def _evaluate_controlled(exp: Experiment) -> dict:
    r = exp.results or {}
    c, t = r.get("control") or {}, r.get("treatment") or {}
    need = ("visitors", "orders")
    if not all(k in c and k in t for k in need):
        raise ValidationError("Controlled experiments need control and treatment 'visitors' and 'orders' "
                              "(plus optional 'revenue' and 'gross_profit').")
    test = M.two_proportion_test(int(c["orders"]), int(c["visitors"]), int(t["orders"]), int(t["visitors"]))
    out: dict = {"design": exp.design, "conversion": test, "metrics": {}}
    for key in ("revenue", "gross_profit", "contribution"):
        if key in c and key in t:
            per_c = M.safe_div(c[key], c["visitors"])
            per_t = M.safe_div(t[key], t["visitors"])
            out["metrics"][f"{key}_per_visitor"] = {"control": per_c, "treatment": per_t,
                                                    "relative_lift": M.growth(per_t, per_c)}
    base = test["rate_a"]
    needed = M.required_sample_per_arm(base, 0.10) if 0 < base < 1 else None
    powered = needed is not None and min(c["visitors"], t["visitors"]) >= needed
    p = test["p_value"]
    if p < 0.05 and powered:
        strength = EvidenceStrength.STRONG
    elif p < 0.05 or (p < 0.10 and powered):
        strength = EvidenceStrength.MODERATE
    else:
        strength = EvidenceStrength.INCONCLUSIVE
    primary = out["metrics"].get("gross_profit_per_visitor") or out["metrics"].get("revenue_per_visitor")
    direction = "neutral"
    if strength != EvidenceStrength.INCONCLUSIVE:
        lift = (primary or {}).get("relative_lift", test["relative_lift"])
        direction = "positive" if (lift or 0) > 0 else "negative"
    out.update({
        "sample_needed_per_arm_for_10pct_lift": needed, "adequately_powered": powered,
        "evidence_strength": strength, "direction": direction,
        "interpretation": _controlled_text(test, strength, powered, primary),
        "limitations": ["Revenue/profit per visitor are compared without a variance estimate unless supplied; "
                        "significance is computed on conversion."] if primary else [],
    })
    return out


def _controlled_text(test, strength, powered, primary) -> str:
    s = (f"Conversion {test['rate_a']:.2%} (control) vs {test['rate_b']:.2%} (treatment), "
         f"p = {test['p_value']:.3f}.")
    if primary and primary.get("relative_lift") is not None:
        s += f" Value per visitor changed {primary['relative_lift']:+.1%}."
    if strength == EvidenceStrength.INCONCLUSIVE:
        s += " The difference is not distinguishable from noise" + ("" if powered else
                                                                     " and the test was under-powered") + "."
    elif not powered:
        s += " Significant but under-powered: effect size is likely overstated."
    return s


def _evaluate_before_after(db: Session, store: Store, exp: Experiment) -> dict:
    if not (exp.start_date and exp.baseline_start and exp.baseline_end):
        raise ValidationError("Before/after evaluation needs baseline start/end and a test start date.")
    end = min(exp.end_date or date.today(), date.today())
    base = kpis(load_dataset(db, store, exp.baseline_start, exp.baseline_end))
    test = kpis(load_dataset(db, store, exp.start_date, end))
    keys = ["orders", "net_sales", "aov", "units_per_order", "discount_rate", "gross_margin_pct", "conversion_rate",
            "contribution"]
    base_days = (exp.baseline_end - exp.baseline_start).days + 1
    test_days = (end - exp.start_date).days + 1
    per_day = {k: {"baseline": M.safe_div(base[k], base_days), "test": M.safe_div(test[k], test_days)}
               for k in ("orders", "net_sales", "contribution") if base.get(k) is not None}
    changes = {k: M.growth(test.get(k), base.get(k)) for k in keys}
    for k, v in per_day.items():
        changes[f"{k}_per_day"] = M.growth(v["test"], v["baseline"])

    confounders = []
    # Seasonality / trend: same windows one year earlier (difference-in-differences)
    yb = kpis(load_dataset(db, store, exp.baseline_start - timedelta(days=364), exp.baseline_end - timedelta(days=364)))
    yt = kpis(load_dataset(db, store, exp.start_date - timedelta(days=364), end - timedelta(days=364)))
    did = None
    if yb["orders"] and yt["orders"]:
        last_year_change = M.growth(M.safe_div(yt["net_sales"], test_days), M.safe_div(yb["net_sales"], base_days))
        if last_year_change is not None and changes.get("net_sales_per_day") is not None:
            did = changes["net_sales_per_day"] - last_year_change
            confounders.append(f"Seasonality: the same windows last year moved {last_year_change:+.1%} in net sales "
                               f"per day; adjusted change ≈ {did:+.1%}.")
    else:
        confounders.append("Seasonality: no data for the same windows last year, so seasonal effects are not removed.")
    if base.get("sessions") and test.get("sessions"):
        tr = M.growth(test["sessions"] / test_days, base["sessions"] / base_days)
        confounders.append(f"Traffic: sessions per day changed {tr:+.1%}.")
    else:
        confounders.append("Traffic: unknown (no sessions data), so demand changes cannot be separated.")
    overlapping = db.scalars(select(PromoEvent).where(
        PromoEvent.store_id == store.id, PromoEvent.start_date <= end, PromoEvent.end_date >= exp.baseline_start)).all()
    if overlapping:
        confounders.append("Concurrent promotions: " + ", ".join(sorted({e.name for e in overlapping})[:6]) + ".")
    return {
        "design": "before_after",
        "baseline": {"start": exp.baseline_start.isoformat(), "end": exp.baseline_end.isoformat(), "kpis": base},
        "test": {"start": exp.start_date.isoformat(), "end": end.isoformat(), "kpis": test},
        "changes": {k: (round(v, 4) if v is not None else None) for k, v in changes.items()},
        "seasonality_adjusted_net_sales_per_day_change": round(did, 4) if did is not None else None,
        "confounders": confounders,
        "evidence_strength": EvidenceStrength.WEAK if (base["orders"] and test["orders"]) else EvidenceStrength.INCONCLUSIVE,
        "direction": "neutral",
        "interpretation": "Before/after comparison: describes what changed, not what caused it. Treat as a "
                          "hypothesis to confirm with a controlled test.",
    }


def record_learning(db: Session, exp: Experiment) -> LearningRecord | None:
    ev = exp.evaluation or {}
    strength = exp.evidence_strength or ev.get("evidence_strength") or EvidenceStrength.INCONCLUSIVE
    direction = ev.get("direction", "neutral")
    finding = exp.interpretation or ev.get("interpretation") or exp.hypothesis
    # Supersede earlier learning from the same experiment
    for old in db.scalars(select(LearningRecord).where(LearningRecord.experiment_id == exp.id,
                                                       LearningRecord.active.is_(True))):
        old.active = False
    rec = LearningRecord(store_id=exp.store_id, experiment_id=exp.id, strategy_type=exp.strategy_type,
                         finding=f"{exp.title}: {finding} Decision: {exp.decision}.", direction=direction,
                         evidence_strength=strength, effect={k: ev.get(k) for k in ("conversion", "metrics", "changes")
                                                             if k in ev},
                         tags=list(exp.product_scope or []), active=True, created_at=utcnow())
    db.add(rec)
    db.flush()
    return rec
