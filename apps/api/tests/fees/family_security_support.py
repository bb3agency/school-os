"""Family counter and fee structure notice routes for the authorization matrix (FM-07, FM-10;
FR-FEE-101..125; synthetic data only).

Loaded by path from ``tests/security/test_authz_matrix.py``. School A gets one family of two
siblings in 9A (a shared guardian) with fee accounts, created once; each allowed family payment
takes ₹1 from each account. The structure routes read the year's plans (no object id in the
path). ``/fees/students/{student_id}/family`` is covered by BOLA through ``student_id`` (school
B's student answers 404); the other routes take no path id. Fees are switched on for school A
only around each call (the paths start with ``/api/v1/fees/``), builders included.
"""

from __future__ import annotations

import importlib.util
import sys
import uuid
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any

from sqlalchemy import Engine

from app.fees import service as fees
from app.fees.family_schemas import FamilyPaymentCreate, FamilyPaymentPart

_HERE = Path(__file__).resolve()
PAY = "/api/v1/fees/family-payments"


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


def _family(w: Any, admin: Engine) -> tuple[list[uuid.UUID], list[uuid.UUID]]:
    """(students, accounts) of school A's matrix family (once)."""
    key = "fees_matrix_family"
    if key not in w.a.ids:
        S.ensure_people(admin, w.a)
        kids = [F.student(w.a), F.student(w.a)]
        guardian = F.SW.add_guardian(w.a, kids[0], full_name="Synthetica Matrix Guardian")
        F.SW.add_guardian(w.a, kids[1], guardian_id=guardian)
        accounts = [F.account(w.a, k) for k in kids]
        w.a.ids[key] = (kids, accounts)
    value: tuple[list[uuid.UUID], list[uuid.UUID]] = w.a.ids[key]
    return value


def _receipts(w: Any, admin: Engine) -> list[uuid.UUID]:
    key = "fees_matrix_family_receipts"
    if key not in w.a.ids:
        _, accounts = _family(w, admin)
        data = FamilyPaymentCreate(
            mode="cash",
            parts=[FamilyPaymentPart(account_id=a, amount=F.Decimal("1.00")) for a in accounts],
        )
        out = F.call(w.a, "office_staff", fees.issue_family_payment, data)
        w.a.ids[key] = [r.id for r in out.receipts]
    value: list[uuid.UUID] = w.a.ids[key]
    return value


def matrix_specs() -> dict[tuple[str, str], Builder]:
    def year(w: Any) -> str:
        return str(w.a.ids["year"])

    def pay(w: Any, r: str, a: Engine) -> Request:
        _, accounts = _family(w, a)
        return (
            PAY,
            {"mode": "cash", "parts": [{"account_id": str(x), "amount": "1.00"} for x in accounts]},
            {"Idempotency-Key": uuid.uuid4().hex},
        )

    def slip(w: Any, r: str, a: Engine) -> Request:
        query = "&".join(f"receipt_ids={x}" for x in _receipts(w, a))
        return (f"{PAY}/slip?{query}", None, {})

    return {
        ("GET", "/api/v1/fees/students/{student_id}/family"): lambda w, r, a: (
            f"/api/v1/fees/students/{_family(w, a)[0][0]}/family",
            None,
            {},
        ),
        ("POST", PAY): pay,
        ("GET", f"{PAY}/slip"): slip,
        ("GET", "/api/v1/fees/structure-notice"): lambda w, r, a: (
            f"/api/v1/fees/structure-notice?academic_year_id={year(w)}",
            None,
            {},
        ),
        ("GET", "/api/v1/fees/structure-notice/print"): lambda w, r, a: (
            f"/api/v1/fees/structure-notice/print?academic_year_id={year(w)}",
            None,
            {},
        ),
        ("GET", "/api/v1/fees/structure-changes"): lambda w, r, a: (
            f"/api/v1/fees/structure-changes?academic_year_id={year(w)}",
            None,
            {},
        ),
    }


MATRIX_CREATES = frozenset({PAY})

__all__ = ["MATRIX_CREATES", "matrix_specs"]
