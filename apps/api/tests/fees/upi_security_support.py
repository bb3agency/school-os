"""UPI and bank statement routes for the security suites (authorization matrix, BOLA): request
builders for school A and statement objects of school B, created through the real services
(R2-2E; synthetic data only).

Loaded by path from ``tests/security/test_authz_matrix.py`` and ``tests/security/test_bola.py``.
Fees are switched on for a school only around each call, as for the other fee routes.
"""

from __future__ import annotations

import importlib.util
import secrets
import sys
import uuid
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any

from sqlalchemy import Engine, text

from app.fees import service as fees
from app.fees.upi_schemas import StatementImportIn, UpiSettingsIn

_HERE = Path(__file__).resolve()


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
U = _load("sos_test_fees_upi_support", _HERE.with_name("upi_support.py"))
S = _load("sos_test_fees_security_support", _HERE.with_name("security_support.py"))

Request = tuple[str, dict[str, Any] | None, dict[str, str]]
Builder = Callable[[Any, str, Engine], Request]


def _amount() -> str:
    return f"{1000 + secrets.randbelow(5000)}.{secrets.randbelow(100):02d}"


def _import(school: Any, admin: Engine, credits: list[Any], role: str = "accountant") -> Any:
    doc = U.upload(admin, school, U.statement("generic", credits), role=role)
    ctx = F.ctx(school, school.people[role], role)
    return fees.import_statement(ctx, StatementImportIn(document_id=doc))


def _line_of(admin: Engine, import_id: uuid.UUID) -> uuid.UUID:
    with admin.connect() as c:
        value: object = c.execute(
            text("SELECT id FROM fin.bank_statement_lines WHERE import_id = :i ORDER BY line_no"),
            {"i": import_id},
        ).scalar()
    return uuid.UUID(str(value))


def _settings(w: Any) -> int:
    current = F.call(w.a, "accountant", fees.get_upi_settings)
    if not current.configured:
        current = F.call(
            w.a,
            "accountant",
            fees.put_upi_settings,
            UpiSettingsIn(payee_name="Synthetic Matrix School", vpa="matrix.school@sbi"),
            expected_version=None,
        )
    version: int = current.version
    return version


def _shared(w: Any, admin: Engine) -> dict[str, uuid.UUID]:
    """One statement of school A with a proposed credit (its receipt) and an unmatched one."""
    if "upi_matrix" not in w.a.ids:
        amount = _amount()
        receipt = U.upi_receipt(w.a, amount)
        out = _import(
            w.a,
            admin,
            [U.Credit(U.today(), amount, receipt.utr), U.Credit(U.today(), _amount(), upi=False)],
        )
        with admin.connect() as c:
            rows = c.execute(
                text(
                    "SELECT id, match_status FROM fin.bank_statement_lines WHERE import_id = :i "
                    "ORDER BY line_no"
                ),
                {"i": out.id},
            ).all()
        w.a.ids["upi_matrix"] = {
            "statement": out.id,
            "proposed": rows[0][0],
            "unmatched": rows[1][0],
            "receipt": receipt.id,
        }
    value: dict[str, uuid.UUID] = w.a.ids["upi_matrix"]
    return value


def _import_request(w: Any, r: str, a: Engine) -> Request:
    """A fresh statement uploaded by the caller (only one's own upload is read)."""
    person = w.person(r)
    doc = F.D.make_document(
        a,
        w.a.tenant_id,
        person.user_id,
        acl=[("membership", str(person.membership_id))],
        purpose="import_file",
        doc_type="import_file",
        data=U.statement("generic", [U.Credit(U.today(), _amount(), F.utr())]),
        mime_type="text/csv",
        ext="csv",
    )
    return "/api/v1/fees/bank-statements", {"document_id": str(doc)}, {}


def matrix_specs() -> dict[tuple[str, str], Builder]:
    """One builder per UPI route (school A). Repeated calls succeed (idempotent decisions)."""

    def g(path: Callable[[Any, Engine], str]) -> Builder:
        return lambda w, r, a: (path(w, a), None, {})

    def settings_put(w: Any, r: str, a: Engine) -> Request:
        version = _settings(w)
        body = {"payee_name": "Synthetic Matrix School", "vpa": "matrix.school@sbi"}
        return "/api/v1/fees/upi-settings", body, {"If-Match": f'W/"{version}"'}

    def qr(w: Any, a: Engine) -> str:
        _settings(w)
        return "/api/v1/fees/upi-settings/qr.svg"

    def stmt(w: Any, a: Engine) -> str:
        return f"/api/v1/fees/bank-statements/{_shared(w, a)['statement']}"

    return {
        ("GET", "/api/v1/fees/upi-settings"): g(lambda w, a: "/api/v1/fees/upi-settings"),
        ("PUT", "/api/v1/fees/upi-settings"): settings_put,
        ("GET", "/api/v1/fees/upi-settings/qr.svg"): g(qr),
        ("POST", "/api/v1/fees/upi/utr-check"): lambda w, r, a: (
            "/api/v1/fees/upi/utr-check",
            {"utr": F.utr()},
            {},
        ),
        ("GET", "/api/v1/fees/bank-statement-presets"): g(
            lambda w, a: "/api/v1/fees/bank-statement-presets"
        ),
        ("POST", "/api/v1/fees/bank-statements"): _import_request,
        ("GET", "/api/v1/fees/bank-statements"): g(lambda w, a: "/api/v1/fees/bank-statements"),
        ("GET", "/api/v1/fees/bank-statements/{statement_id}"): g(stmt),
        ("POST", "/api/v1/fees/bank-statements/{statement_id}/confirm"): lambda w, r, a: (
            f"{stmt(w, a)}/confirm",
            {"line_ids": [str(_shared(w, a)["proposed"])]},
            {},
        ),
        ("POST", "/api/v1/fees/bank-statement-lines/{line_id}/confirm"): lambda w, r, a: (
            f"/api/v1/fees/bank-statement-lines/{_shared(w, a)['proposed']}/confirm",
            {"receipt_id": str(_shared(w, a)["receipt"])},
            {},
        ),
        ("POST", "/api/v1/fees/bank-statement-lines/{line_id}/dismiss"): lambda w, r, a: (
            f"/api/v1/fees/bank-statement-lines/{_shared(w, a)['unmatched']}/dismiss",
            {"reason": "not_a_fee"},
            {},
        ),
        ("POST", "/api/v1/fees/bank-statements/rematch"): lambda w, r, a: (
            "/api/v1/fees/bank-statements/rematch",
            None,
            {},
        ),
        ("GET", "/api/v1/fees/upi-exceptions"): g(lambda w, a: "/api/v1/fees/upi-exceptions"),
        ("GET", "/api/v1/fees/reports/upi"): g(lambda w, a: "/api/v1/fees/reports/upi"),
    }


MATRIX_CREATES = frozenset({"/api/v1/fees/bank-statements"})


# --- BOLA: statement objects of school B ---------------------------------------------------------

_B: dict[uuid.UUID, dict[str, uuid.UUID]] = {}


def b_objects(admin: Engine, w: Any) -> dict[str, uuid.UUID]:
    """A statement and one of its credit lines in school B."""
    found = _B.get(w.b.tenant_id)
    if found is not None:
        return found
    S.ensure_people(admin, w.b)
    with F.fees_on(admin, w.b.tenant_id):
        out = _import(w.b, admin, [U.Credit(U.today(), _amount(), F.utr())])
    value = {"statement_id": out.id, "line_id": _line_of(admin, out.id)}
    _B[w.b.tenant_id] = value
    return value


BOLA_BODIES: dict[tuple[str, str], dict[str, Any] | None] = {
    ("POST", "/api/v1/fees/bank-statements/{statement_id}/confirm"): {
        "line_ids": ["01920000-0000-7000-8000-000000000001"]
    },
    ("POST", "/api/v1/fees/bank-statement-lines/{line_id}/confirm"): {
        "receipt_id": "01920000-0000-7000-8000-000000000001"
    },
    ("POST", "/api/v1/fees/bank-statement-lines/{line_id}/dismiss"): {"reason": "other"},
}
