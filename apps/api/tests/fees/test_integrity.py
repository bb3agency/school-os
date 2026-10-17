"""Money integrity (docs/14 R2 test plan): gap-free receipt numbers under concurrency, the
append-only ledger, frozen receipts, allocation sums, maker-checker CHECKs, UTR uniqueness under
concurrency, narrowed grants and tenant isolation (FR-FEE-006, FR-FEE-010..014; invariant 1)."""

from __future__ import annotations

import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError, ProgrammingError

from app.core.db import tenant_session
from app.core.errors import Conflict
from app.fees import repository as repo
from app.fees import service as fees
from app.fees.schemas import CancellationRequest, DecisionIn

pytestmark = pytest.mark.db

F: Any = __import__("sys").modules["sos_test_fees_support"]


def _numbers(admin: Engine, tenant_id: uuid.UUID, fy: str) -> list[int]:
    with admin.connect() as c:
        return [
            int(row[0])
            for row in c.execute(
                text(
                    "SELECT receipt_no FROM fin.receipts WHERE tenant_id = :t "
                    "AND financial_year = :f ORDER BY receipt_no"
                ),
                {"t": tenant_id, "f": fy},
            ).all()
        ]


def test_FR_FEE_010_concurrent_receipts_are_gap_free_and_unique(
    school: Any, admin_engine: Engine
) -> None:
    accounts = [F.account(school) for _ in range(8)]
    start = threading.Barrier(len(accounts))

    def one(account_id: uuid.UUID) -> str:
        start.wait()
        out = F.issue(school, account_id, "10.00")
        return str(out.receipt_number)

    with ThreadPoolExecutor(max_workers=len(accounts)) as pool:
        numbers = list(pool.map(one, accounts))
    assert len(set(numbers)) == len(numbers)
    fy = numbers[0].split("/")[1]
    all_numbers = _numbers(admin_engine, school.tenant_id, fy)
    assert all_numbers == list(range(1, len(all_numbers) + 1)), "gap-free and never reused"


def test_FR_FEE_010_a_failed_save_leaves_no_gap(school: Any, admin_engine: Engine) -> None:
    account_id = F.account(school)
    first = F.issue(school, account_id, "10.00")
    fy = first.financial_year

    class Boom(Exception):
        pass

    def fail_after_allocating() -> None:
        with tenant_session(school.tenant_id) as s:
            repo.allocate_number(s, "RCT", fy)
            raise Boom

    with pytest.raises(Boom):
        fail_after_allocating()
    second = F.issue(school, account_id, "10.00")
    n1 = int(first.receipt_number.rsplit("/", 1)[1])
    n2 = int(second.receipt_number.rsplit("/", 1)[1])
    assert n2 > n1
    assert _numbers(admin_engine, school.tenant_id, fy) == list(range(1, n2 + 1))


def test_FR_FEE_014_same_utr_twice_at_once_gives_one_receipt(school: Any) -> None:
    a, b = F.account(school), F.account(school)
    utr = F.utr()
    start = threading.Barrier(2)

    def one(account_id: uuid.UUID) -> str:
        start.wait()
        try:
            F.issue(school, account_id, "10.00", mode="upi_qr", utr=utr)
        except Conflict as exc:
            return str(exc.code)
        return "ok"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = sorted(pool.map(one, [a, b]))
    assert results == ["ok", "utr_in_use"]


# --- triggers and CHECKs (as the test superuser: grants do not stop it, triggers do) ------------


def _constraint(exc: DBAPIError) -> str:
    return str(getattr(getattr(exc.orig, "diag", None), "constraint_name", "") or "")


def _first_receipt(admin: Engine, tenant_id: uuid.UUID) -> uuid.UUID:
    with admin.connect() as c:
        value: object = c.execute(
            text("SELECT id FROM fin.receipts WHERE tenant_id = :t AND status = 'issued' LIMIT 1"),
            {"t": tenant_id},
        ).scalar_one()
    return uuid.UUID(str(value))


@pytest.mark.parametrize(
    ("statement", "constraint"),
    [
        (
            "UPDATE fin.ledger_entries SET amount = amount WHERE tenant_id = :t",
            "ledger_entries_append_only",
        ),
        ("DELETE FROM fin.ledger_entries WHERE tenant_id = :t", "ledger_entries_append_only"),
        ("UPDATE fin.receipts SET amount = amount + 1 WHERE tenant_id = :t", "receipts_frozen"),
        ("UPDATE fin.receipts SET content = '{}'::jsonb WHERE tenant_id = :t", "receipts_frozen"),
        ("DELETE FROM fin.receipts WHERE tenant_id = :t", "receipts_append_only"),
        (
            "DELETE FROM fin.receipt_allocations WHERE tenant_id = :t",
            "receipt_allocations_append_only",
        ),
        (
            "UPDATE fin.receipt_counters SET last_no = 0 WHERE tenant_id = :t",
            "receipt_counters_never_down",
        ),
        (
            "DELETE FROM fin.student_fee_accounts WHERE tenant_id = :t",
            "student_fee_accounts_append_only",
        ),
    ],
)
def test_FR_FEE_006_ledger_and_receipts_cannot_be_rewritten(
    school: Any, admin_engine: Engine, statement: str, constraint: str
) -> None:
    F.issue(school, F.account(school), "10.00")
    with pytest.raises(DBAPIError) as err, admin_engine.begin() as c:
        c.execute(text(statement), {"t": school.tenant_id})
    assert _constraint(err.value) == constraint


def test_FR_FEE_011_receipt_status_only_moves_to_cancelled(
    school: Any, admin_engine: Engine
) -> None:
    rid = _first_receipt(admin_engine, school.tenant_id)
    with pytest.raises(DBAPIError) as err, admin_engine.begin() as c:
        c.execute(text("UPDATE fin.receipts SET status = 'void' WHERE id = :i"), {"i": rid})
    assert _constraint(err.value) in ("receipts_frozen", "receipts_status_check")


def test_FR_FEE_006_allocations_must_add_up_at_commit(school: Any, admin_engine: Engine) -> None:
    account_id = F.account(school)
    with pytest.raises(DBAPIError) as err, admin_engine.begin() as c:
        c.execute(
            text(
                "INSERT INTO fin.receipts (id, tenant_id, account_id, financial_year, receipt_no, "
                "receipt_number, receipt_date, amount, mode, collected_by, content, "
                "content_sha256) "
                "VALUES (:i, :t, :a, '2026-27', 999999, 'RCT/2026-27/999999', current_date, 10, "
                "'cash', :m, '{}'::jsonb, decode(repeat('00', 32), 'hex'))"
            ),
            {
                "i": uuid.uuid4(),
                "t": school.tenant_id,
                "a": account_id,
                "m": school.people["office_staff"].membership_id,
            },
        )
    assert _constraint(err.value) == "receipts_allocations_sum"


@pytest.mark.parametrize(
    ("table", "constraint"),
    [
        ("fin.receipt_cancellations", "receipt_cancellations_maker_checker"),
        ("fin.concessions", "concessions_maker_checker"),
        ("fin.adjustments", "adjustments_maker_checker"),
        ("fin.refunds", "refunds_maker_checker"),
    ],
)
def test_ADR_0010_db_check_refuses_self_approval(
    school: Any, admin_engine: Engine, table: str, constraint: str
) -> None:
    assert (
        F.scalar(
            admin_engine,
            "SELECT count(*) FROM pg_constraint WHERE conname = :n AND contype = 'c'",
            n=constraint,
        )
        == 1
    )
    if table == "fin.receipt_cancellations":
        r = F.issue(school, F.account(school), "10.00", role="accountant")
        req = F.call(
            school,
            "accountant",
            fees.request_cancellation,
            r.id,
            CancellationRequest(reason_code="other", reason="Testing the database rule"),
        )
        with pytest.raises(DBAPIError) as err, admin_engine.begin() as c:
            c.execute(
                text(
                    "UPDATE fin.receipt_cancellations SET status = 'approved', decided_by = "
                    "requested_by, decided_at = now(), cancelled_on = current_date WHERE id = :i"
                ),
                {"i": req.id},
            )
        assert _constraint(err.value) == constraint


def test_FR_FEE_012_decided_request_never_changes(school: Any, admin_engine: Engine) -> None:
    r = F.issue(school, F.account(school), "10.00", role="accountant")
    req = F.call(
        school,
        "accountant",
        fees.request_cancellation,
        r.id,
        CancellationRequest(reason_code="other", reason="Testing the frozen request"),
    )
    F.call(
        school, "principal", fees.approve_cancellation, req.id, DecisionIn(), expected_version=None
    )
    with pytest.raises(DBAPIError) as err, admin_engine.begin() as c:
        c.execute(
            text(
                "UPDATE fin.receipt_cancellations SET reason = 'Something else entirely' "
                "WHERE id = :i"
            ),
            {"i": req.id},
        )
    assert _constraint(err.value) == "fee_requests_frozen"


# --- grants (SEC-002 pattern) --------------------------------------------------------------------


def _privileges(admin: Engine, table: str) -> set[str]:
    schema, name = table.split(".")
    with admin.connect() as c:
        return set(
            c.execute(
                text(
                    "SELECT privilege_type FROM information_schema.role_table_grants "
                    "WHERE table_schema = :s AND table_name = :n AND grantee = 'sos_app'"
                ),
                {"s": schema, "n": name},
            ).scalars()
        )


@pytest.mark.parametrize(
    "table", ["fin.ledger_entries", "fin.receipt_allocations", "fin.day_closes"]
)
def test_FR_FEE_006_app_role_may_only_read_and_append(admin_engine: Engine, table: str) -> None:
    assert _privileges(admin_engine, table) == {"SELECT", "INSERT"}


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE fin.receipts SET amount = amount WHERE false",
        "UPDATE fin.receipts SET content = content WHERE false",
        "UPDATE fin.ledger_entries SET amount = amount WHERE false",
        "DELETE FROM fin.receipts WHERE false",
        "UPDATE fin.concessions SET requested_by = requested_by WHERE false",
    ],
)
def test_FR_FEE_006_app_role_is_refused_outside_its_columns(
    app_engine: Engine, statement: str
) -> None:
    with app_engine.connect() as c:
        c.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(uuid.uuid4())})
        with pytest.raises(ProgrammingError, match="permission denied"):
            c.execute(text(statement))
        c.rollback()


def test_SEC_001_other_school_sees_no_fee_rows(school: Any, world: Any) -> None:
    F.issue(school, F.account(school), "10.00")
    with tenant_session(world.b.tenant_id) as s:
        for table in ("fin.receipts", "fin.ledger_entries", "fin.student_fee_accounts"):
            assert s.execute(text(f"SELECT count(*) FROM {table}")).scalar_one() == 0


def test_money_columns_are_numeric_14_2(admin_engine: Engine) -> None:
    with admin_engine.connect() as c:
        rows = c.execute(
            text(
                "SELECT table_name, column_name, numeric_precision, numeric_scale FROM "
                "information_schema.columns WHERE table_schema = 'fin' AND data_type = 'numeric'"
            )
        ).all()
    assert rows
    assert {(r[2], r[3]) for r in rows} == {(14, 2)}
    assert Decimal("0.01")
