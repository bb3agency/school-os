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


APAAR_LENGTH = 12
PEN_LENGTH = 11


def synthetic_apaar_id(rng: random.Random) -> str:
    """A synthetic APAAR-like ID (12 ASCII digits, ADR-0037) that is never mistaken for an
    Aadhaar number: it starts with ``1`` (Aadhaar numbers start 2-9) and FAILS the Verhoeff
    check, so :func:`app.core.redaction.mask_aadhaar` leaves it alone in every pipeline.

    Real APAAR IDs may pass Verhoeff (unknown on 2026-10-01); tests that need such a value build
    it inside the test process.
    """
    body = "1" + "".join(str(rng.randint(0, 9)) for _ in range(APAAR_LENGTH - 2))
    correct = verhoeff_check_digit(body)
    number = body + rng.choice([d for d in "0123456789" if d != correct])
    if verhoeff_valid(number):  # pragma: no cover - impossible by construction
        raise RuntimeError("generated a valid Verhoeff number")
    return number


def synthetic_udise_pen(rng: random.Random) -> str:
    """A synthetic UDISE+ PEN-like number (11 digits; the real format is still to confirm)."""
    return str(rng.randint(1, 9)) + "".join(str(rng.randint(0, 9)) for _ in range(PEN_LENGTH - 1))
