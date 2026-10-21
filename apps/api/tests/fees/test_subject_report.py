"""Data-principal access report: the fees contribution (R3-1A; FR-DPDP-009, ADR-0048
decision 6, PRV-031). Synthetic data only (invariant 11)."""

from __future__ import annotations

import datetime as dt
import secrets
import uuid
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
import yaml
from sqlalchemy import Engine

from app.core.db import tenant_session
from app.core.subjects import ReportSection, Subject
from app.fees import service as fees
from app.fees.schemas import (
    CancellationRequest,
    ConcessionCreate,
    ConcessionTarget,
    DecisionIn,
    RefundCreate,
)
from app.fees.upi_schemas import ConfirmIn, StatementImportIn

pytestmark = pytest.mark.db

F: Any = __import__("sys").modules["sos_test_fees_support"]
U: Any = __import__("sys").modules["sos_test_fees_upi_support"]


def _classes() -> set[str]:
    path = Path(fees.__file__).parents[1] / "privacy" / "subjects.yaml"
    raw = yaml.safe_load(path.read_text("utf-8"))
    return set(raw["contributors"]["fees"]["classes"])


def _report(school: Any, subject: Subject) -> list[ReportSection]:
    with tenant_session(school.tenant_id, school.people["owner"].user_id) as s:
        return fees.subject_report(s, subject)


def _by_title(sections: list[ReportSection]) -> dict[str, ReportSection]:
    return {s.title: s for s in sections}


def _amount() -> str:
    return f"{1000 + secrets.randbelow(5000)}.{secrets.randbelow(100):02d}"


@pytest.fixture(scope="module")
def setup(school: Any, admin_engine: Engine) -> dict[str, Any]:
    F.ensure_setup(school)
    student = F.student(school)
    account_id = F.account(school, student)
    other_student = F.student(school)
    other_account = F.account(school, other_student)

    cash = F.issue(school, account_id, "1000.00", role="accountant")
    req = F.call(
        school,
        "accountant",
        fees.request_cancellation,
        cash.id,
        CancellationRequest(reason_code="wrong_amount", reason=F.REASON),
    )
    F.call(
        school, "principal", fees.approve_cancellation, req.id, DecisionIn(), expected_version=None
    )
    amount = _amount()
    upi = F.issue(school, account_id, amount, mode="upi_qr", utr=F.utr())
    refund = F.call(
        school,
        "accountant",
        fees.request_refund,
        upi.id,
        RefundCreate(amount=Decimal("300.00"), mode="cash", reason=F.REASON),
    )
    F.call(school, "principal", fees.approve_refund, refund.id, DecisionIn(), expected_version=None)
    ids = F.ensure_setup(school)
    F.call(
        school,
        "office_admin",
        fees.request_concession,
        account_id,
        ConcessionCreate(
            concession_type="sibling",
            basis="percent",
            value=Decimal("10"),
            targets=[ConcessionTarget(instalment_no=2, head_id=ids.tui)],
            reason=F.REASON,
        ),
    )
    other_receipt = F.issue(school, other_account, "777.00", role="accountant")
    today: dt.date = U.today()
    doc = U.upload(admin_engine, school, U.statement("generic", [U.Credit(today, amount, upi.utr)]))
    ctx = F.ctx(school, school.people["accountant"], "accountant")
    out = fees.import_statement(ctx, StatementImportIn(document_id=doc))
    # A proposed match may belong to another payer: not the student's data until confirmed.
    proposed = _by_title(_report(school, Subject("student", student)))
    assert "Bank statement lines linked to the student's receipts" not in proposed
    line_id = F.scalar(
        admin_engine,
        "SELECT id FROM fin.bank_statement_lines WHERE receipt_id = :r",
        r=upi.id,
    )
    F.call(school, "accountant", fees.confirm_statement, out.id, ConfirmIn(line_ids=[line_id]))
    return {
        "student": student,
        "account": account_id,
        "cash": cash,
        "upi": upi,
        "other_student": other_student,
        "other_receipt": other_receipt,
    }


def test_FR_DPDP_009_fees_report_has_account_receipts_concessions_refunds(
    school: Any, setup: dict[str, Any]
) -> None:
    sections = _report(school, Subject("student", setup["student"]))

    assert {s.class_key for s in sections} <= _classes()
    by = _by_title(sections)
    assert set(by) == {
        "Fee accounts",
        "Fee receipts",
        "Fee concessions",
        "Fee refunds",
        "Bank statement lines linked to the student's receipts",
    }
    for title in ("Fee accounts", "Fee receipts", "Fee concessions", "Fee refunds"):
        assert by[title].class_key == "financial_records"
    (account,) = by["Fee accounts"].rows
    assert account[0] == "2026-27"
    assert account[1] == "Class IX 2026-27"
    assert account[3] == "₹9,000.00"
    receipts = by["Fee receipts"].rows
    assert [r[0] for r in receipts] == [setup["upi"].receipt_number, setup["cash"].receipt_number]
    assert [(r[3], r[4]) for r in receipts] == [("UPI", "Issued"), ("Cash", "Cancelled")]
    assert by["Fee concessions"].rows == (
        ("Sibling", "10%", "—", by["Fee concessions"].rows[0][3], "Pending"),
    )
    (refund,) = by["Fee refunds"].rows
    assert (refund[2], refund[3], refund[4]) == ("₹300.00", "Cash", "Processed")
    bank = by["Bank statement lines linked to the student's receipts"]
    assert bank.class_key == "bank_statements"
    assert bank.rows == (("1",),)

    flat = " ".join(" ".join(" ".join(r) for r in s.rows) for s in sections)
    assert setup["other_receipt"].receipt_number not in flat
    assert setup["upi"].utr not in flat
    assert F.REASON not in flat


def test_FR_DPDP_009_fees_report_empty_for_guardian_member_and_unrelated(
    school: Any, setup: dict[str, Any]
) -> None:
    assert _report(school, Subject("student", uuid.uuid4())) == []
    assert _report(school, Subject("guardian", setup["student"])) == []
    assert _report(school, Subject("member", setup["student"])) == []
    assert _report(school, Subject("student", F.student(school))) == []


def test_FR_DPDP_009_fee_targets_are_the_students_own(school: Any, setup: dict[str, Any]) -> None:
    with tenant_session(school.tenant_id) as s:
        accounts, receipts = fees.fee_targets_of_student(s, setup["student"])
    assert accounts == [setup["account"]]
    assert set(receipts) == {setup["cash"].id, setup["upi"].id}


def test_FR_DPDP_012_fees_is_not_an_eraser() -> None:
    assert not hasattr(fees, "erase_for_subject")
