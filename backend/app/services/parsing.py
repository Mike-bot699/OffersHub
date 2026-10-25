"""Tolerant parsers for human-maintained spreadsheets.

The historical workbook mixes European ("$7.820,52") and US ("$10,154.04") number
formats, several date formats, emoji prefixes and free text. Each parser returns
`(value, issue)` so ambiguous input is reported instead of silently guessed.
"""

import re
import unicodedata
from datetime import date, datetime

CURRENCY_SYMBOLS = {"$": "USD", "€": "EUR", "£": "GBP", "A$": "AUD", "C$": "CAD", "NZ$": "NZD", "zł": "PLN"}
_NUM_CLEAN = re.compile(r"[^\d,.\-]")


def detect_currency(text: str | None) -> str | None:
    if not text:
        return None
    for sym in sorted(CURRENCY_SYMBOLS, key=len, reverse=True):
        if sym in text:
            return CURRENCY_SYMBOLS[sym]
    return None


def parse_number(raw, *, kind: str = "money") -> tuple[float | None, str | None]:
    """Parse a number written in either EU or US convention.

    kind="money": a lone separator followed by exactly 2 digits is a decimal mark.
    kind="count": a lone separator followed by exactly 3 digits is a thousands mark.
    """
    if raw is None:
        return None, None
    if isinstance(raw, (int, float)):
        return float(raw), None
    s = str(raw).strip()
    if s in ("", "-", "—", "–", "n/a", "N/A", "#VALUE!", "#DIV/0!", "#REF!"):
        return None, ("spreadsheet error value" if s.startswith("#") else None)
    neg = s.startswith("-") or (s.startswith("(") and s.endswith(")"))
    s = _NUM_CLEAN.sub("", s).lstrip("-")
    if not s or not re.search(r"\d", s):
        return None, f"not a number: {raw!r}"
    issue = None
    if "," in s and "." in s:
        dec = "," if s.rfind(",") > s.rfind(".") else "."
        thou = "." if dec == "," else ","
        s = s.replace(thou, "").replace(dec, ".")
    elif "," in s or "." in s:
        sep = "," if "," in s else "."
        parts = s.split(sep)
        tail = parts[-1]
        if len(parts) > 2 or len(tail) == 3 and kind == "count":  # 1.234.567 or 1,234,567
            s = "".join(parts)
        elif len(tail) == 3 and kind == "money":
            s = "".join(parts)
            issue = f"ambiguous separator in {raw!r}; read as thousands"
        elif len(tail) in (1, 2):
            s = parts[0] + "." + tail
        else:
            s = parts[0] + "." + tail
            issue = f"unusual number format {raw!r}"
    try:
        val = float(s)
    except ValueError:
        return None, f"not a number: {raw!r}"
    return (-val if neg else val), issue


def parse_percent(raw) -> tuple[float | None, str | None]:
    """Returns a fraction (15,41% -> 0.1541)."""
    if raw is None or raw == "":
        return None, None
    if isinstance(raw, (int, float)):
        return (float(raw) if raw <= 1 else float(raw) / 100), None
    val, issue = parse_number(str(raw).replace("%", ""), kind="money")
    return (val / 100 if val is not None else None), issue


def parse_date(raw, *, default_year: int | None = None) -> tuple[date | None, str | None]:
    if raw is None or raw == "":
        return None, None
    if isinstance(raw, datetime):
        return raw.date(), None
    if isinstance(raw, date):
        return raw, None
    s = str(raw).strip()
    m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})(?:[ T].*)?", s)
    if m:
        y, mo, d = map(int, m.groups())
        return _safe_date(y, mo, d, raw)
    m = re.fullmatch(r"(\d{1,2})[/.\-](\d{1,2})(?:[/.\-](\d{2,4}))?", s)
    if m:
        d, mo = int(m.group(1)), int(m.group(2))
        if m.group(3):
            y = int(m.group(3))
            y = y + 2000 if y < 100 else y
            issue = None
        elif default_year:
            y, issue = default_year, f"date {raw!r} has no year; assumed {default_year}"
        else:
            return None, f"date {raw!r} has no year"
        if mo > 12 and d <= 12:  # clearly month/day order
            d, mo = mo, d
            issue = f"date {raw!r} read as month/day"
        out, err = _safe_date(y, mo, d, raw)
        return out, err or issue
    return None, f"unrecognised date {raw!r}"


def _safe_date(y, mo, d, raw):
    try:
        return date(y, mo, d), None
    except ValueError:
        return None, f"invalid date {raw!r}"


_STORE_CODE = re.compile(r"\b([A-Z]{2,8})\b")


def strip_decoration(text: str | None) -> str:
    """Remove emoji, flags and odd unicode styling (e.g. 'ᴠɪᴘ') while keeping words."""
    if not text:
        return ""
    out = []
    for ch in unicodedata.normalize("NFKC", str(text)):
        cat = unicodedata.category(ch)
        # Drop pictographs/emoji (So, Sk) and invisible formatting; keep currency (Sc) and math (Sm).
        cp = ord(ch)
        if (cat in ("So", "Sk", "Cf", "Co", "Cs") or 0x1F1E6 <= cp <= 0x1F1FF  # regional-indicator flags
                or 0xFE00 <= cp <= 0xFE0F or cp == 0x20E3 or 0x1F3FB <= cp <= 0x1F3FF):  # variation/keycap/skin
            continue
        out.append(ch)
    return re.sub(r"\s+", " ", "".join(out)).strip()


def store_code(raw: str | None) -> str | None:
    m = _STORE_CODE.search(strip_decoration(raw))
    return m.group(1) if m else None


def norm_key(text: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", " ", strip_decoration(text).lower()).strip()
