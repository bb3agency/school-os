"""Add-on fee routes for the security suites (authorization matrix, BOLA): request builders for
school A and an add-on of school B, created through the real services (FM-02; FR-FEE-081..100;
synthetic data only).

Loaded by path from ``tests/security/test_authz_matrix.py`` and ``tests/security/test_bola.py``.
Fees are switched on for a school only around each call (the paths start with ``/api/v1/fees/``).
Every builder is repeatable: assigning a student already on the add-on and unassigning one who is
not are both answered 200 with counts.
"""

from __future__ import annotations

import importlib.util
import itertools
import sys
import uuid
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path
from types import ModuleType
from typing import Any

from sqlalchemy import Engine

from app.fees import service as fees
from app.fees.addons_schemas import AddonCreate
from app.fees.schemas import HeadCreate

_HERE = Path(__file__).resolve()
BASE = "/api/v1/fees/addons"
REASON = "Synthetic reason for taking a student off"
_seq = itertools.count(1)


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


def _head(school: Any) -> uuid.UUID:
    """A collectable transport head of ``school`` (once)."""
    key = "fees_addon_head"
    if key not in school.ids:
        F.ensure_setup(school)
        school.ids[key] = F.call(
            school,
            "accountant",
            fees.create_head,
            HeadCreate(
                code=f"AD{uuid.uuid4().hex[:6]}".upper(),
                name="Transport band",
                kind="transport",
                gst_exempt=True,
            ),
        ).id
    value: uuid.UUID = school.ids[key]
    return value


def _make(school: Any) -> Any:
    return F.call(
        school,
        "accountant",
        fees.create_addon,
        AddonCreate(
            academic_year_id=school.ids["year"],
            head_id=_head(school),
            label=f"Matrix band {next(_seq)} {uuid.uuid4().hex[:6]}",
            amounts={1: Decimal("10.00"), 2: Decimal("10.00")},
        ),
    )


def _shared(w: Any, admin: Engine) -> Any:
    key = "fees_matrix_addon"
    if key not in w.a.ids:
        S.ensure_people(admin, w.a)
        w.a.ids[key] = _make(w.a)
    return w.a.ids[key]


def _student(w: Any, admin: Engine) -> uuid.UUID:
    key = "fees_matrix_addon_student"
    if key not in w.a.ids:
        account = S._account(w)
        w.a.ids[key] = F.scalar(
            admin, "SELECT student_id FROM fin.student_fee_accounts WHERE id = :a", a=account
        )
    value: uuid.UUID = w.a.ids[key]
    return value


def matrix_specs() -> dict[tuple[str, str], Builder]:
    """One builder per add-on route (school A)."""

    def addon(w: Any, a: Engine) -> uuid.UUID:
        value: uuid.UUID = _shared(w, a).id
        return value

    def patch(w: Any, r: str, a: Engine) -> Request:
        row = _shared(w, a)
        current = F.scalar(a, "SELECT version FROM fin.fee_addons WHERE id = :i", i=row.id)
        return (f"{BASE}/{row.id}", {"label": row.label}, {"If-Match": f'W/"{current}"'})

    def target(w: Any, a: Engine) -> dict[str, Any]:
        addon(w, a)
        return {"student_ids": [str(_student(w, a))]}

    return {
        ("GET", BASE): lambda w, r, a: (BASE, None, {}),
        ("POST", BASE): lambda w, r, a: (
            BASE,
            {
                "academic_year_id": str(w.a.ids["year"]),
                "head_id": str(_head(w.a)),
                "label": f"Matrix band {next(_seq)} {uuid.uuid4().hex[:6]}",
                "amounts": {"1": "10.00"},
            },
            {},
        ),
        ("PATCH", f"{BASE}/{{addon_id}}"): patch,
        ("POST", f"{BASE}/{{addon_id}}/assign"): lambda w, r, a: (
            f"{BASE}/{addon(w, a)}/assign",
            {**target(w, a), "start_instalment": 1},
            {},
        ),
        ("POST", f"{BASE}/{{addon_id}}/unassign"): lambda w, r, a: (
            f"{BASE}/{addon(w, a)}/unassign",
            {**target(w, a), "from_instalment": 2, "reason": REASON, "dry_run": True},
            {},
        ),
        ("GET", f"{BASE}/{{addon_id}}/students"): lambda w, r, a: (
            f"{BASE}/{addon(w, a)}/students",
            None,
            {},
        ),
    }


MATRIX_CREATES = frozenset({BASE})

# --- BOLA: an add-on of school B -----------------------------------------------------------------

_B: dict[uuid.UUID, uuid.UUID] = {}


def b_addon(admin: Engine, w: Any) -> uuid.UUID:
    found = _B.get(w.b.tenant_id)
    if found is not None:
        return found
    S.ensure_people(admin, w.b)
    with F.fees_on(admin, w.b.tenant_id):
        value: uuid.UUID = _make(w.b).id
    _B[w.b.tenant_id] = value
    return value


BOLA_BODIES: dict[tuple[str, str], dict[str, Any] | None] = {
    ("PATCH", f"{BASE}/{{addon_id}}"): {"label": "Synthetic"},
    ("POST", f"{BASE}/{{addon_id}}/assign"): {
        "student_ids": [str(uuid.uuid4())],
        "start_instalment": 1,
    },
    ("POST", f"{BASE}/{{addon_id}}/unassign"): {
        "student_ids": [str(uuid.uuid4())],
        "from_instalment": 1,
        "reason": REASON,
    },
}
