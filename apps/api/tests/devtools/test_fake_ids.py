"""Aadhaar-like synthetic numbers always FAIL the Verhoeff check (invariant 4, docs/12 §3)."""

from __future__ import annotations

import random

import pytest

from app.core.redaction import contains_full_aadhaar, verhoeff_valid
from app.devtools.fake_ids import invalid_aadhaar_like, last4


def test_docs12_s3_aadhaar_like_numbers_never_pass_verhoeff() -> None:
    rng = random.Random(1234)
    numbers = [invalid_aadhaar_like(rng) for _ in range(20_000)]
    for number in numbers:
        assert len(number) == 12
        assert number.isdigit()
        assert number[0] in "23456789"
        assert not verhoeff_valid(number)
        assert not contains_full_aadhaar(number)
    assert len(set(numbers)) > 19_000


def test_docs12_s3_aadhaar_like_numbers_are_deterministic() -> None:
    a = [invalid_aadhaar_like(random.Random(7)) for _ in range(5)]
    b = [invalid_aadhaar_like(random.Random(7)) for _ in range(5)]
    assert a == b


def test_invariant4_last4_only() -> None:
    assert last4("2345 6789 0123") == "0123"
    with pytest.raises(ValueError, match="12 digits"):
        last4("12345")
