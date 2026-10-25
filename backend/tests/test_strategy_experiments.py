from datetime import date, timedelta

import pytest
from sqlalchemy import select

from app.core.errors import CapabilityNotSupported, ConflictError, ValidationError
from app.experiments.evaluation import evaluate, transition
from app.models import ActionProposal, AuditLog, Experiment, LearningRecord, Recommendation, Store, Variant
from app.offers.monitoring import alerts
from app.services.demo import reset_demo
from app.strategy.detectors import nice_price
from app.strategy.engine import run_store


@pytest.fixture()
def demo(db):
    return reset_demo(db, days=420, daily_orders=14)


def test_engine_produces_complete_recommendations(db, demo):
    res = run_store(db, demo)
    assert res["detector_errors"] == []
    recs = db.scalars(select(Recommendation)).all()
    assert recs
    for r in recs:
        assert r.title and r.problem and r.proposed_action and r.next_action
        assert r.confidence in ("low", "medium", "high") and r.confidence_reason
        assert r.evidence and r.is_demo
        if r.estimated_impact:
            assert r.estimated_impact.get("basis")  # every estimate states its basis
    # rerun: no duplicates, statuses preserved
    recs[0].status = "dismissed"
    db.commit()
    run_store(db, demo)
    again = db.scalars(select(Recommendation)).all()
    assert len(again) == len(recs)
    assert db.get(Recommendation, recs[0].id).status == "dismissed"


def test_learning_lowers_confidence_for_negative_strong_evidence(db, demo):
    run_store(db, demo)
    rec = db.scalars(select(Recommendation).where(Recommendation.strategy_type == "pricing")).first()
    if rec is None:
        pytest.skip("demo produced no pricing recommendation")
    before = rec.confidence
    db.add(LearningRecord(store_id=demo.id, strategy_type="pricing", finding="Price +6% cut conversion 12%",
                          direction="negative", evidence_strength="strong", created_at=rec.generated_at))
    db.add(LearningRecord(store_id=demo.id, strategy_type="pricing", finding="anecdote", direction="positive",
                          evidence_strength="weak", created_at=rec.generated_at))
    db.commit()
    run_store(db, demo)
    db.refresh(rec)
    assert len(rec.prior_evidence) == 2
    assert ["low", "medium", "high"].index(rec.confidence) <= ["low", "medium", "high"].index(before)
    assert any("Prior evidence" in r for r in rec.risks)


def test_experiment_lifecycle_and_before_after_is_weak(db, demo):
    today = date.today()
    exp = Experiment(store_id=demo.id, title="Gift test", hypothesis="Gift lifts AOV", strategy_type="free_gift",
                     status="hypothesis", design="before_after", baseline_start=today - timedelta(days=60),
                     baseline_end=today - timedelta(days=31), start_date=today - timedelta(days=30), end_date=today)
    db.add(exp)
    db.commit()
    with pytest.raises(ValidationError):
        transition(db, exp, "closed")
    transition(db, exp, "simulated")
    transition(db, exp, "approved")
    transition(db, exp, "implementing")
    transition(db, exp, "measuring")
    with pytest.raises(ValidationError):
        transition(db, exp, "evaluated")  # evaluation missing
    ev = evaluate(db, exp)
    assert ev["evidence_strength"] == "weak"
    assert any("Seasonality" in c for c in ev["confounders"])
    transition(db, exp, "evaluated")
    with pytest.raises(ValidationError):
        transition(db, exp, "closed")  # decision missing
    exp.decision = "iterate"
    transition(db, exp, "closed")
    lr = db.scalar(select(LearningRecord).where(LearningRecord.experiment_id == exp.id))
    assert lr.evidence_strength == "weak" and lr.direction == "neutral"


def test_controlled_experiment_strength():
    exp = Experiment(store_id=1, title="t", hypothesis="h", strategy_type="free_gift", design="ab_test",
                     results={"control": {"visitors": 40000, "orders": 800, "gross_profit": 30000},
                              "treatment": {"visitors": 40000, "orders": 920, "gross_profit": 34000}})
    ev = evaluate(_NoDb(), exp)
    assert ev["evidence_strength"] in ("strong", "moderate") and ev["direction"] == "positive"
    exp.results = {"control": {"visitors": 500, "orders": 10}, "treatment": {"visitors": 500, "orders": 12}}
    assert evaluate(_NoDb(), exp)["evidence_strength"] == "inconclusive"


class _NoDb:
    def get(self, *_):
        return None


def test_action_approval_safeguards(db, store):
    from app.actions.workflow import approve, create_proposal, execute, mark_applied_manually, verify_applied
    from app.models import Product

    p = Product(store_id=store.id, source="shopify", source_id="p", title="P")
    db.add(p)
    db.flush()
    v = Variant(store_id=store.id, product_id=p.id, source="shopify", source_id="v", price=100.0, sku="S")
    db.add(v)
    db.commit()
    with pytest.raises(ValidationError):
        create_proposal(db, store, "update_variant_price", {"variant_id": v.id, "new_price": 150}, title="too big")
    prop = create_proposal(db, store, "update_variant_price", {"variant_id": v.id, "new_price": 106}, title="+6%")
    assert prop.current_values == {"price": 100.0, "compare_at_price": None}
    with pytest.raises(ConflictError):
        create_proposal(db, store, "update_variant_price", {"variant_id": v.id, "new_price": 106}, title="dup")
    with pytest.raises(ValidationError):
        approve(db, prop, confirm_store_code="WRONG")
    v.price = 101.0  # store changed after the proposal
    with pytest.raises(ConflictError):
        approve(db, prop, confirm_store_code=store.code)
    v.price = 100.0
    approve(db, prop, confirm_store_code=store.code)
    with pytest.raises(CapabilityNotSupported):
        execute(db, prop)
    mark_applied_manually(db, prop)
    assert verify_applied(db, prop)["verification"] == "not_matched"
    v.price = 106.0
    assert verify_applied(db, prop)["verification"] == "matched"
    actions = [a.action for a in db.scalars(select(AuditLog))]
    assert {"action.proposed", "action.approved", "action.execute_blocked", "action.applied_manually"} <= set(actions)


def test_demo_store_cannot_get_actions(db, demo):
    from app.actions.workflow import create_proposal

    with pytest.raises(ValidationError):
        create_proposal(db, demo, "create_discount_code", {"code": "X"}, title="x")
    assert db.scalar(select(ActionProposal)) is None


def test_offer_alerts_on_demo(db, demo):
    out = alerts(db, demo)
    kinds = {a["kind"] for a in out}
    assert "overlap" not in kinds or all(a["store_id"] == demo.id for a in out)
    assert all(a["severity"] in ("critical", "warning", "info") for a in out)


def test_nice_price():
    assert nice_price(94.34, like=89.0) == 94.0
    assert nice_price(52.9, like=49.99) == 52.99
    assert nice_price(8.48, like=8.0) == 8.48


def test_store_fixture_exists(store):
    assert isinstance(store, Store)
