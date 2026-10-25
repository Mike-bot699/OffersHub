from datetime import date

import pytest

from app.services.parsing import (
    detect_currency,
    parse_date,
    parse_number,
    parse_percent,
    store_code,
    strip_decoration,
)


@pytest.mark.parametrize("raw,expected", [
    ("$7.820,52", 7820.52),        # EU thousands + decimal
    ("$10,154.04", 10154.04),      # US
    ("$127,20", 127.20),           # EU decimal only
    ("$0,00", 0.0),
    ("€2.618,28", 2618.28),
    ("$99", 99.0),
    ("$104.038,87", 104038.87),
    ("$1,527.33", 1527.33),
    (12.5, 12.5),
])
def test_money(raw, expected):
    val, _ = parse_number(raw)
    assert val == pytest.approx(expected)


def test_count_thousands():
    assert parse_number("75,858", kind="count")[0] == 75858
    assert parse_number("16788", kind="count")[0] == 16788


def test_ambiguous_money_is_flagged():
    val, issue = parse_number("$1,527")
    assert val == 1527 and "ambiguous" in issue


def test_spreadsheet_errors():
    assert parse_number("#VALUE!") == (None, "spreadsheet error value")
    assert parse_number("-")[0] is None


def test_percent():
    assert parse_percent("15,41%")[0] == pytest.approx(0.1541)
    assert parse_percent("0,11%")[0] == pytest.approx(0.0011)


@pytest.mark.parametrize("raw,expected", [
    ("23/9/2024", date(2024, 9, 23)),
    ("01/12/2024", date(2024, 12, 1)),
    ("2025-01-09", date(2025, 1, 9)),
    ("05.11.2025", date(2025, 11, 5)),
])
def test_dates(raw, expected):
    assert parse_date(raw)[0] == expected


def test_date_without_year_uses_default_and_flags():
    d, issue = parse_date("18/11", default_year=2024)
    assert d == date(2024, 11, 18) and "no year" in issue


def test_store_codes():
    assert store_code("🧥 WB") == "WB"
    assert store_code("🧸 GRG 🇩🇪") == "GRG"
    assert store_code("👗 BEA") == "BEA"


def test_strip_decoration():
    assert strip_decoration("🍂 Autumn Breeze: Fall Sale") == "Autumn Breeze: Fall Sale"
    assert detect_currency("€99,00") == "EUR"
