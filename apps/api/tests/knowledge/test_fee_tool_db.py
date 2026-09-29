"""``get_fee_dues`` on the real database through the ask route (M6; ADR-0032 Proposed;
FR-TALLY-008; ADR-0008; invariants 3, 8, 9; SEC-018, SEC-020).

A scripted model calls ``get_fee_dues`` once and cites what it gets back. Each test checks what
reached the model (the recorded request bodies): the tool is offered only with ``finance.read``
held school-wide AND the school's ``tally.connector.enabled`` flag on; only ledgers LINKED to the
student are read; ledger names never reach the model; an unlinked student gets "no fee figure",
never a guess by name; another school's student is an error result. Synthetic data only.
"""

from __future__ import annotations

import importlib.util
import sys
import uuid
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from sqlalchemy import Engine

from app.core.db import tenant_session
from app.knowledge import composition
from app.knowledge.config.tools import load_tools_config
from app.knowledge.tools.fees import GetFeeDuesTool, inr

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
K = _load("sos_test_ask_support", TESTS / "knowledge" / "ask_support.py")
R = _load("sos_test_record_tools_db", TESTS / "knowledge" / "test_record_tools_db.py")
T = _load("sos_test_tally_support", TESTS / "tally" / "support.py")
W = K.W
world = W.world
api = W.api
ask_with = R.ask_with

LINKED_LEDGER = "Synthetica Venkata Sai 9A Fees"
FAMILY_LEDGER = "Synthetica Family Transport"
UNLINKED_LEDGER = "Synthetica Lakshmi Devi 9C"


@pytest.fixture(scope="module")
def fees(world: Any, admin_engine: Engine) -> Iterator[dict[str, uuid.UUID]]:
    """School A: s9a linked to two ledgers (15,000.00 and 1,250.50), s9c has an UNLINKED
    ledger whose name matches her (7,500.00); AI and the connector on for the module."""
    K.install_runtime()
    ids = K.SW.ensure_students(world)
    K.enable_ai(admin_engine, world.a.tenant_id)
    composition.set_runtime(None)
    owner = world.a.people["owner"].user_id
    tenant = world.a.tenant_id
    main = T.seed_party(admin_engine, tenant, owner, ledger=LINKED_LEDGER)
    family = T.seed_party(admin_engine, tenant, owner, ledger=FAMILY_LEDGER)
    other = T.seed_party(admin_engine, tenant, owner, ledger=UNLINKED_LEDGER)
    with admin_engine.begin() as c:
        from sqlalchemy import text

        for party, amount in ((main, "15000.00"), (family, "1250.50"), (other, "7500.00")):
            c.execute(
                text("UPDATE ops.tally_parties SET closing_balance = :a WHERE id = :i"),
                {"a": Decimal(amount), "i": party},
            )
    T.seed_link(admin_engine, tenant, main, ids["s9a"], owner)
    T.seed_link(admin_engine, tenant, family, ids["s9a"], owner)
    T.set_flag(admin_engine, tenant, enabled=True)
    yield {**ids, "main": main, "family": family, "other": other}
    T.set_flag(admin_engine, tenant, enabled=False)
    composition.set_runtime(None)


def _names(transport: Any) -> set[str]:
    return {t["name"] for t in transport.sent[0]["tools"]}


def _result_text(transport: Any) -> str:
    return " ".join(r["content"][0]["text"] for r in transport.results)


def test_FR_TALLY_008_offered_only_to_finance_readers_with_the_flag_on(
    world: Any, api: Any, admin_engine: Engine, fees: dict[str, uuid.UUID]
) -> None:
    for role in ("owner", "principal", "accountant"):
        transport, _ = ask_with(api, world.person(role), "count_students", {})
        assert "get_fee_dues" in _names(transport), role
    for role in ("office_admin", "office_staff", "class_teacher", "teacher", "exam_coordinator"):
        transport, _ = ask_with(api, world.person(role), "count_students", {})
        assert "get_fee_dues" not in _names(transport), role
    T.set_flag(admin_engine, world.a.tenant_id, enabled=False)
    try:
        transport, _ = ask_with(api, world.person("accountant"), "count_students", {})
        assert "get_fee_dues" not in _names(transport)
    finally:
        T.set_flag(admin_engine, world.a.tenant_id, enabled=True)


def test_FR_TALLY_008_linked_ledgers_only_with_exact_figures_and_no_ledger_names(
    world: Any, api: Any, fees: dict[str, uuid.UUID]
) -> None:
    transport, events = ask_with(
        api, world.person("accountant"), "get_fee_dues", {"student_id": str(fees["s9a"])}
    )
    text = _result_text(transport)
    assert "₹16,250.50 in total" in text
    assert "₹15,000.00" in text
    assert "₹1,250.50" in text
    assert "as of 28/09/2026" in text
    sent = str(transport.sent)
    for ledger in (LINKED_LEDGER, FAMILY_LEDGER, UNLINKED_LEDGER):
        assert ledger not in sent
    assert "7,500.00" not in sent  # the unlinked ledger never reaches the model
    citations = [d["source"] for e, d in events if e == "citation"]
    assert len(citations) == 1
    assert citations[0].startswith("sos://fee/")


def test_FR_TALLY_008_an_unlinked_student_has_no_fee_figure_never_a_guess(
    world: Any, api: Any, fees: dict[str, uuid.UUID]
) -> None:
    transport, _ = ask_with(
        api, world.person("principal"), "get_fee_dues", {"student_id": str(fees["s9c"])}
    )
    text = _result_text(transport)
    assert "No Tally ledger is linked" in text
    assert "₹" not in text
    assert "7,500" not in str(transport.sent)


def test_FR_TALLY_008_school_totals_are_numbers_only(
    world: Any, api: Any, fees: dict[str, uuid.UUID]
) -> None:
    transport, _ = ask_with(api, world.person("owner"), "get_fee_dues", {})
    text = _result_text(transport)
    assert "school totals" in str(transport.results[0]["title"])
    assert "Synthetica" not in str(transport.sent[-1]["messages"][-1])
    # At least s9a's 16,250.50; the unlinked ledger is counted as unlinked, not as a due.
    assert "not linked to a student and not counted" in text


def test_SEC_001_another_schools_student_is_an_error_result(
    world: Any, api: Any, fees: dict[str, uuid.UUID]
) -> None:
    transport, _ = ask_with(
        api, world.person("accountant"), "get_fee_dues", {"student_id": str(fees["b_sb"])}
    )
    assert transport.error
    assert transport.results == []


def test_FR_TALLY_008_the_tool_rechecks_permission_and_flag_when_it_runs(
    world: Any, admin_engine: Engine, fees: dict[str, uuid.UUID]
) -> None:
    tool = GetFeeDuesTool(load_tools_config().tools["get_fee_dues"])
    accountant = K.SW.ctx_for(world.a.tenant_id, world.person("accountant"), "accountant")
    teacher = K.SW.ctx_for(world.a.tenant_id, world.person("office_admin"), "office_admin")
    args = {"student_id": str(fees["s9a"])}
    with tenant_session(world.a.tenant_id) as db:
        assert tool.run(db, teacher, "c1", args).is_error  # no finance.read
        assert not tool.run(db, accountant, "c2", args).is_error
        assert tool.run(db, accountant, "c3", {"student_id": "not-a-uuid"}).is_error
        assert tool.run(db, accountant, "c4", {"student": str(fees["s9a"])}).is_error
    T.set_flag(admin_engine, world.a.tenant_id, enabled=False)
    try:
        with tenant_session(world.a.tenant_id) as db:
            assert tool.run(db, accountant, "c5", args).is_error
            assert not tool.available(db, accountant)
    finally:
        T.set_flag(admin_engine, world.a.tenant_id, enabled=True)


@pytest.mark.parametrize(
    ("amount", "text"),
    [
        ("0", "₹0.00"),
        ("5", "₹5.00"),
        ("999.5", "₹999.50"),
        ("1000", "₹1,000.00"),
        ("150000.5", "₹1,50,000.50"),
        ("12345678.90", "₹1,23,45,678.90"),
        ("-200.50", "-₹200.50"),
    ],
)
def test_FR_TALLY_008_amounts_use_indian_grouping(amount: str, text: str) -> None:
    assert inr(Decimal(amount)) == text
