"""FM-08 bank statement matching for cheques and bank transfers; cheque clearing status
(FR-FEE-126..150; ADR-0042 decision 9, P9; owner decision D19; invariants 1, 2, 3, 5, 6, 7).

Synthetic statements only (invariant 11): made-up payer names, account numbers and references.
A rule only proposes and a person confirms; a returned cheque goes through the cancellation
maker-checker and the ledger is never edited here.
"""

from __future__ import annotations

import datetime as dt
import secrets
import uuid
from typing import Any

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError

from app.core.errors import Conflict, Forbidden, NotFound
from app.fees import service as fees
from app.fees.bank_matching_config import bank_facts, load_bank_matching_config
from app.fees.bank_matching_schemas import ChequeDepositIn, ChequeReturnIn
from app.fees.upi_schemas import ConfirmIn, MatchIn, StatementImportIn

F: Any = __import__("sys").modules["sos_test_fees_support"]
U: Any = __import__("sys").modules["sos_test_fees_upi_support"]

ACCOUNT_NO = "50100987654321"  # made up; must never be stored
PAYER = "Synthetica Ramapuram"


def _amount() -> str:
    return f"{2000 + secrets.randbelow(5000)}.{secrets.randbelow(100):02d}"


def _cheque_no() -> str:
    return f"{100000 + secrets.randbelow(899999):06d}"


def _neft_ref() -> str:
    return f"SBIN4{secrets.randbelow(10**11):011d}"


# --- pure extraction (FR-FEE-126) -----------------------------------------------------------------

CFG = load_bank_matching_config()


@pytest.mark.parametrize(
    ("narration", "reference", "upi", "expected"),
    [
        ("CLG CHQ 123456 SYNTHETIC BANK", "", False, ("123456", None)),
        ("BY CLG INWARD SYNTHETIC BANK", "000000654321", False, ("654321", None)),
        ("CHEQUE DEPOSIT", "234567", False, ("234567", None)),
        ("CTS-CHQ NO-0345678 SYNTHETIC", "", False, ("345678", None)),
        (f"NEFT/SBIN426123456789/{PAYER.upper()}/FEES", "", False, (None, "SBIN426123456789")),
        ("NEFT CR-SYNB0000001-SYNTHETICA-FEES", "SYNBN26291123456", False, (None, None)),
        ("IMPS/P2A/612345678901/SYNTHETICA/FEES", "", False, (None, "612345678901")),
        (f"NEFT/{ACCOUNT_NO}/SYNTHETICA", "", False, (None, None)),  # all-digit 14: account no.
        ("TRANSFER FROM 50100123456789", "", False, (None, None)),
        ("UPI/629112345678/SCHOOL FEES CHQ 123456", "", True, (None, None)),
    ],
)
def test_FR_FEE_126_cheque_number_and_transfer_reference_are_read(
    narration: str, reference: str, upi: bool, expected: tuple[str | None, str | None]
) -> None:
    assert bank_facts(narration, reference, is_upi=upi, cfg=CFG) == expected


def test_FR_FEE_126_config_is_marked_unverified() -> None:
    assert CFG.cheques.verified is False
    assert CFG.transfers.verified is False


# --- statements with cheque and transfer credits -------------------------------------------------


def _statement(rows: list[tuple[dt.date, str, str, str]]) -> bytes:
    table: list[list[Any]] = [
        ["Account Name", "SYNTHETIC PUBLIC SCHOOL"],
        ["Account Number", ACCOUNT_NO],
        [],
        ["Date", "Description", "Reference", "Debit", "Credit", "Balance"],
    ]
    for day, narration, ref, amount in rows:
        table.append([day.strftime("%d/%m/%Y"), narration, ref, "", amount, "100000.00"])
    data: bytes = U.csv_bytes(table)
    return data


def _import(school: Any, admin: Engine, rows: list[tuple[dt.date, str, str, str]]) -> Any:
    doc = U.upload(admin, school, _statement(rows))
    ctx = F.ctx(school, school.people["accountant"], "accountant")
    return fees.import_statement(ctx, StatementImportIn(document_id=doc, preset="generic"))


def _cheque(school: Any, amount: str, number: str) -> Any:
    return F.issue(
        school,
        F.account(school),
        amount,
        mode="cheque",
        mode_details={
            "cheque_number": number,
            "bank_name": "Synthetic Co-operative Bank",
            "cheque_date": U.today().isoformat(),
        },
    )


def _transfer(school: Any, amount: str, ref: str) -> Any:
    return F.issue(
        school, F.account(school), amount, mode="bank_transfer", mode_details={"reference": ref}
    )


def _lines(school: Any, import_id: uuid.UUID) -> list[Any]:
    return list(F.call(school, "accountant", fees.get_statement, import_id).lines)


def _audits(admin: Engine, tenant_id: uuid.UUID, action: str) -> list[dict[str, Any]]:
    with admin.connect() as c:
        rows = c.execute(
            text(
                "SELECT summary FROM audit.events WHERE tenant_id = :t AND action = :a "
                "ORDER BY occurred_at"
            ),
            {"t": tenant_id, "a": action},
        ).all()
    return [dict(r[0]) for r in rows]


@pytest.mark.db
def test_FR_FEE_127_cheque_number_and_amount_are_proposed_and_confirmed_by_a_person(
    school: Any, admin_engine: Engine
) -> None:
    amount, number = _amount(), _cheque_no()
    receipt = _cheque(school, amount, number)
    out = _import(school, admin_engine, [(U.today(), f"CLG CHQ {number} SYNTH BANK", "", amount)])
    [line] = _lines(school, out.id)
    assert line.cheque_no == number
    assert line.match_status == "proposed"
    assert line.match_kind == "cheque_exact"
    assert line.receipt is not None
    assert line.receipt.id == receipt.id
    assert line.receipt.mode == "cheque"
    assert line.receipt.cheque_number == number
    # Nothing is confirmed (or posted) until a person confirms.
    before = F.call(school, "accountant", fees.get_receipt, receipt.id)
    assert before.bank_confirmed_at is None
    assert before.cheque_status == "received"
    done = F.call(
        school, "accountant", fees.confirm_statement, out.id, ConfirmIn(line_ids=[line.id])
    )
    assert done.confirmed == 1
    after = F.call(school, "accountant", fees.get_receipt, receipt.id)
    assert after.bank_confirmed_at is not None
    assert after.cheque_status == "cleared"
    assert after.status == "issued"
    assert after.content_sha256 == before.content_sha256
    listed = F.call(school, "accountant", fees.list_cheques)
    item = next(x for x in listed.items if x.receipt_id == receipt.id)
    assert item.cheque_status == "cleared"
    assert item.cleared_from_statement
    assert item.cleared_on == U.today()


@pytest.mark.db
def test_FR_FEE_127_transfer_reference_and_amount_are_proposed(
    school: Any, admin_engine: Engine
) -> None:
    amount, ref = _amount(), _neft_ref()
    receipt = _transfer(school, amount, ref)
    narration = f"NEFT/{ref}/{PAYER.upper()}/FEES"
    out = _import(school, admin_engine, [(U.today(), narration, "", amount)])
    [line] = _lines(school, out.id)
    assert line.transfer_ref == ref
    assert line.match_kind == "transfer_exact"
    assert line.receipt is not None
    assert line.receipt.id == receipt.id
    F.call(school, "accountant", fees.confirm_statement, out.id, ConfirmIn(line_ids=[line.id]))
    got = F.call(school, "accountant", fees.get_receipt, receipt.id)
    assert got.bank_confirmed_at is not None
    assert got.cheque_status is None
    # The narration is stored masked: no account number, no payer UPI ID, no raw long numbers.
    stored = F.call(school, "accountant", fees.get_statement, out.id).lines[0].narration
    assert ACCOUNT_NO not in (stored or "")


@pytest.mark.db
def test_FR_FEE_127_same_amount_different_cheque_number_is_never_matched(
    school: Any, admin_engine: Engine
) -> None:
    amount = _amount()
    receipt = _cheque(school, amount, _cheque_no())
    other_no = _cheque_no()
    out = _import(school, admin_engine, [(U.today(), f"CLG CHQ {other_no}", "", amount)])
    [line] = _lines(school, out.id)
    assert line.match_status == "unmatched"
    with pytest.raises(Conflict) as err:
        F.call(school, "accountant", fees.match_line, line.id, MatchIn(receipt_id=receipt.id))
    assert err.value.code == "cheque_number_differs"


@pytest.mark.db
def test_FR_FEE_128_a_person_matches_a_transfer_with_another_reference(
    school: Any, admin_engine: Engine
) -> None:
    amount = _amount()
    receipt = _transfer(school, amount, _neft_ref())
    out = _import(school, admin_engine, [(U.today(), f"NEFT/{_neft_ref()}/SYN", "", amount)])
    [line] = _lines(school, out.id)
    assert line.match_status == "unmatched"
    got = F.call(school, "accountant", fees.match_line, line.id, MatchIn(receipt_id=receipt.id))
    assert got.match_status == "confirmed"
    assert got.match_kind == "manual"


@pytest.mark.db
def test_FR_FEE_132_deposit_then_return_raises_a_cheque_dishonoured_cancellation(
    school: Any, admin_engine: Engine
) -> None:
    receipt = _cheque(school, _amount(), _cheque_no())
    cash = F.issue(school, F.account(school), "100.00")
    today = U.today()
    with pytest.raises(Conflict) as err:
        F.call(
            school,
            "accountant",
            fees.mark_cheque_returned,
            receipt.id,
            ChequeReturnIn(returned_on=today, reason="Returned by the bank"),
        )
    assert err.value.code == "cheque_not_deposited"
    dep = F.call(
        school,
        "office_admin",
        fees.mark_cheques_deposited,
        ChequeDepositIn(receipt_ids=[receipt.id, cash.id, receipt.id], deposited_on=today),
    )
    assert dep.deposited == 1
    assert [s.code for s in dep.skipped] == ["not_a_cheque"]
    again = F.call(
        school,
        "office_admin",
        fees.mark_cheques_deposited,
        ChequeDepositIn(receipt_ids=[receipt.id], deposited_on=today),
    )
    assert again.already_deposited == 1
    assert again.deposited == 0
    out = F.call(
        school,
        "accountant",
        fees.mark_cheque_returned,
        receipt.id,
        ChequeReturnIn(returned_on=today, reason="Returned by the bank"),
    )
    assert out.cheque.cheque_status == "returned"
    assert out.cancellation.reason_code == "cheque_dishonoured"
    assert out.cancellation.status == "pending"
    assert out.cheque.cancellation_id == out.cancellation.id
    # The ledger is untouched until a checker approves the cancellation.
    got = F.call(school, "accountant", fees.get_receipt, receipt.id)
    assert got.status == "issued"
    assert got.cheque_status == "returned"
    summaries = _audits(admin_engine, school.tenant_id, "fee.cheque.returned")
    assert any(s.get("cancellation_id") == str(out.cancellation.id) for s in summaries)
    assert _audits(admin_engine, school.tenant_id, "fee.cheque.deposited")
    with pytest.raises(Conflict) as twice:
        F.call(
            school,
            "accountant",
            fees.mark_cheque_returned,
            receipt.id,
            ChequeReturnIn(returned_on=today, reason="Returned by the bank"),
        )
    assert twice.value.code == "cheque_already_returned"


@pytest.mark.db
def test_FR_FEE_133_a_returned_cheque_is_not_confirmed_from_the_statement(
    school: Any, admin_engine: Engine
) -> None:
    amount, number = _amount(), _cheque_no()
    receipt = _cheque(school, amount, number)
    today = U.today()
    F.call(
        school,
        "accountant",
        fees.mark_cheques_deposited,
        ChequeDepositIn(receipt_ids=[receipt.id], deposited_on=today),
    )
    F.call(
        school,
        "accountant",
        fees.mark_cheque_returned,
        receipt.id,
        ChequeReturnIn(returned_on=today, reason="Returned by the bank"),
    )
    out = _import(school, admin_engine, [(today, f"CLG CHQ {number}", "", amount)])
    [line] = _lines(school, out.id)
    assert line.match_status == "unmatched"
    with pytest.raises(Conflict) as err:
        F.call(school, "accountant", fees.match_line, line.id, MatchIn(receipt_id=receipt.id))
    assert err.value.code == "cheque_returned"


@pytest.mark.db
def test_FR_FEE_131_cheque_routes_need_fee_permissions(school: Any) -> None:
    receipt = _cheque(school, _amount(), _cheque_no())
    for role in ("class_teacher", "teacher", "auditor_readonly"):
        with pytest.raises(Forbidden):
            F.call(school, role, fees.list_cheques)
    with pytest.raises(Forbidden):  # office_admin collects but does not reconcile
        F.call(
            school,
            "office_admin",
            fees.mark_cheque_returned,
            receipt.id,
            ChequeReturnIn(returned_on=U.today(), reason="Returned"),
        )
    with pytest.raises(NotFound):
        F.call(
            school,
            "accountant",
            fees.mark_cheque_returned,
            uuid.uuid4(),
            ChequeReturnIn(returned_on=U.today(), reason="Returned"),
        )


@pytest.mark.db
def test_FR_FEE_136_deposit_slip_lists_cheques_without_student_names(
    school: Any, admin_engine: Engine
) -> None:
    number = _cheque_no()
    receipt = _cheque(school, _amount(), number)
    F.call(
        school,
        "office_admin",
        fees.mark_cheques_deposited,
        ChequeDepositIn(receipt_ids=[receipt.id], deposited_on=U.today()),
    )
    page = F.call(school, "office_admin", fees.cheque_deposit_slip_page, None)
    assert number in page
    assert receipt.receipt_number in page
    assert receipt.student_name not in page
    assert "<script" not in page
    assert _audits(admin_engine, school.tenant_id, "fee.cheque.deposit_slip_viewed")


@pytest.mark.db
def test_FR_FEE_135_database_keeps_cheque_events_append_only_and_ordered(
    school: Any, admin_engine: Engine
) -> None:
    cash = F.issue(school, F.account(school), "100.00")
    receipt = _cheque(school, _amount(), _cheque_no())
    member = school.people["accountant"].membership_id

    def insert(rid: uuid.UUID, status: str) -> None:
        with admin_engine.begin() as c:
            c.execute(
                text(
                    "INSERT INTO fin.cheque_status_events (id, tenant_id, receipt_id, status, "
                    "event_date, source, recorded_by, cancellation_id) VALUES (gen_random_uuid(), "
                    ":t, :r, :s, current_date, 'manual', :m, NULL)"
                ),
                {"t": school.tenant_id, "r": rid, "s": status, "m": member},
            )

    with pytest.raises(DBAPIError, match=r"cheque_status_events_not_cheque|only a cheque"):
        insert(cash.id, "deposited")
    with pytest.raises(DBAPIError):
        insert(receipt.id, "returned")  # no cancellation and not deposited
    insert(receipt.id, "deposited")
    with pytest.raises(DBAPIError):
        insert(receipt.id, "deposited")
    update = (
        "UPDATE fin.cheque_status_events SET event_date = current_date - 1 WHERE receipt_id = :r"
    )
    with pytest.raises(DBAPIError), admin_engine.begin() as c:
        c.execute(text(update), {"r": receipt.id})


@pytest.mark.db
def test_INV_1_cheque_events_are_invisible_to_another_school(
    school: Any, other_school: Any, admin_engine: Engine
) -> None:
    receipt = _cheque(school, _amount(), _cheque_no())
    F.call(
        school,
        "accountant",
        fees.mark_cheques_deposited,
        ChequeDepositIn(receipt_ids=[receipt.id], deposited_on=U.today()),
    )
    listed = F.call(other_school, "accountant", fees.list_cheques)
    assert all(x.receipt_id != receipt.id for x in listed.items)
    with pytest.raises(NotFound):
        F.call(
            other_school,
            "accountant",
            fees.mark_cheque_returned,
            receipt.id,
            ChequeReturnIn(returned_on=U.today(), reason="Returned"),
        )
    dep = F.call(
        other_school,
        "accountant",
        fees.mark_cheques_deposited,
        ChequeDepositIn(receipt_ids=[receipt.id], deposited_on=U.today()),
    )
    assert [s.code for s in dep.skipped] == ["not_found"]


# --- HTTP -----------------------------------------------------------------------------------------


@pytest.mark.db
def test_FR_FEE_131_cheque_routes_over_http(
    school: Any, other_school: Any, api: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    receipt = _cheque(school, _amount(), _cheque_no())
    accountant, admin_ = school.people["accountant"], school.people["office_admin"]
    capsys.readouterr()
    ok = api.call(admin_, "GET", "/api/v1/fees/cheques")
    assert ok.status_code == 200, ok.text
    assert any(x["receipt_id"] == str(receipt.id) for x in ok.json()["items"])
    assert api.call(school.people["teacher"], "GET", "/api/v1/fees/cheques").status_code == 403
    dep = api.call(
        admin_,
        "POST",
        "/api/v1/fees/cheques/deposited",
        json={"receipt_ids": [str(receipt.id)], "deposited_on": U.today().isoformat()},
    )
    assert dep.status_code == 200, dep.text
    slip = api.call(admin_, "GET", "/api/v1/fees/cheques/deposit-slip/print")
    assert slip.status_code == 200
    assert slip.headers["content-type"].startswith("text/html")
    assert "default-src 'none'" in slip.headers["content-security-policy"]
    path = f"/api/v1/fees/cheques/{receipt.id}/returned"
    body = {"returned_on": U.today().isoformat(), "reason": "Returned by the bank"}
    assert api.call(admin_, "POST", path, json=body).status_code == 403
    assert api.call(other_school.people["accountant"], "POST", path, json=body).status_code == 404
    res = api.call(accountant, "POST", path, json=body)
    assert res.status_code == 200, res.text
    assert res.json()["cancellation"]["reason_code"] == "cheque_dishonoured"
    out = capsys.readouterr()
    assert receipt.student_name not in out.out + out.err
