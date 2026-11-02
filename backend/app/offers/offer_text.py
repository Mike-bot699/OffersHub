"""Plain-language offer for docs, written from the calendar's Coupon cell and the store's codes.

"SPOOKLAB (15 %new) | OWLPASS (existing 20%)" with a $99 threshold on a Full-Displayed offer gives:
"New customers get 15% off orders over $99 with code SPOOKLAB, shown on the site. Email subscribers
only get 20% off orders over $99 with code OWLPASS."

Nothing is guessed: a code whose value is neither in the cell nor in the store's discount data is
not described, and without any described code the doc falls back to the Offer text.
"""

import re

from app.services.offers_import import parse_codes

SEGMENTS = [  # first match wins
    (re.compile(r"\b(existing|subscriber|vip|member|purchaser|list|email)", re.IGNORECASE), "Email subscribers only"),
    (re.compile(r"\bnew\b|\d\s*%\s*new", re.IGNORECASE), "New customers"),
    (re.compile(r"\bAU\b"), "Customers in Australia"),
]


def _amount(v: float) -> str:
    return f"${v:g}"


def describe(coupon_raw: str | None, threshold: float | None, display: str | None,
             store_codes: dict[str, dict] | None = None) -> str | None:
    if not coupon_raw:
        return None
    sentences = []
    for token in coupon_raw.split("|"):
        codes = parse_codes(token)
        if not codes:
            continue
        code = codes[0]
        notes = " ".join(re.findall(r"\(([^)]*)\)", token)) + " " + re.sub(r"\([^)]*\)", " ", token).replace(code, " ")
        pct = re.search(r"(\d+(?:\.\d+)?)\s*%", notes)
        flat = re.search(r"\$\s*(\d+(?:\.\d+)?)\s*off", notes, re.IGNORECASE)
        if pct:
            value = f"{float(pct.group(1)):g}% off"
        elif flat:
            value = f"${float(flat.group(1)):g} off"
        else:
            c = (store_codes or {}).get(code)
            if c and c.get("value") and c.get("value_type") in ("percent", "fixed"):
                value = f"{c['value']:g}% off" if c["value_type"] == "percent" else f"${c['value']:g} off"
            else:
                continue  # value unknown: not described
        floor = re.search(r"\$\s*(\d+(?:\.\d+)?)\s*\+", notes)
        floor_v = float(floor.group(1)) if floor else threshold
        segment = next((label for rx, label in SEGMENTS if rx.search(notes)), None)
        free_ship = bool(re.search(r"\+\s*FS\b|free ship", notes, re.IGNORECASE))
        what = f"{value} orders over {_amount(floor_v)}" if floor_v else value
        if free_ship:
            what += " plus free shipping"
        where = ", shown on the site" if segment == "New customers" and display == "full_site" else ""
        sentences.append(f"{segment or 'Customers'} get {what} with code {code}{where}.")
    return " ".join(sentences) or None
