"""Money, numbering and print templates (pure; FR-FEE-011, FR-FEE-028; ADR-0042 P2)."""

from __future__ import annotations

import datetime as dt
import random
from decimal import Decimal

import pytest

from app.fees import templates
from app.fees.config import load_config
from app.fees.money import (
    AmountError,
    amount,
    financial_year,
    group_indian,
    in_words,
    percent_of,
    spread,
    total,
)


@pytest.mark.parametrize("value", ["12.345", "1e-3", "nan", "inf", "abc", 1.5, True])
def test_FR_FEE_028_amounts_are_exact_two_place_decimals(value: object) -> None:
    with pytest.raises(AmountError):
        amount(value)


@pytest.mark.parametrize(("value", "expected"), [("12.3", "12.30"), (7, "7.00"), ("0.10", "0.10")])
def test_FR_FEE_028_amount_keeps_exact_value(value: object, expected: str) -> None:
    assert amount(value) == Decimal(expected)
    assert str(amount(value)) == expected


def test_FR_FEE_028_sums_are_exact_property() -> None:
    """Property: adding many paisa amounts as Decimal never drifts (no float anywhere)."""
    rng = random.Random(20261017)
    for _ in range(200):
        parts = [Decimal(rng.randint(1, 10_000_000)) / 100 for _ in range(rng.randint(1, 60))]
        cents = sum(int(p * 100) for p in parts)
        assert total(parts) == Decimal(cents) / 100
        assert total(parts).as_tuple().exponent == -2


def test_FR_FEE_010_spread_fills_oldest_first_and_refuses_overflow() -> None:
    assert spread(Decimal("150.00"), [Decimal("100.00"), Decimal("80.00")]) == [
        Decimal("100.00"),
        Decimal("50.00"),
    ]
    with pytest.raises(AmountError):
        spread(Decimal("200.00"), [Decimal("100.00"), Decimal("80.00")])


def test_FR_FEE_008_percent_rounds_half_up_to_the_paisa() -> None:
    assert percent_of(Decimal("333.33"), Decimal("50")) == Decimal("166.67")
    assert percent_of(Decimal("3000.00"), Decimal("12.5")) == Decimal("375.00")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("0", "0.00"),
        ("999", "999.00"),
        ("1000", "1,000.00"),
        ("125000.5", "1,25,000.50"),
        ("12345678.9", "1,23,45,678.90"),
    ],
)
def test_receipt_amounts_use_indian_grouping(value: str, expected: str) -> None:
    assert group_indian(Decimal(value)) == expected


def test_receipt_amount_in_words() -> None:
    assert in_words(Decimal("1250.50")) == (
        "Rupees One Thousand Two Hundred Fifty and Fifty Paise Only"
    )
    assert in_words(Decimal("150000")) == "Rupees One Lakh Fifty Thousand Only"
    assert in_words(Decimal("10000000")) == "Rupees One Crore Only"


@pytest.mark.parametrize(
    ("day", "expected"),
    [
        (dt.date(2026, 4, 1), "2026-27"),
        (dt.date(2026, 10, 17), "2026-27"),
        (dt.date(2027, 3, 31), "2026-27"),
        (dt.date(2027, 4, 1), "2027-28"),
        (dt.date(2099, 12, 31), "2099-00"),
    ],
)
def test_ADR_0042_P2_one_series_per_financial_year(day: dt.date, expected: str) -> None:
    assert financial_year(day) == expected


def test_ADR_0042_numbering_formats() -> None:
    cfg = load_config().numbering
    assert cfg.receipt.render("2026-27", 123) == "RCT/2026-27/000123"
    assert cfg.refund.render("2026-27", 1) == "RFD/2026-27/0001"


def _content(**over: object) -> dict[str, object]:
    base: dict[str, object] = {
        "school": {"name": "Synthetic <School>", "address": "Synthetic road", "affiliation": ""},
        "receipt_number": "RCT/2026-27/000001",
        "financial_year": "2026-27",
        "receipt_date": "2026-10-17",
        "academic_year": "2026-27",
        "student": {"name": "<script>x</script>", "admission_no": "A1", "class_section": "IX-A"},
        "lines": [
            {
                "instalment_no": 1,
                "label": "Term 1",
                "head_name": "Tuition fee",
                "amount": "1000.00",
                "amount_text": "1,000.00",
            }
        ],
        "amount": "1000.00",
        "amount_text": "1,000.00",
        "amount_in_words": "Rupees One Thousand Only",
        "mode": "upi_qr",
        "mode_details": {},
        "utr": "499118665246",
        "collected_by_name": "Synthetic Clerk",
        "gst_note": "Education services: exempt from GST",
        "paper_size": "A5",
        "template_version": "v1",
    }
    base.update(over)
    return base


def test_FR_FEE_011_receipt_page_escapes_and_has_no_scripts() -> None:
    page = templates.render_receipt(_content())
    assert "<script>" not in page
    assert "&lt;script&gt;" in page
    assert "size: A5" in page
    assert "Education services: exempt from GST" in page


def test_FR_FEE_014_utr_is_shown_unmasked_in_its_field_only() -> None:
    # 499118665246 passes the Verhoeff check (an Aadhaar-like number): the typed UTR field shows
    # it as typed; anywhere else it would be masked.
    page = templates.render_receipt(_content(gst_note="Note 499118665246"))
    assert 'UTR) <span class="ref">499118665246</span>' in page
    assert "Note 499118665246" not in page


def test_FR_FEE_012_cancelled_receipt_is_marked() -> None:
    page = templates.render_receipt(_content(), cancelled_on=dt.date(2026, 10, 18))
    assert "CANCELLED on 18/10/2026" in page


def test_receipt_style_is_pinned_in_the_csp() -> None:
    for style in (templates.RECEIPT_STYLE_A4, templates.RECEIPT_STYLE_A5, templates.REPORT_STYLE):
        assert templates._style_hash(style) in templates.STYLE_CSP
