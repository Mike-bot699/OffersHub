from datetime import date, datetime
from typing import Any

from sqlalchemy import Date, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class Recommendation(TimestampMixin, Base):
    """A structured, evidence-backed recommendation produced by a detector.

    `fingerprint` (detector + store + scope) lets re-analysis update an existing
    recommendation instead of creating duplicates, while preserving user status.
    """

    __tablename__ = "recommendations"

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int | None] = mapped_column(ForeignKey("stores.id", ondelete="CASCADE"), index=True)
    fingerprint: Mapped[str] = mapped_column(String(200), unique=True)
    detector: Mapped[str] = mapped_column(String(64))
    strategy_type: Mapped[str] = mapped_column(String(32))
    scope_type: Mapped[str] = mapped_column(String(16))  # store | product | variant | offer | portfolio
    scope: Mapped[dict[str, Any]] = mapped_column(default=dict)  # {"product_ids": [..], "labels": [..]}
    title: Mapped[str] = mapped_column(String(300))
    problem: Mapped[str] = mapped_column(Text)
    evidence: Mapped[list[Any]] = mapped_column(default=list)  # [{"label","value","source","basis"}]
    proposed_action: Mapped[str] = mapped_column(Text)
    objective: Mapped[str | None] = mapped_column(String(64))
    economic_rationale: Mapped[str] = mapped_column(Text)
    estimated_impact: Mapped[dict[str, Any] | None] = mapped_column()  # {"metric","low","base","high","unit","basis"}
    assumptions: Mapped[list[Any]] = mapped_column(default=list)
    missing_data: Mapped[list[Any]] = mapped_column(default=list)
    confidence: Mapped[str] = mapped_column(String(8))  # low | medium | high
    confidence_reason: Mapped[str] = mapped_column(Text)
    risks: Mapped[list[Any]] = mapped_column(default=list)
    complexity: Mapped[str] = mapped_column(String(8))  # low | medium | high
    recommended_test: Mapped[str | None] = mapped_column(Text)
    success_metrics: Mapped[list[Any]] = mapped_column(default=list)
    next_action: Mapped[str] = mapped_column(Text)
    prior_evidence: Mapped[list[Any]] = mapped_column(default=list)  # learning records consulted
    priority_score: Mapped[float] = mapped_column(Float, default=0.0)
    score_breakdown: Mapped[dict[str, Any]] = mapped_column(default=dict)
    status: Mapped[str] = mapped_column(String(16), default="open")  # open | accepted | dismissed | in_experiment | stale
    status_note: Mapped[str | None] = mapped_column(Text)
    analysis_start: Mapped[date | None] = mapped_column(Date)
    analysis_end: Mapped[date | None] = mapped_column(Date)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    is_demo: Mapped[bool] = mapped_column(default=False)
    ai_commentary: Mapped[str | None] = mapped_column(Text)
    ai_model: Mapped[str | None] = mapped_column(String(64))
    ai_generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Simulation(Base):
    __tablename__ = "simulations"

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int | None] = mapped_column(ForeignKey("stores.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(32))  # price_change | threshold | bundle | discount_level | free_gift
    name: Mapped[str] = mapped_column(String(300))
    inputs: Mapped[dict[str, Any]] = mapped_column(default=dict)
    assumptions: Mapped[list[Any]] = mapped_column(default=list)
    outputs: Mapped[dict[str, Any]] = mapped_column(default=dict)
    recommendation_id: Mapped[int | None] = mapped_column(ForeignKey("recommendations.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Experiment(TimestampMixin, Base):
    __tablename__ = "experiments"

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(300))
    hypothesis: Mapped[str] = mapped_column(Text)
    strategy_type: Mapped[str] = mapped_column(String(32))
    product_scope: Mapped[list[Any]] = mapped_column(default=list)
    status: Mapped[str] = mapped_column(String(16), default="hypothesis")
    design: Mapped[str] = mapped_column(String(24), default="before_after")  # ab_test | holdout | geo_split | before_after
    primary_metric: Mapped[str] = mapped_column(String(48), default="contribution_per_session")
    target_metrics: Mapped[list[Any]] = mapped_column(default=list)
    baseline: Mapped[dict[str, Any]] = mapped_column(default=dict)
    baseline_start: Mapped[date | None] = mapped_column(Date)
    baseline_end: Mapped[date | None] = mapped_column(Date)
    start_date: Mapped[date | None] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    results: Mapped[dict[str, Any]] = mapped_column(default=dict)  # {"control": {...}, "treatment": {...}}
    evaluation: Mapped[dict[str, Any]] = mapped_column(default=dict)  # computed by evaluator
    methodology: Mapped[str | None] = mapped_column(Text)
    costs: Mapped[float | None] = mapped_column(Float)
    economic_impact: Mapped[dict[str, Any]] = mapped_column(default=dict)
    confounders: Mapped[list[Any]] = mapped_column(default=list)
    limitations: Mapped[str | None] = mapped_column(Text)
    interpretation: Mapped[str | None] = mapped_column(Text)
    evidence_strength: Mapped[str | None] = mapped_column(String(16))
    decision: Mapped[str | None] = mapped_column(String(24))  # adopt | reject | iterate | extend
    follow_up: Mapped[str | None] = mapped_column(Text)
    recommendation_id: Mapped[int | None] = mapped_column(ForeignKey("recommendations.id", ondelete="SET NULL"))
    simulation_id: Mapped[int | None] = mapped_column(ForeignKey("simulations.id", ondelete="SET NULL"))
    history: Mapped[list[Any]] = mapped_column(default=list)  # [{"at","from","to","note"}]


class LearningRecord(Base):
    """What an experiment taught us. Weak/inconclusive findings are kept but never treated as rules."""

    __tablename__ = "learning_records"

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int | None] = mapped_column(ForeignKey("stores.id", ondelete="CASCADE"), index=True)
    experiment_id: Mapped[int | None] = mapped_column(ForeignKey("experiments.id", ondelete="CASCADE"))
    strategy_type: Mapped[str] = mapped_column(String(32))
    finding: Mapped[str] = mapped_column(Text)
    direction: Mapped[str] = mapped_column(String(12))  # positive | negative | neutral
    evidence_strength: Mapped[str] = mapped_column(String(16))
    effect: Mapped[dict[str, Any]] = mapped_column(default=dict)
    tags: Mapped[list[Any]] = mapped_column(default=list)
    active: Mapped[bool] = mapped_column(default=True)
    superseded_by_id: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ActionProposal(TimestampMixin, Base):
    """A proposed change to a live store. Nothing executes without explicit approval."""

    __tablename__ = "action_proposals"

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(48))  # update_variant_price | create_discount_code | ...
    title: Mapped[str] = mapped_column(String(300))
    payload: Mapped[dict[str, Any]] = mapped_column(default=dict)
    current_values: Mapped[dict[str, Any]] = mapped_column(default=dict)
    proposed_values: Mapped[dict[str, Any]] = mapped_column(default=dict)
    affected: Mapped[dict[str, Any]] = mapped_column(default=dict)
    expected_impact: Mapped[dict[str, Any]] = mapped_column(default=dict)
    assumptions: Mapped[list[Any]] = mapped_column(default=list)
    risks: Mapped[list[Any]] = mapped_column(default=list)
    required_permissions: Mapped[list[Any]] = mapped_column(default=list)
    status: Mapped[str] = mapped_column(String(20), default="draft")
    idempotency_key: Mapped[str] = mapped_column(String(64), unique=True)
    approved_by: Mapped[str | None] = mapped_column(String(100))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    result: Mapped[dict[str, Any]] = mapped_column(default=dict)
    error: Mapped[str | None] = mapped_column(Text)
    rollback_payload: Mapped[dict[str, Any]] = mapped_column(default=dict)
    recommendation_id: Mapped[int | None] = mapped_column(ForeignKey("recommendations.id", ondelete="SET NULL"))
    experiment_id: Mapped[int | None] = mapped_column(ForeignKey("experiments.id", ondelete="SET NULL"))


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    actor: Mapped[str] = mapped_column(String(100))
    action: Mapped[str] = mapped_column(String(64))
    entity_type: Mapped[str] = mapped_column(String(48))
    entity_id: Mapped[int | None] = mapped_column(Integer)
    store_id: Mapped[int | None] = mapped_column(Integer, index=True)
    details: Mapped[dict[str, Any]] = mapped_column(default=dict)
