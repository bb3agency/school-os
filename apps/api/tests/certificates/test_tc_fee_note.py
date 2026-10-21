"""The fee note on the TC preview (FR-CERT-025, US-2005 AC1; FR-FEE-030; ADR-0042 decision 13;
owner decision D16).

Given fees are on and the caller holds ``finance.read`` school-wide, the TC preview carries the
outstanding balance as a note; it is never one of the printed fields, never a blocker and never
changes ``can_issue``. Callers without ``finance.read``, other certificate types, fees off and a
student without a balance get no note. Synthetic data only.
"""

from __future__ import annotations

import importlib.util
import sys
from decimal import Decimal
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from sqlalchemy import Engine

from app.certificates import service as certificates

pytestmark = pytest.mark.db


def _load(name: str, path: Path) -> ModuleType:
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


TESTS = Path(__file__).resolve().parents[1]
F = _load("sos_test_fees_support", TESTS / "fees" / "support.py")
W = F.W
world = W.world


@pytest.fixture(scope="module")
def school(world: Any, admin_engine: Engine) -> Any:
    F.install()
    s = W.School(W.provision_school())
    s.people["owner"] = W.add_member(admin_engine, s.tenant_id, ["owner"])
    W.build_structure(s, s.people["owner"])
    for role in ("principal", "office_admin", "office_staff", "accountant"):
        s.people[role] = W.add_member(admin_engine, s.tenant_id, [role])
    F.enable(admin_engine, s.tenant_id)
    s.ids["owing"] = F.student(s)
    s.ids["owing_account"] = F.account(s, s.ids["owing"])
    F.issue(s, s.ids["owing_account"], "500.00")
    s.ids["no_account"] = F.student(s, "section_10a")
    return s


def _preview(school: Any, role: str, student: str, kind: str = "transfer") -> Any:
    return F.call(school, role, certificates.preview, school.ids[student], kind)


def test_FR_CERT_025_finance_readers_see_the_balance_as_a_note(school: Any) -> None:
    out = _preview(school, "principal", "owing")
    note = out.fee_note
    assert note is not None
    # Class IX plan: 9,000.00 charged over two instalments, 500.00 paid.
    assert note.balance == Decimal("8500.00")
    assert note.overdue > 0
    assert note.account_id == school.ids["owing_account"]
    assert note.last_payment_on is not None


def test_FR_FEE_030_the_note_is_never_printed_and_never_blocks(school: Any) -> None:
    with_note = _preview(school, "principal", "owing")
    without = _preview(school, "office_admin", "owing")
    assert with_note.fee_note is not None
    assert without.fee_note is None
    # Identical TC either way: same printed fields, blockers and can_issue.
    assert with_note.fields == without.fields
    assert with_note.blockers == without.blockers
    assert with_note.can_issue == without.can_issue
    printed = " ".join(f"{f.label_en} {f.value or ''}" for f in with_note.fields)
    assert "8500" not in printed.replace(",", "")
    assert not any("fee" in f.label_en.casefold() for f in with_note.fields)


def test_FR_CERT_025_no_note_for_other_types_no_account_or_fees_off(
    school: Any, admin_engine: Engine
) -> None:
    assert _preview(school, "principal", "owing", "bonafide").fee_note is None
    assert _preview(school, "principal", "no_account").fee_note is None
    assert _preview(school, "office_staff", "owing").fee_note is None
    F.enable(admin_engine, school.tenant_id, on=False)
    try:
        assert _preview(school, "principal", "owing").fee_note is None
    finally:
        F.enable(admin_engine, school.tenant_id)
