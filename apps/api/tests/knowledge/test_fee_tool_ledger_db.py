"""``get_fee_dues`` on the SchoolOS fee ledger while fees are on (FR-KB-044, US-2112; docs/06
§7, §13.4; invariants 3, 8, 9; SEC-001).

A synthetic school with fees on (flag and setting) and the Tally connector ALSO on, with a Tally
ledger linked to the same student: the tool must read the fee ledger through ``fees.service``
and never Tally. Offered only with ``finance.read`` school-wide; another school's student and a
student outside the caller's reach are error results; a student without a fee account gets "no
fee account", never a figure; the school view is numbers per class, no names. With fees off the
M6 Tally behaviour stays (``test_fee_tool_db.py``). Synthetic data only.
"""

from __future__ import annotations

import importlib.util
import sys
import uuid
from collections.abc import Iterator, Mapping
from decimal import Decimal
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from sqlalchemy import Engine, text

from app.core.db import tenant_session
from app.knowledge import sources
from app.knowledge.config.tools import load_tools_config
from app.knowledge.tools import registry
from app.knowledge.tools.fees import NAME, GetFeeDuesTool, inr

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
T = _load("sos_test_tally_support", TESTS / "tally" / "support.py")
W = F.W
SW = F.SW
world = W.world

TALLY_LEDGER = "Synthetica Ledger Tally Only 9A"
TALLY_AMOUNT = Decimal("43210.00")


@pytest.fixture(scope="module")
def school(world: Any, admin_engine: Engine) -> Iterator[Any]:
    """Fees on; Tally connector on with a linked ledger for ``paid``; accounts in 9A only."""
    F.install()
    s = W.School(W.provision_school())
    s.people["owner"] = W.add_member(admin_engine, s.tenant_id, ["owner"])
    W.build_structure(s, s.people["owner"])
    for role in ("principal", "office_admin", "office_staff", "accountant", "auditor_readonly"):
        s.people[role] = W.add_member(admin_engine, s.tenant_id, [role])
    s.people["class_teacher"] = W.add_member(
        admin_engine, s.tenant_id, ["class_teacher"], scopes=[("section", s.ids["section_9a"])]
    )
    F.enable(admin_engine, s.tenant_id)
    paid = F.student(s)
    unpaid = F.student(s)
    s.ids["paid_account"] = F.account(s, paid)
    s.ids["unpaid_account"] = F.account(s, unpaid)
    F.issue(s, s.ids["paid_account"], "1500.00")
    s.ids["paid"] = paid
    s.ids["unpaid"] = unpaid
    s.ids["no_account"] = F.student(s, "section_10a")
    owner = s.people["owner"].user_id
    party = T.seed_party(admin_engine, s.tenant_id, owner, ledger=TALLY_LEDGER)
    with admin_engine.begin() as c:
        c.execute(
            text("UPDATE ops.tally_parties SET closing_balance = :a WHERE id = :i"),
            {"a": TALLY_AMOUNT, "i": party},
        )
    T.seed_link(admin_engine, s.tenant_id, party, paid, owner)
    T.set_flag(admin_engine, s.tenant_id, enabled=True)
    yield s
    T.set_flag(admin_engine, s.tenant_id, enabled=False)


def _tool() -> GetFeeDuesTool:
    return GetFeeDuesTool(load_tools_config().tools[NAME])


def _run(school: Any, role: str, args: Mapping[str, object]) -> Any:
    tool = _tool()
    c = F.ctx(school, school.people[role], role)
    with tenant_session(school.tenant_id, c.user_id) as db:
        return tool.run(db, c, "call-1", args)


def _text(outcome: Any) -> str:
    assert not outcome.is_error
    assert len(outcome.blocks) == 1
    return str(outcome.blocks[0].text)


def test_FR_KB_044_offered_to_school_wide_finance_readers_when_fees_are_on(
    school: Any, admin_engine: Engine
) -> None:
    tools = {NAME: _tool()}
    T.set_flag(admin_engine, school.tenant_id, enabled=False)  # the ledger alone is enough
    try:
        for role, expected in (
            ("owner", True),
            ("principal", True),
            ("accountant", True),
            ("office_admin", False),
            ("office_staff", False),
            ("class_teacher", False),
        ):
            c = F.ctx(school, school.people[role], role)
            with tenant_session(school.tenant_id, c.user_id) as db:
                names = [t.spec.name for t in registry.offered(tools, c, db)]  # type: ignore[arg-type]
            assert (NAME in names) is expected, role
    finally:
        T.set_flag(admin_engine, school.tenant_id, enabled=True)


def test_FR_KB_044_student_figure_comes_from_the_ledger_never_tally(school: Any) -> None:
    outcome = _run(school, "accountant", {"student_id": str(school.ids["paid"])})
    said = _text(outcome)
    setup = F.ensure_setup(school)
    # 9A plan: 3,500 collectable + 1,000 taxable transport per instalment, two instalments.
    assert "Fee balance for Synthetica Fee Student" in said
    assert inr(Decimal("9000.00") - Decimal("1500.00")) in said
    assert "Term 1" in said
    assert "Term 2" in said
    assert "Last payment on" in said
    assert "SchoolOS fee ledger" in said
    assert setup.first_due.strftime("%d/%m/%Y") in said
    assert inr(TALLY_AMOUNT) not in said
    assert "Tally" not in said
    assert TALLY_LEDGER not in said
    source = outcome.blocks[0].source
    assert source.startswith("sos://fee/")
    assert sources.parse(source).kind == "fee"


def test_FR_KB_044_a_student_without_a_fee_account_gets_no_figure(school: Any) -> None:
    said = _text(_run(school, "principal", {"student_id": str(school.ids["no_account"])}))
    assert "has no fee account" in said
    assert "₹" not in said


def test_FR_KB_044_school_totals_are_numbers_per_class_without_names(school: Any) -> None:
    said = _text(_run(school, "owner", {}))
    assert "from the SchoolOS fee ledger" in said
    assert "By class: Class IX:" in said
    assert "Synthetica" not in said
    assert inr(TALLY_AMOUNT) not in said
    # both 9A accounts owe: 9,000 + 7,500
    assert inr(Decimal("16500.00")) in said


def test_FR_KB_044_the_tool_rechecks_permission_when_it_runs(school: Any) -> None:
    args = {"student_id": str(school.ids["paid"])}
    assert _run(school, "office_admin", args).is_error
    assert _run(school, "class_teacher", args).is_error
    assert _run(school, "office_staff", {}).is_error


def test_SEC_001_another_schools_student_is_an_error_result(world: Any, school: Any) -> None:
    other = SW.create(world.b, name="Synthetica Other School Pupil", section_key="section_9a")
    assert _run(school, "accountant", {"student_id": str(other)}).is_error
    assert _run(school, "accountant", {"student_id": str(uuid.uuid4())}).is_error


def test_FR_KB_044_fees_off_falls_back_to_tally(school: Any, admin_engine: Engine) -> None:
    F.enable(admin_engine, school.tenant_id, on=False)
    try:
        said = _text(_run(school, "accountant", {"student_id": str(school.ids["paid"])}))
        assert inr(TALLY_AMOUNT) in said
        assert "Tally" in said
    finally:
        F.enable(admin_engine, school.tenant_id)
