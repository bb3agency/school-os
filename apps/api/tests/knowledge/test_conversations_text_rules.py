"""Conversation titles and summaries never keep a 12-digit number (invariant 4; D52 separators).

Synthetic numbers only (no real data, invariant 11)."""

from __future__ import annotations

import pytest

from app.knowledge.conversations import aadhaar_like, mask_numbers


@pytest.mark.parametrize(
    "number",
    [
        "2345.6789/0124",
        "2345 - 6789 - 0124",
        "2345\u00a0\u00a06789_0124",
        "2345/ 6789 .0124",
        "2345.6789/0125",  # fails Verhoeff: still any 12-digit number here
        "234567890125",
    ],
)
def test_FR_KB_012_D52_twelve_digit_numbers_with_any_separators_are_masked(number: str) -> None:
    text = f"Fees for {number} please"
    assert aadhaar_like(text)
    out = mask_numbers(text)
    for group in ("2345", "6789"):
        assert group not in out
    assert "XXXX XXXX" in out


def test_FR_KB_012_D52_short_numbers_stay() -> None:
    text = "Class 10 results for 2026-27, roll 1234"
    assert not aadhaar_like(text)
    assert mask_numbers(text) == text
