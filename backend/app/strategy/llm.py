"""Optional AI strategist commentary (Claude).

The deterministic engine decides *what* is recommended and computes every number.
The model is only asked to critique and extend a recommendation using the evidence
JSON it is given: alternative explanations, offer-design ideas, test-design risks.
Its output is stored separately (`ai_commentary`) and labelled as AI hypotheses.
No credentials or customer data are sent; only aggregated metrics.
"""

import json
import logging

from app.core.config import get_settings
from app.core.errors import ConfigurationError, IntegrationError
from app.db.base import utcnow
from app.models import Recommendation, Store
from app.strategy.frameworks import FRAMEWORKS

log = logging.getLogger(__name__)

SYSTEM = """You are a senior ecommerce strategist reviewing one recommendation produced by an analytics system.

Rules:
- Use only the numbers in the provided JSON. Never invent metrics, results, customer behaviour or sources.
- Separate clearly: (1) what the data shows, (2) your hypotheses, (3) what would falsify them.
- You may draw on the listed principles; name the source when you do. Do not cite anything else as a source.
- Prefer non-discount levers (bonuses, bundles, merchandising, thresholds) unless the data justifies a discount.
- Point out confounders and weaknesses in the proposed test.
- If the evidence is too thin to act on, say so plainly.

Write in concise Markdown with these headings: "What the data shows", "Alternative explanations",
"Ways to strengthen the offer", "Test design notes", "Verdict". Keep it under 350 words."""


def available() -> bool:
    key = get_settings().anthropic_api_key
    return bool(key and key.get_secret_value())


def _payload(rec: Recommendation, store: Store | None) -> dict:
    return {
        "store": {"code": store.code, "currency": store.currency, "is_demo": store.is_demo} if store else None,
        "title": rec.title, "strategy_type": rec.strategy_type, "problem": rec.problem,
        "proposed_action": rec.proposed_action, "economic_rationale": rec.economic_rationale,
        "evidence": rec.evidence, "estimated_impact": rec.estimated_impact, "assumptions": rec.assumptions,
        "missing_data": rec.missing_data, "confidence": rec.confidence, "confidence_reason": rec.confidence_reason,
        "risks": rec.risks, "recommended_test": rec.recommended_test, "prior_experiments": rec.prior_evidence,
        "analysis_period": [str(rec.analysis_start), str(rec.analysis_end)],
    }


def commentary(rec: Recommendation, store: Store | None) -> str:
    if not available():
        raise ConfigurationError("AI commentary is off: set EGI_ANTHROPIC_API_KEY to enable it.")
    import anthropic

    settings = get_settings()
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key.get_secret_value())
    principles = "\n".join(f"- {v['name']} ({v['source']}): {v['principle']}" for v in FRAMEWORKS.values())
    try:
        response = client.beta.messages.create(
            model=settings.ai_model,
            max_tokens=16000,
            system=SYSTEM + "\n\nPrinciples you may reference:\n" + principles,
            output_config={"effort": "medium"},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            messages=[{"role": "user", "content": "Recommendation to review:\n```json\n"
                       + json.dumps(_payload(rec, store), default=str, indent=1) + "\n```"}],
        )
    except anthropic.AuthenticationError as exc:
        raise ConfigurationError("The Anthropic API key was rejected.") from exc
    except anthropic.RateLimitError as exc:
        raise IntegrationError("AI rate limit reached; try again shortly.", retryable=True) from exc
    except anthropic.APIStatusError as exc:
        raise IntegrationError(f"AI request failed ({exc.status_code}).", retryable=exc.status_code >= 500) from exc
    except anthropic.APIConnectionError as exc:
        raise IntegrationError("Could not reach the AI service.", retryable=True) from exc
    if response.stop_reason == "refusal":
        raise IntegrationError("The AI declined to comment on this recommendation.")
    text = "\n".join(b.text for b in response.content if b.type == "text").strip()
    if not text:
        raise IntegrationError("The AI returned no commentary.")
    rec.ai_commentary = text
    rec.ai_model = response.model
    rec.ai_generated_at = utcnow()
    return text
