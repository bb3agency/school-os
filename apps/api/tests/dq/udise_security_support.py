"""UDISE+ correction request routes for the security suites (authorization matrix, BOLA): request
builders for school A and a request of school B, written as the test superuser (FO-02;
FR-UDI-009; synthetic data only).

Loaded by path from ``tests/security/test_authz_matrix.py`` and ``tests/security/test_bola.py``.
The world schools hold no UDISE+ differences, so the guard-passed answers of the routes that
need one are fixed here (``MATRIX_STATUS``): preparing a request for a line that has no open
difference is 422 ``line_not_open``
and the letter of a request whose lines are not open is 409 ``nothing_to_print``.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

from sqlalchemy import Engine, text

BASE = "/api/v1/dq/udise-corrections"
PROFILE = "bseap-ssc-2027"

Request = tuple[str, dict[str, Any] | None, dict[str, str]]
Builder = Callable[[Any, str, Engine], Request]


def _if_match(version: int) -> dict[str, str]:
    return {"If-Match": f'W/"{version}"'}


def insert_request(admin: Engine, school: Any, *, status: str = "prepared") -> uuid.UUID:
    """A request of ``school`` with one line on a fresh bare student (no UDISE+ value)."""
    request_id, student_id = uuid.uuid4(), uuid.uuid4()
    user = school.people["owner"].user_id
    sent = status != "prepared"
    with admin.begin() as c:
        c.execute(
            text("INSERT INTO sis.students (id, tenant_id, admission_no) VALUES (:i, :t, :a)"),
            {"i": student_id, "t": school.tenant_id, "a": f"UDS-{uuid.uuid4().hex[:10]}"},
        )
        c.execute(
            text(
                "INSERT INTO sis.udise_correction_requests (id, tenant_id, profile_key, status, "
                "lines_count, students_count, prepared_by, sent_on, sent_to, sent_by) VALUES "
                "(:i, :t, :p, :s, 1, 1, :u, :on, :to, :by)"
            ),
            {
                "i": request_id,
                "t": school.tenant_id,
                "p": PROFILE,
                "s": status,
                "u": user,
                "on": "2026-11-01" if sent else None,
                "to": "meo" if sent else None,
                "by": user if sent else None,
            },
        )
        c.execute(
            text(
                "INSERT INTO sis.udise_correction_lines (id, tenant_id, request_id, student_id, "
                "attribute_key) VALUES (:i, :t, :r, :s, 'full_name')"
            ),
            {"i": uuid.uuid4(), "t": school.tenant_id, "r": request_id, "s": student_id},
        )
    return request_id


def _shared(w: Any, admin: Engine) -> uuid.UUID:
    key = "udise_matrix_request"
    if key not in w.a.ids:
        w.a.ids[key] = insert_request(admin, w.a)
    value: uuid.UUID = w.a.ids[key]
    return value


def matrix_specs() -> dict[tuple[str, str], Builder]:
    """One builder per UDISE+ correction request route (school A)."""
    return {
        ("GET", f"{BASE}/candidates"): lambda w, r, a: (
            f"{BASE}/candidates?profile_key={PROFILE}",
            None,
            {},
        ),
        ("GET", BASE): lambda w, r, a: (BASE, None, {}),
        ("POST", BASE): lambda w, r, a: (
            BASE,
            {
                "profile_key": PROFILE,
                "lines": [{"student_id": str(uuid.uuid4()), "attribute_key": "full_name"}],
            },
            {"Idempotency-Key": uuid.uuid4().hex},
        ),
        ("GET", f"{BASE}/{{correction_id}}"): lambda w, r, a: (
            f"{BASE}/{_shared(w, a)}",
            None,
            {},
        ),
        ("POST", f"{BASE}/{{correction_id}}/sent"): lambda w, r, a: (
            f"{BASE}/{insert_request(a, w.a)}/sent",
            {"sent_on": "2026-11-02", "sent_to": "meo"},
            _if_match(1),
        ),
        ("POST", f"{BASE}/{{correction_id}}/close"): lambda w, r, a: (
            f"{BASE}/{insert_request(a, w.a, status='sent')}/close",
            {"outcome": "done"},
            _if_match(1),
        ),
        ("GET", f"{BASE}/{{correction_id}}/letter"): lambda w, r, a: (
            f"{BASE}/{_shared(w, a)}/letter",
            None,
            {},
        ),
    }


# Guard passed; the service answers (a random line, and a request whose line is not open).
MATRIX_STATUS: dict[tuple[str, str], int] = {
    ("POST", BASE): 422,
    ("GET", f"{BASE}/{{correction_id}}/letter"): 409,
}

# --- BOLA: a request of school B -----------------------------------------------------------------

_B: dict[uuid.UUID, uuid.UUID] = {}


def b_request(admin: Engine, w: Any) -> uuid.UUID:
    found = _B.get(w.b.tenant_id)
    if found is None:
        found = _B[w.b.tenant_id] = insert_request(admin, w.b)
    return found


BOLA_BODIES: dict[tuple[str, str], dict[str, Any] | None] = {
    ("POST", f"{BASE}/{{correction_id}}/sent"): {"sent_on": "2026-11-02", "sent_to": "meo"},
    ("POST", f"{BASE}/{{correction_id}}/close"): {"outcome": "done"},
}
