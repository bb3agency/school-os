"""Fix-owner letter routes for the authorization matrix (FA-08; FR-DQ-059; synthetic data only).

Loaded by path from ``tests/security/test_authz_matrix.py``. The routes take no object id in
the path (students, sections and findings are query or body values, checked against the
caller's reach by the service; other schools' ids: tests/dq/test_fix_letters.py). The world
schools hold no board registration difference, so the board letter's guard-passed answer is 409
``nothing_to_print`` (``MATRIX_STATUS``).
"""

from __future__ import annotations

import sys
import uuid
from collections.abc import Callable
from typing import Any

from sqlalchemy import Engine

PROFILE = "bseap-ssc-2027"
BASE = "/api/v1/dq/readiness/{profile_key}"
URL = f"/api/v1/dq/readiness/{PROFILE}"

Request = tuple[str, dict[str, Any] | None, dict[str, str]]
Builder = Callable[[Any, str, Engine], Request]


def _parent_fix_student(w: Any) -> uuid.UUID:
    """A fresh Class IX student of 9A whose Aadhaar-as-printed name differs from the register."""
    from app.students.schemas import ValueIn

    sw = sys.modules["sos_test_student_world"]
    name = f"Kommineni Letter {uuid.uuid4().hex[:8].capitalize()} Sai Kumar"
    extra = [
        ValueIn(
            attribute_key="aadhaar_name_as_printed",
            source="aadhaar_as_printed",
            value=name.replace(" Sai Kumar", " Saikumar"),
        ),
        ValueIn(
            attribute_key="aadhaar_dob_as_printed", source="aadhaar_as_printed", value="2012-03-14"
        ),
        ValueIn(
            attribute_key="aadhaar_gender_as_printed", source="aadhaar_as_printed", value="male"
        ),
        ValueIn(attribute_key="full_name", source="udise_plus", value=name),
        ValueIn(attribute_key="dob", source="udise_plus", value="2012-03-14"),
        ValueIn(attribute_key="gender", source="udise_plus", value="male"),
    ]
    sid: uuid.UUID = sw.create(w.a, name=name, section_key="section_9a", extra=extra)
    return sid


def _shared(w: Any) -> uuid.UUID:
    key = "fix_letter_matrix_student"
    if key not in w.a.ids:
        w.a.ids[key] = _parent_fix_student(w)
    value: uuid.UUID = w.a.ids[key]
    return value


def matrix_specs() -> dict[tuple[str, str], Builder]:
    """One builder per fix-letter route (school A)."""
    return {
        ("GET", f"{BASE}/fix-letters"): lambda w, r, a: (f"{URL}/fix-letters", None, {}),
        ("GET", f"{BASE}/parent-letters"): lambda w, r, a: (
            f"{URL}/parent-letters?student_id={_shared(w)}",
            None,
            {},
        ),
        ("GET", f"{BASE}/board-letter"): lambda w, r, a: (f"{URL}/board-letter", None, {}),
        ("POST", f"{BASE}/fix-letters/{{letter_key}}/sent"): lambda w, r, a: (
            f"{URL}/fix-letters/parent_aadhaar/sent",
            {"student_ids": [str(_parent_fix_student(w))]},
            {"Idempotency-Key": uuid.uuid4().hex},
        ),
    }


# Guard passed; the service answers (no board registration difference in the world).
MATRIX_STATUS: dict[tuple[str, str], int] = {
    ("GET", f"{BASE}/board-letter"): 409,
}
MATRIX_CREATES: frozenset[str] = frozenset({f"{BASE}/fix-letters/{{letter_key}}/sent"})
