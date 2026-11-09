"""Tuition fee certificate routes for the security suites (authorization matrix, BOLA): request
builders for school A and a certificate of school B, created through the real services (FM-06;
FR-FEE-054; synthetic data only).

Loaded by path from ``tests/security/test_authz_matrix.py`` and ``tests/security/test_bola.py``.
Fees are switched on for a school only around each call (the paths start with ``/api/v1/fees/``).
"""

from __future__ import annotations

import importlib.util
import sys
import uuid
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path
from types import ModuleType
from typing import Any

from sqlalchemy import Engine, text

from app.fees import service as fees
from app.fees.schemas import AllocationIn
from app.fees.tax_schemas import TaxCertificateIssue

_HERE = Path(__file__).resolve()
FY = "2026-27"
BASE = "/api/v1/fees/tax-certificates"


def _load(name: str, path: Path) -> ModuleType:
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


F = _load("sos_test_fees_support", _HERE.with_name("support.py"))
S = _load("sos_test_fees_security_support", _HERE.with_name("security_support.py"))

Request = tuple[str, dict[str, Any] | None, dict[str, str]]
Builder = Callable[[Any, str, Engine], Request]


def _paid_student(school: Any) -> uuid.UUID:
    """A fresh student of 9A with 100.00 tuition paid today."""
    student: uuid.UUID = F.student(school)
    account = F.account(school, student)
    tui = F.ensure_setup(school).tui
    F.issue(
        school,
        account,
        "100.00",
        allocations=[AllocationIn(instalment_no=1, head_id=tui, amount=Decimal("100.00"))],
    )
    return student


def _certificate(school: Any, admin: Engine, *, rendered: bool) -> uuid.UUID:
    student = _paid_student(school)
    out = F.call(
        school,
        "accountant",
        fees.issue_tax_certificate,
        TaxCertificateIssue(student_id=student, financial_year=FY),
    )
    if rendered:
        fees.render_tax_certificate_pdf(
            school.tenant_id, out.id, school.people["accountant"].user_id, renderer=F.FakeRenderer()
        )
        doc = F.scalar(
            admin, "SELECT document_id FROM fin.tax_certificates WHERE id = :i", i=out.id
        )
        with admin.begin() as c:
            c.execute(
                text("UPDATE kb.document_versions SET status = 'ready' WHERE document_id = :d"),
                {"d": doc},
            )
    value: uuid.UUID = out.id
    return value


def _shared(w: Any, admin: Engine) -> uuid.UUID:
    key = "fees_matrix_tax_certificate"
    if key not in w.a.ids:
        S.ensure_people(admin, w.a)
        w.a.ids[key] = _certificate(w.a, admin, rendered=True)
    value: uuid.UUID = w.a.ids[key]
    return value


def _shared_student(w: Any) -> uuid.UUID:
    key = "fees_matrix_tax_student"
    if key not in w.a.ids:
        w.a.ids[key] = _paid_student(w.a)
    value: uuid.UUID = w.a.ids[key]
    return value


def matrix_specs() -> dict[tuple[str, str], Builder]:
    """One builder per tuition fee certificate route (school A)."""

    def g(path: Callable[[Any, Engine], str]) -> Builder:
        return lambda w, r, a: (path(w, a), None, {})

    return {
        ("POST", BASE): lambda w, r, a: (
            BASE,
            {"student_id": str(_shared_student(w)), "financial_year": FY},
            {},
        ),
        ("POST", f"{BASE}/bulk"): lambda w, r, a: (
            f"{BASE}/bulk",
            {"financial_year": FY, "section_id": str(w.a.ids["section_9a"])},
            {},
        ),
        ("GET", BASE): g(lambda w, a: f"{BASE}?financial_year={FY}"),
        ("GET", f"{BASE}/{{tax_certificate_id}}"): g(lambda w, a: f"{BASE}/{_shared(w, a)}"),
        ("GET", f"{BASE}/{{tax_certificate_id}}/print"): g(
            lambda w, a: f"{BASE}/{_shared(w, a)}/print"
        ),
        ("GET", f"{BASE}/{{tax_certificate_id}}/download-url"): g(
            lambda w, a: f"{BASE}/{_shared(w, a)}/download-url"
        ),
        ("POST", f"{BASE}/{{tax_certificate_id}}/render"): lambda w, r, a: (
            f"{BASE}/{_shared(w, a)}/render",
            None,
            {},
        ),
    }


MATRIX_CREATES = frozenset({BASE})

# --- BOLA: a certificate of school B -------------------------------------------------------------

_B: dict[uuid.UUID, uuid.UUID] = {}


def b_certificate(admin: Engine, w: Any) -> uuid.UUID:
    found = _B.get(w.b.tenant_id)
    if found is not None:
        return found
    S.ensure_people(admin, w.b)
    with F.fees_on(admin, w.b.tenant_id):
        value = _certificate(w.b, admin, rendered=False)
    _B[w.b.tenant_id] = value
    return value


BOLA_BODIES: dict[tuple[str, str], dict[str, Any] | None] = {
    ("POST", f"{BASE}/{{tax_certificate_id}}/render"): None,
}
