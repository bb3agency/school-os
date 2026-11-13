"""Enrolment statistics (FO-06) and scheme verification pack (FO-11) routes for the security
suites (authorization matrix): request builders for school A (synthetic data only).

Loaded by path from ``tests/security/test_authz_matrix.py``. The bulk certificate request names
a random student, so a role that passes the guard gets 201 with that student refused
(``not_found``): nothing is issued in the shared world.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

from sqlalchemy import Engine

STATS = "/api/v1/reports/enrolment-statistics"
SCHEMES = "/api/v1/scheme-packs"

Request = tuple[str, dict[str, Any] | None, dict[str, str]]
Builder = Callable[[Any, str, Engine], Request]


def _attendance(w: Any, suffix: str = "") -> str:
    section = w.a.ids["section_9a"]
    return (
        f"{SCHEMES}/attendance{suffix}?scheme_key=thalliki_vandanam&section_id={section}"
        "&from_date=2026-07-01&to_date=2026-07-31"
    )


def matrix_specs() -> dict[tuple[str, str], Builder]:
    """One builder per route (school A)."""
    return {
        ("GET", STATS): lambda w, r, a: (STATS, None, {}),
        ("GET", f"{STATS}/print"): lambda w, r, a: (f"{STATS}/print", None, {}),
        ("GET", f"{STATS}/xlsx"): lambda w, r, a: (f"{STATS}/xlsx", None, {}),
        ("GET", SCHEMES): lambda w, r, a: (SCHEMES, None, {}),
        ("GET", f"{SCHEMES}/attendance"): lambda w, r, a: (_attendance(w), None, {}),
        ("GET", f"{SCHEMES}/attendance/print"): lambda w, r, a: (
            _attendance(w, "/print"),
            None,
            {},
        ),
        ("GET", f"{SCHEMES}/attendance/xlsx"): lambda w, r, a: (
            _attendance(w, "/xlsx"),
            None,
            {},
        ),
        ("POST", f"{SCHEMES}/certificates"): lambda w, r, a: (
            f"{SCHEMES}/certificates",
            {"scheme_key": "scholarship", "student_ids": [str(uuid.uuid4())]},
            {"Idempotency-Key": uuid.uuid4().hex},
        ),
    }


MATRIX_CREATES: frozenset[str] = frozenset({f"{SCHEMES}/certificates"})
