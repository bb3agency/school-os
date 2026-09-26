"""Canonical value resolution, table-driven (docs/05 §10, FR-STU-004, BR-01, US-301 AC2)."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from app.students.canonical import resolve
from app.students.definitions import CanonicalPolicy

IDENTITY = CanonicalPolicy(
    precedence=("admission_register", "birth_certificate", "tc_incoming", "parent_form", "manual_entry"),
    require_verified=True,
    anchor="admission_register",
    show_conflicts_from=("udise_plus", "board_registration"),
)
PLAIN = CanonicalPolicy(
    precedence=("admission_register", "parent_form", "udise_plus", "manual_entry"),
    require_verified=False,
    anchor=None,
    show_conflicts_from=(),
)


@dataclass(frozen=True)
class V:
    source: str
    verification_status: str
    text: str


R, U = "verified", "unverified"

# (policy, current {source: (status, text)}, expected source, provisional, conflicts)
CASES = [
    # BR-01: the verified admission-register value is canonical and not provisional.
    (IDENTITY, {"admission_register": (R, "A"), "udise_plus": (R, "B")}, "admission_register", False, ["udise_plus"]),
    # Unverified register value: still shown, but provisional (DQ-005-style finding later).
    (IDENTITY, {"admission_register": (U, "A")}, "admission_register", True, []),
    # A verified birth certificate beats an unverified register value, but stays provisional.
    (IDENTITY, {"admission_register": (U, "A"), "birth_certificate": (R, "C")}, "birth_certificate", True, ["admission_register"]),
    # Other sources never become canonical, even verified: they only produce conflicts.
    (IDENTITY, {"udise_plus": (R, "B"), "aadhaar_as_printed": (R, "B")}, None, True, []),
    (IDENTITY, {"admission_register": (U, "A"), "udise_plus": (R, "A")}, "admission_register", True, []),
    # Rejected values never count.
    (IDENTITY, {"admission_register": ("rejected", "A"), "parent_form": (U, "D")}, "parent_form", True, []),
    # Nothing recorded for an identity attribute: missing and provisional.
    (IDENTITY, {}, None, True, []),
    # Non-identity: first source by precedence, verified or not, never provisional.
    (PLAIN, {"udise_plus": (R, "X"), "parent_form": (U, "Y")}, "parent_form", False, ["udise_plus"]),
    (PLAIN, {"manual_entry": (U, "X")}, "manual_entry", False, []),
    (PLAIN, {}, None, False, []),
]


@pytest.mark.parametrize(("policy", "current", "source", "provisional", "conflicts"), CASES)
def test_FR_STU_004_canonical_resolution(
    policy: CanonicalPolicy,
    current: dict[str, tuple[str, str]],
    source: str | None,
    provisional: bool,
    conflicts: list[str],
) -> None:
    rows = {s: V(s, status, t) for s, (status, t) in current.items()}
    res = resolve(policy, rows, compare={s: v.text for s, v in rows.items()})
    assert (res.value.source if res.value else None) == source
    assert res.provisional is provisional
    assert list(res.conflicts) == conflicts


def test_no_conflicts_without_comparison_values() -> None:
    rows = {"admission_register": V("admission_register", R, "A"), "udise_plus": V("udise_plus", R, "B")}
    assert resolve(IDENTITY, rows).conflicts == ()
