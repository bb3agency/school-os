"""Aadhaar-LIKE test numbers that are guaranteed NOT to be valid Aadhaar numbers (docs/12 §3).

CLAUDE.md invariant 4: SchoolOS never stores Aadhaar numbers. Synthetic datasets still need
12-digit look-alikes (to test import parsing, masking and the "last 4 only" rule), so every
number generated here FAILS the Verhoeff check: :func:`app.core.redaction.verhoeff_valid` is
false for all of them, which also means it can never be a real Aadhaar number. Dedicated
redaction tests that need valid-checksum numbers build them inside the test process instead.

Students are M1: nothing here writes to the database.
"""

from __future__ import annotations

import random

from app.core.redaction import verhoeff_check_digit, verhoeff_valid

AADHAAR_LENGTH = 12


def invalid_aadhaar_like(rng: random.Random) -> str:
    """A 12-digit string shaped like an Aadhaar number (first digit 2-9) with a WRONG check digit.

    Deterministic for a given ``rng`` state. The last digit is chosen from the nine digits that
    are not the Verhoeff check digit, so the checksum always fails.
    """
    body = str(rng.randint(2, 9)) + "".join(str(rng.randint(0, 9)) for _ in range(10))
    correct = verhoeff_check_digit(body)
    wrong = rng.choice([d for d in "0123456789" if d != correct])
    number = body + wrong
    if verhoeff_valid(number):  # pragma: no cover - impossible by construction
        raise RuntimeError("generated a valid Verhoeff number")
    return number


def last4(number: str) -> str:
    """The only part SchoolOS may store (``aadhaar_last4``, docs/05)."""
    digits = "".join(ch for ch in number if ch.isdigit())
    if len(digits) != AADHAAR_LENGTH:
        raise ValueError("expected 12 digits")
    return digits[-4:]
