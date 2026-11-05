"""Bill-wise (term-wise) fee dues from Tally: the agent's pending bills per ledger are stored per
snapshot, shown per student with due dates and an overdue state, read by ``get_fee_dues``, purged
with their sync and isolated per school (lead decision D63; ADR-0032; FR-TALLY-005,
FR-TALLY-007, FR-TALLY-008; SEC-001, SEC-003). Synthetic data only.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from sqlalchemy import Engine, text

from app.core.db import tenant_session
from app.core.errors import NotFound
from app.core.subjects import Subject
from app.tally import agent_auth, service

from .conftest import T

pytestmark = pytest.mark.db

GROUPS = [("Sundry Debtors", "Current Assets"), ("Class IX Fees", "Sundry Debtors")]
PAST = dt.date(2026, 6, 15)
"""A due date long gone (the tests run after it)."""
FUTURE = dt.date(2099, 3, 31)


@pytest.fixture(autouse=True)
def fresh_stores() -> Iterator[None]:
    agent_auth.get_agent_stores.cache_clear()
    yield
    agent_auth.get_agent_stores.cache_clear()


def _ready(api: Any, admin: Engine) -> tuple[Any, Any]:
    school = T.fresh_school(admin)
    agent = T.enrol(api, school)
    assert T.catalog(api, agent, GROUPS).status_code == 200
    T.select(api, school, ["Sundry Debtors", "Class IX Fees"])
    return school, agent


def _bill(
    reference: str, pending: str, due: dt.date | None, *, date: dt.date | None = None
) -> dict[str, Any]:
    return {
        "reference": reference,
        "bill_date": (date or dt.date(2026, 4, 1)).isoformat(),
        "due_date": due.isoformat() if due else None,
        "pending": pending,
    }


def _with_bills(party: dict[str, Any], bills: list[dict[str, Any]]) -> dict[str, Any]:
    return {**party, "bills": bills}


def _bills(admin: Engine, tenant_id: uuid.UUID) -> list[tuple[str, str, str]]:
    with admin.connect() as c:
        return [
            (r[0], r[1], str(r[2]))
            for r in c.execute(
                text(
                    "SELECT p.ledger_name, b.reference, b.pending FROM ops.tally_bills b "
                    "JOIN ops.tally_parties p ON p.tenant_id = b.tenant_id AND p.id = b.party_id "
                    "WHERE b.tenant_id = :t ORDER BY 1, 2"
                ),
                {"t": tenant_id},
            )
        ]


def _link(api: Any, school: Any, pairs: list[tuple[str, uuid.UUID]]) -> None:
    accountant = school.people["accountant"]
    parties = api.call(accountant, "GET", "/api/v1/tally/parties").json()["data"]
    by_name = {p["ledger_name"]: p["id"] for p in parties}
    for ledger, student in pairs:
        res = api.call(
            accountant,
            "POST",
            f"/api/v1/tally/parties/{by_name[ledger]}/links",
            json={"student_id": str(student)},
        )
        assert res.status_code == 201, res.text


def test_FR_TALLY_005_bills_are_replaced_per_snapshot_and_a_repeat_adds_none(
    api: Any, admin_engine: Engine
) -> None:
    school, agent = _ready(api, admin_engine)
    batch = uuid.uuid4()
    first = [
        _with_bills(
            T.party("Synthetica Ravi 9A", "9000.00"),
            [_bill("Term 1/2026", "4000.00", PAST), _bill("Term 2/2026", "5000.00", FUTURE)],
        ),
        T.party("Synthetica Sita 9C", "300.00"),  # no bill-wise details: no bills
        _with_bills(T.party("Synthetica Old Ledger", "50.00"), [_bill("Bus", "50.00", PAST)]),
    ]
    assert T.snapshot(api, agent, first, batch_id=batch).status_code == 200
    assert _bills(admin_engine, school.tenant_id) == [
        ("Synthetica Old Ledger", "Bus", "50.00"),
        ("Synthetica Ravi 9A", "Term 1/2026", "4000.00"),
        ("Synthetica Ravi 9A", "Term 2/2026", "5000.00"),
    ]
    agent_auth.get_agent_stores.cache_clear()
    repeat = T.snapshot(api, agent, first, batch_id=batch)
    assert repeat.json()["repeat"] is True
    assert len(_bills(admin_engine, school.tenant_id)) == 3

    agent_auth.get_agent_stores.cache_clear()
    second = [
        _with_bills(
            T.party("Synthetica Ravi 9A", "5000.00"), [_bill("Term 2/2026", "5000.00", FUTURE)]
        ),
        T.party("Synthetica Sita 9C", "300.00"),
    ]
    res = T.snapshot(api, agent, second, as_of=dt.date(2026, 9, 29))
    assert res.status_code == 200, res.text
    # Ravi's bills replaced; the ledger missing from the snapshot keeps none.
    assert _bills(admin_engine, school.tenant_id) == [
        ("Synthetica Ravi 9A", "Term 2/2026", "5000.00")
    ]
    sync_id = res.json()["sync_id"]
    with admin_engine.connect() as c:
        assert c.execute(
            text("SELECT DISTINCT sync_id FROM ops.tally_bills WHERE tenant_id = :t"),
            {"t": school.tenant_id},
        ).scalar_one() == uuid.UUID(sync_id)


def test_FR_TALLY_005_bills_are_validated(api: Any, admin_engine: Engine) -> None:
    _, agent = _ready(api, admin_engine)
    too_many = _with_bills(
        T.party("Synthetica Many Bills", "1.00"),
        [_bill(f"B{i}", "0.01", None) for i in range(101)],
    )
    assert T.snapshot(api, agent, [too_many]).status_code == 422
    agent_auth.get_agent_stores.cache_clear()
    unknown_field = _with_bills(
        T.party("Synthetica Bad Bill", "1.00"), [{**_bill("B1", "1.00", None), "extra": 1}]
    )
    assert T.snapshot(api, agent, [unknown_field]).status_code == 422
    # (Bill references are cleaned like ledger names: tests/tally/test_agent_text.py.)


def test_FR_TALLY_007_dues_show_bills_with_due_dates_and_overdue_per_student(
    api: Any, admin_engine: Engine
) -> None:
    """Bills of a linked ledger are listed under the student with their due date and overdue
    state; the overdue total is per student; a family ledger's bills appear on each child,
    marked shared, while the totals still count the ledger's balance once (D54)."""
    school, agent = _ready(api, admin_engine)
    ravi = T.SW.create(school, name="Synthetica Ravi", section_key="section_9a", admission_no="B1")
    sita = T.SW.create(school, name="Synthetica Sita", section_key="section_9c", admission_no="B2")
    res = T.snapshot(
        api,
        agent,
        [
            _with_bills(
                T.party("Synthetica Ravi 9A", "9000.00"),
                [_bill("Term 2/2026", "5000.00", FUTURE), _bill("Term 1/2026", "4000.00", PAST)],
            ),
            _with_bills(
                T.party("Synthetica Family Ledger", "1000.00"),
                [_bill("Bus fee", "1000.00", PAST)],
            ),
            T.party("Synthetica Sita 9C", "300.00"),
        ],
    )
    assert res.status_code == 200, res.text
    _link(
        api,
        school,
        [
            ("Synthetica Ravi 9A", ravi),
            ("Synthetica Family Ledger", ravi),
            ("Synthetica Family Ledger", sita),
            ("Synthetica Sita 9C", sita),
        ],
    )
    dues = api.call(school.people["accountant"], "GET", "/api/v1/tally/dues").json()
    rows = {uuid.UUID(d["student_id"]): d for d in dues["data"]}
    r = rows[ravi]
    assert r["total_due"] == "10000.00"  # the ledgers' balances, unchanged
    assert [(b["reference"], b["due_date"], b["overdue"], b["shared"]) for b in r["bills"]] == [
        ("Bus fee", PAST.isoformat(), True, True),
        ("Term 1/2026", PAST.isoformat(), True, False),
        ("Term 2/2026", FUTURE.isoformat(), False, False),
    ]
    assert (r["overdue_due"], r["overdue_bills"]) == ("5000.00", 2)
    s = rows[sita]
    assert s["total_due"] == "1300.00"
    assert [(b["reference"], b["shared"]) for b in s["bills"]] == [("Bus fee", True)]
    assert (s["overdue_due"], s["overdue_bills"]) == ("1000.00", 1)
    # Totals: each ledger once (D54), bills change nothing.
    assert dues["totals"]["total_due"] == "10300.00"

    ctx = T.SW.ctx_for(school.tenant_id, school.people["accountant"], "accountant")
    with tenant_session(school.tenant_id, school.people["accountant"].user_id) as db:
        found = service.student_fee_dues(db, ctx, ravi)
        # The data-principal access report lists the bills of the linked ledgers too (DPDP).
        sections = service.subject_report(db, Subject("student", ravi))
    assert [s.title for s in sections] == [
        "Tally ledgers linked to the student",
        "Pending Tally bills of those ledgers",
    ]
    assert {row[1] for row in sections[1].rows} == {"Term 1/2026", "Term 2/2026", "Bus fee"}
    bills = {b.reference: b for ledger in found.ledgers for b in ledger.bills}
    assert set(bills) == {"Term 1/2026", "Term 2/2026", "Bus fee"}
    assert bills["Term 1/2026"].overdue
    assert not bills["Term 2/2026"].overdue
    assert str(found.total_due) == "10000.00"


def test_SEC_003_bills_are_for_finance_readers_and_stay_in_their_school(
    api: Any, admin_engine: Engine
) -> None:
    school, agent = _ready(api, admin_engine)
    ravi = T.SW.create(school, name="Synthetica Ravi", section_key="section_9a", admission_no="B3")
    T.snapshot(
        api,
        agent,
        [_with_bills(T.party("Synthetica Ravi 9A", "10.00"), [_bill("T1", "10.00", PAST)])],
    )
    _link(api, school, [("Synthetica Ravi 9A", ravi)])
    # A teacher (no finance.read) is refused the dues list.
    teacher = school.people["teacher"]
    assert api.call(teacher, "GET", "/api/v1/tally/dues").status_code == 403
    # Another school sees none of this school's bills (RLS) and cannot read the student's dues.
    other = T.fresh_school(admin_engine)
    with tenant_session(other.tenant_id) as db:
        assert db.execute(text("SELECT count(*) FROM ops.tally_bills")).scalar_one() == 0
    accountant = other.people["accountant"]
    dues = api.call(accountant, "GET", "/api/v1/tally/dues").json()
    assert dues["data"] == []
    ctx = T.SW.ctx_for(other.tenant_id, accountant, "accountant")
    with tenant_session(other.tenant_id, accountant.user_id) as db, pytest.raises(NotFound):
        service.student_fee_dues(db, ctx, ravi)


def test_FR_TALLY_005_bills_go_with_their_sync_record(api: Any, admin_engine: Engine) -> None:
    """Retention (``retention.sync_days``): a sync record the purge deletes takes its bills with
    it (FK cascade); the bills of the sync a ledger still points to stay."""
    school, agent = _ready(api, admin_engine)
    T.snapshot(
        api,
        agent,
        [_with_bills(T.party("Synthetica Keep", "10.00"), [_bill("T1", "10.00", PAST)])],
    )
    agent_auth.get_agent_stores.cache_clear()
    T.snapshot(
        api,
        agent,
        [_with_bills(T.party("Synthetica Keep", "20.00"), [_bill("T2", "20.00", PAST)])],
    )
    with admin_engine.begin() as c:
        old, current = (
            r[0]
            for r in c.execute(
                text(
                    "SELECT id FROM ops.tally_syncs WHERE tenant_id = :t ORDER BY received_at, id"
                ),
                {"t": school.tenant_id},
            )
        )
        party: uuid.UUID = c.execute(
            text("SELECT id FROM ops.tally_parties WHERE tenant_id = :t"), {"t": school.tenant_id}
        ).scalar_one()
        # A bill left on the old sync record (e.g. by an earlier release) goes with it.
        c.execute(
            text(
                "INSERT INTO ops.tally_bills (id, tenant_id, party_id, sync_id, reference, "
                "pending) VALUES (:i, :t, :p, :s, 'Old', 1.00)"
            ),
            {"i": uuid.uuid4(), "t": school.tenant_id, "p": party, "s": old},
        )
        c.execute(
            text(
                "UPDATE ops.tally_syncs SET received_at = now() - interval '500 days' "
                "WHERE tenant_id = :t"
            ),
            {"t": school.tenant_id},
        )
    with tenant_session(school.tenant_id) as db:
        assert service.purge_old_syncs(db) == 1
    assert _bills(admin_engine, school.tenant_id) == [("Synthetica Keep", "T2", "20.00")]
    with admin_engine.connect() as c:
        assert (
            c.execute(
                text("SELECT sync_id FROM ops.tally_bills WHERE tenant_id = :t"),
                {"t": school.tenant_id},
            ).scalar_one()
            == current
        )
