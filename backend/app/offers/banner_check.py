"""Mismatches between an offer and the text of its banner design (HTML files only).

Read from the banner: codes ("code HEADSTART"), percentages, dollar amounts and the end date
("Ends Sun 22 Nov"). Compared with the offer's codes, its offer texts (offer, description,
calendar coupon cell), threshold, bonus fields and end date. Image banners cannot be read.
"""

import re
from datetime import date
from html.parser import HTMLParser

MONTHS = {m: i + 1 for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}
WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


class _Text(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1
        alt = dict(attrs).get("alt")
        if alt:
            self.parts.append(f" {alt} ")

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def banner_text(html: str) -> str:
    p = _Text()
    p.feed(html or "")
    return re.sub(r"\s+", " ", " ".join(p.parts)).strip()


def _amounts(text: str) -> set[float]:
    return {float(a.replace(",", "")) for a in re.findall(r"\$\s*(\d[\d,]*(?:\.\d+)?)", text or "")}


def _pcts(text: str) -> set[float]:
    return {float(p) for p in re.findall(r"(\d+(?:\.\d+)?)\s*%", text or "")}


def check(ev) -> dict:
    """{'readable': bool, 'findings': [str], 'checked': [str]}"""
    if not ev.banner_content:
        return {"readable": False, "findings": [], "checked": [], "note": "No banner design uploaded."}
    if ev.banner_kind != "html":
        return {"readable": False, "findings": [], "checked": [],
                "note": "Image banners cannot be read: upload the HTML design to check it."}
    text = banner_text(ev.banner_content)
    codes = {c.upper() for c in ev.coupon_codes or []}
    offer_texts = " ".join(filter(None, [ev.offer_text, ev.offer_description, ev.coupon_raw]))
    bonus_texts = " ".join(filter(None, [ev.bonus, ev.free_gift, ev.free_shipping, ev.giveaway_product]))
    findings, checked = [], []

    cited = list(dict.fromkeys(c.upper() for c in re.findall(r"\bcode\s*:?\s+([A-Z][A-Z0-9]{3,})\b", text, re.IGNORECASE)))
    for c in cited:
        if c not in codes:
            findings.append(f"Banner cites code {c}, which is not a code of this offer ({', '.join(sorted(codes)) or 'none'}).")
    if codes and not any(c in text.upper() for c in codes):
        findings.append(f"Banner does not mention any of the offer's codes ({', '.join(sorted(codes))}).")
    checked.append("codes")

    offer_pcts = _pcts(offer_texts)
    for p in sorted(_pcts(text) - offer_pcts):
        if offer_pcts:
            findings.append(f"Banner says {p:g}%, the offer has {', '.join(f'{x:g}%' for x in sorted(offer_pcts))}.")
    checked.append("percentages")

    known = _amounts(offer_texts) | _amounts(bonus_texts)
    if ev.threshold_amount:
        known.add(float(ev.threshold_amount))
    for a in sorted(_amounts(text) - known):
        findings.append(f"Banner mentions ${a:g}, which is not the threshold (${ev.threshold_amount:g}) nor an amount of the offer or bonus."
                        if ev.threshold_amount else f"Banner mentions ${a:g}, not found in the offer or bonus.")
    checked.append("amounts")

    if ev.end_date:
        for wd, dd, mon in re.findall(r"\bEnds\s+(?:(Mon|Tue|Wed|Thu|Fri|Sat|Sun)\w*\s+)?(\d{1,2})(?:st|nd|rd|th)?\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\w*",
                                      text, re.IGNORECASE):
            try:
                said = date(ev.end_date.year, MONTHS[mon.lower()[:3]], int(dd))
            except ValueError:
                continue
            if said != ev.end_date:
                findings.append(f"Banner says it ends {said.day} {mon.title()[:3]}, the offer ends {ev.end_date.day} {ev.end_date.strftime('%b')}.")
            elif wd and WEEKDAYS[said.weekday()] != wd.lower()[:3]:
                findings.append(f"Banner says {wd.title()} {said.day} {mon.title()[:3]}, which is a {WEEKDAYS[said.weekday()].title()}.")
        checked.append("end date")
    return {"readable": True, "findings": list(dict.fromkeys(findings)), "checked": checked, "note": None}
