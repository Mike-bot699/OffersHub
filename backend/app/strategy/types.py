from dataclasses import dataclass, field
from typing import Any


@dataclass
class Evidence:
    label: str
    value: Any
    source: str  # e.g. "Shopify orders, last 90 days" | "offers workbook (documented)" | "derived"
    basis: str | None = None

    def as_dict(self) -> dict:
        return {"label": self.label, "value": self.value, "source": self.source, "basis": self.basis}


@dataclass
class RecDraft:
    detector: str
    key: str  # scope-specific suffix for the fingerprint
    strategy_type: str
    scope_type: str
    title: str
    problem: str
    proposed_action: str
    economic_rationale: str
    next_action: str
    evidence: list[Evidence] = field(default_factory=list)
    scope: dict = field(default_factory=dict)
    estimated_impact: dict | None = None  # {"metric","low","base","high","unit","basis","annualized"}
    assumptions: list[str] = field(default_factory=list)
    missing_data: list[str] = field(default_factory=list)
    confidence: str = "low"
    confidence_reason: str = ""
    risks: list[str] = field(default_factory=list)
    risk_level: str = "medium"
    complexity: str = "medium"
    recommended_test: str | None = None
    success_metrics: list[str] = field(default_factory=list)
    frameworks: list[str] = field(default_factory=list)
    impact_hint: float | None = None  # annual gross-profit-equivalent exposure, used for ranking only
    blocks_analysis: bool = False  # data gaps that prevent other analyses
