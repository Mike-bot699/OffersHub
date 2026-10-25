"""Infer a structured mechanic from free-text offer descriptions.

Used when importing the historical workbook, where offers are written as prose
("Extra 10% off all orders", "$30 out of $100 minimum order", "Buy 3 items get 10% off,
buy 5 items get 15% off"). The result is marked `inferred` and can be corrected by the user.
"""

import re

from app.models.enums import OfferType

_PCT = re.compile(r"(\d{1,2}(?:[.,]\d+)?)\s*%")
_AMOUNT = re.compile(r"[$€£]\s?(\d+(?:[.,]\d{1,2})?)")
_QTY_TIER = re.compile(r"buy\s*(\d+)\+?\s*(?:items?|products?)?\s*(?:get|for|and get|save)?\s*(\d{1,2})\s*%")
_INCENTIVE_WORDS = re.compile(r"%|[$€£]\s?\d|\bfree\b|\bbuy\b|\bgift\b|\bcredit\b|\boff\b|\bsave\b|\bvoucher\b|"
                              r"\bpoints\b|\bdeal|\bdiscount|\bprice\b|\bsale\b")


def classify_offer(offer_text: str | None, bonus: str | None = None, coupon: str | None = None) -> dict:
    text = (offer_text or "").lower()
    bonus_l = (bonus or "").lower()
    mechanic: dict = {"inferred": True}
    components: list[str] = []
    tiers = [(int(q), float(p)) for q, p in _QTY_TIER.findall(text)]

    if not text:
        otype = OfferType.OTHER
        mechanic["note"] = "no offer text recorded"
    elif "no offer" in text or not _INCENTIVE_WORDS.search(text):
        otype = OfferType.MESSAGE_ONLY
    elif len(tiers) >= 2 or "buy more save more" in text:
        otype = OfferType.QUANTITY
        if tiers:
            mechanic["tiers"] = [{"min_quantity": q, "value": p, "value_type": "percent"} for q, p in tiers]
    elif "spend more save more" in text or ("tier" in text and _AMOUNT.search(text)):
        otype = OfferType.TIERED
    elif re.search(r"\bfree\b.+\b(when you buy|with (every|any)? ?(purchase|order)s?|with orders? over)", text):
        otype = OfferType.FREE_GIFT
        m = re.search(r"buy\s*(\d+)", text)
        if m:
            mechanic["min_quantity"] = int(m.group(1))
    elif re.search(r"\bbuy\s*\d+|\bbuy one|\bsecond (item|one)|2nd item", text):
        otype = OfferType.SECOND_ITEM if re.search(r"second|2nd|buy 1 (item, )?get 1", text) else OfferType.BXGY
        m = re.search(r"buy\s*(\d+)", text)
        if m:
            mechanic["min_quantity"] = int(m.group(1)) + (1 if "get 1" in text else 0)
    elif "launch price" in text or "special price" in text:
        otype = OfferType.SPECIAL_PRICE
    elif re.search(r"voucher|credit|future purchase|cashback", text):
        otype = OfferType.VOUCHER
    elif "points" in text and "loyalty" in text:
        otype = OfferType.LOYALTY
    elif "free shipping" in text and not _PCT.search(text):
        otype = OfferType.FREE_SHIPPING
    elif re.search(r"\bup to\b", text) and _PCT.search(text):
        otype = OfferType.TIERED
        mechanic["note"] = "'Up to X%' — actual depth varies by product"
    elif _PCT.search(text):
        otype = OfferType.PERCENTAGE
    elif re.search(r"[$€£]\s?\d+(?:[.,]\d+)?\s*(off|out of)", text):
        otype = OfferType.FIXED
    elif "gift" in text or re.search(r"\bfree\b", text):
        otype = OfferType.FREE_GIFT
    elif "bundle" in text or re.search(r"\bkits?\b", text):
        otype = OfferType.BUNDLE
    else:
        otype = OfferType.OTHER

    pct = _PCT.search(text)
    if pct and otype not in (OfferType.QUANTITY,):
        mechanic["value"] = float(pct.group(1).replace(",", "."))
        mechanic["value_type"] = "percent"
    elif otype in (OfferType.FIXED, OfferType.VOUCHER):
        amt = _AMOUNT.search(text)
        if amt:
            mechanic["value"] = float(amt.group(1).replace(",", "."))
            mechanic["value_type"] = "fixed"
    m = re.search(r"(?:over|minimum|min\.?|out of|spend|above)\s*(?:order\s*(?:of)?\s*)?[$€£]\s?(\d+)", text)
    if m:
        mechanic["threshold_amount"] = float(m.group(1))

    if "free shipping" in text or "free shipping" in bonus_l:
        components.append("free_shipping")
    if "gift" in bonus_l or "gift" in text or otype == OfferType.FREE_GIFT:
        components.append("free_gift")
    if re.search(r"exclusive|limited edition|bundle|\bkits?\b", bonus_l):
        components.append("exclusive_bundle")
    if "mystery" in bonus_l:
        components.append("mystery_gift")
    if "donat" in bonus_l:
        components.append("charity")
    if components:
        mechanic["components"] = sorted(set(components))
    return {"offer_type": str(otype), "mechanic": mechanic}
