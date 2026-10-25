"""Strategic principles referenced by recommendations.

Each entry is a *sourced principle* (what a published author or study argues), kept
separate from the system's own hypotheses about a specific store. Detectors cite
principles by key; the UI shows the source so the user can judge its relevance.
"""

FRAMEWORKS: dict[str, dict] = {
    "value_equation": {
        "name": "Value Equation",
        "source": "Alex Hormozi, $100M Offers (2021)",
        "principle": "Perceived value rises with the dream outcome and perceived likelihood of achieving it, "
                     "and falls with time delay and effort/sacrifice. Improve an offer by changing these levers "
                     "rather than only cutting price.",
    },
    "offer_stacking": {
        "name": "Bonuses over discounts",
        "source": "Alex Hormozi, $100M Offers (2021)",
        "principle": "Adding bonuses (gifts, complementary items, guarantees) can raise perceived value more "
                     "cheaply than lowering price, and avoids anchoring customers on a lower price.",
    },
    "price_break_even": {
        "name": "Break-even sales change for price moves",
        "source": "T. Nagle & G. Müller, The Strategy and Tactics of Pricing (6th ed., 2018)",
        "principle": "A price change is profitable only if the volume change stays within the break-even "
                     "threshold −Δp / (contribution margin + Δp); low-margin products need large volume gains "
                     "to justify price cuts.",
    },
    "anchoring_decoy": {
        "name": "Anchoring and the decoy effect",
        "source": "D. Ariely, Predictably Irrational (2008); Huber, Payne & Puto, J. Consumer Research (1982)",
        "principle": "A premium option or reference price changes how the other options are judged; a premium "
                     "variant can lift the mix even if it sells little itself.",
    },
    "zero_price": {
        "name": "Zero-price effect",
        "source": "Shampanier, Mazar & Ariely, Marketing Science (2007)",
        "principle": "'Free' (free shipping, a free gift) is valued disproportionately compared with a "
                     "discount of similar monetary value.",
    },
    "goal_gradient": {
        "name": "Goal-gradient effect",
        "source": "Kivetz, Urminsky & Zheng, J. Marketing Research (2006)",
        "principle": "People accelerate effort as they approach a goal; visible progress toward a reward "
                     "(cart progress bars, tiers) increases completion.",
    },
    "bundling": {
        "name": "Mixed bundling",
        "source": "Stremersch & Tellis, J. Marketing (2002)",
        "principle": "Offering items both separately and as a bundle captures customers with different "
                     "valuations; bundles work best for complementary products with uneven valuations.",
    },
    "promotion_expectations": {
        "name": "Promotion-induced reference prices",
        "source": "Kalwani & Yim, J. Marketing Research (1992)",
        "principle": "Frequent price promotions lower the price customers expect to pay, reducing willingness "
                     "to buy at full price.",
    },
    "controlled_experiments": {
        "name": "Controlled experiments over before/after",
        "source": "Kohavi, Tang & Xu, Trustworthy Online Controlled Experiments (2020)",
        "principle": "Only randomised comparisons establish causality; before/after comparisons are confounded "
                     "by seasonality, traffic mix and concurrent changes.",
    },
    "retention_economics": {
        "name": "Retention economics",
        "source": "F. Reichheld, The Loyalty Effect (1996)",
        "principle": "Repeat customers often cost less to serve and buy more over time, so modest retention "
                     "gains can matter more than equal gains in acquisition — verify with your own cohorts.",
    },
    "inventory_availability": {
        "name": "Availability before demand generation",
        "source": "Standard retail operations practice",
        "principle": "Demand spent on out-of-stock items is lost or diverted; fix availability before "
                     "investing in traffic or promotions for those items.",
    },
}


def cite(*keys: str) -> list[dict]:
    return [{"key": k, **FRAMEWORKS[k]} for k in keys if k in FRAMEWORKS]
