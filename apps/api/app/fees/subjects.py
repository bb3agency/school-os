"""Data-principal access report contribution of the fees module (R3-1A; FR-DPDP-009,
ADR-0048 decision 6, PRV-031).

For a student: each fee account (plan, year, charged, concessions, paid, refunded, balance),
the receipts (number, date, amount, mode, status), concessions and refunds, under
``financial_records``; the number of bank statement lines matched to the student's receipts
under ``bank_statements`` (a count only, never the bank's narration).

Left out on purpose: reasons and decision notes (staff free text), cheque/bank/UTR details of
``mode_details``, receipt and voucher content and hashes, payment link URLs, and the payment
provider's raw notices (``provider_inboxes``): they carry no student reference other than inside
the encrypted payload, so nothing is matched to a student and the class is omitted.

A guardian: the schema records no payer guardian on receipts or payments, so a guardian's report
has no fee section ([]). Fee records are kept for the school's accounts: this module never
erases.
"""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.core.report_format import (
    EMPTY,
    cap_rows,
    fmt_count,
    fmt_date,
    fmt_money,
    fmt_text,
    humanise,
)
from app.core.subjects import MAX_SECTION_ROWS, ReportSection, Subject
from app.fees import _common as c
from app.fees import ledger
from app.fees import repository as repo
from app.fees import upi_repository as upi_repo
from app.fees.models import StudentFeeAccount
from app.tenancy import service as tenancy

FINANCIAL = "financial_records"
BANK = "bank_statements"

_MODES = {
    "cash": "Cash",
    "cheque": "Cheque",
    "bank_transfer": "Bank transfer",
    "upi_qr": "UPI",
    "razorpay": "Online payment",
    "original_method": "Original payment method",
}


def _mode(code: str | None) -> str:
    return _MODES.get(code or "", humanise(code))


def _accounts_section(session: Session, accounts: list[StudentFeeAccount]) -> ReportSection:
    plans = repo.plans_by_id(session, {a.plan_id for a in accounts})
    years = {y.id: y.label for y in tenancy.list_academic_years(session)}
    dues = ledger.dues_of(session, accounts, c.today(session))
    ordered = sorted(accounts, key=lambda a: (a.created_at, a.id), reverse=True)
    rows = []
    for a in ordered:
        totals = dues[a.id].totals()
        plan = plans.get(a.plan_id)
        rows.append(
            (
                fmt_text(years.get(a.academic_year_id)),
                fmt_text(plan.name if plan is not None else None),
                humanise(a.status),
                fmt_money(totals.charged + totals.late_fees + totals.adjustments),
                fmt_money(totals.concessions),
                fmt_money(totals.paid),
                fmt_money(totals.refunded),
                fmt_money(totals.balance),
            )
        )
    kept, note = cap_rows(rows)
    return ReportSection(
        class_key=FINANCIAL,
        title="Fee accounts",
        columns=(
            "Academic year",
            "Fee plan",
            "Status",
            "Total charged",
            "Concessions",
            "Paid",
            "Refunded",
            "Balance",
        ),
        rows=tuple(kept),
        note=note,
    )


def _receipts_section(session: Session, account_ids: list[uuid.UUID]) -> ReportSection | None:
    receipts, total = repo.receipts_of_accounts(session, account_ids, limit=MAX_SECTION_ROWS)
    if total == 0:
        return None
    rows = [
        (
            fmt_text(r.receipt_number),
            fmt_date(r.receipt_date),
            fmt_money(r.amount),
            _mode(r.mode),
            humanise(r.status),
        )
        for r in receipts
    ]
    kept, note = cap_rows(rows, total)
    return ReportSection(
        class_key=FINANCIAL,
        title="Fee receipts",
        columns=("Receipt number", "Date", "Amount", "Mode", "Status"),
        rows=tuple(kept),
        note=note,
    )


def _concessions_section(session: Session, account_ids: list[uuid.UUID]) -> ReportSection | None:
    found = repo.concessions_of_accounts(session, account_ids)
    if not found:
        return None
    rows = [
        (
            humanise(x.concession_type),
            f"{x.value.normalize():f}%" if x.basis == "percent" else fmt_money(x.value),
            fmt_money(x.total_amount) if x.total_amount is not None else EMPTY,
            fmt_date(x.requested_at),
            humanise(x.status),
        )
        for x in found
    ]
    kept, note = cap_rows(rows)
    return ReportSection(
        class_key=FINANCIAL,
        title="Fee concessions",
        columns=("Concession", "Value", "Total amount", "Requested on", "Status"),
        rows=tuple(kept),
        note=note,
    )


def _refunds_section(session: Session, account_ids: list[uuid.UUID]) -> ReportSection | None:
    found = repo.refunds_of_accounts(session, account_ids)
    if not found:
        return None
    rows = [
        (
            fmt_text(x.voucher_number),
            fmt_date(x.voucher_date or x.requested_at),
            fmt_money(x.amount),
            _mode(x.mode),
            humanise(x.status),
        )
        for x in found
    ]
    kept, note = cap_rows(rows)
    return ReportSection(
        class_key=FINANCIAL,
        title="Fee refunds",
        columns=("Voucher number", "Date", "Amount", "Mode", "Status"),
        rows=tuple(kept),
        note=note,
    )


def subject_report(session: Session, subject: Subject) -> list[ReportSection]:
    """The student's fee records; [] for a guardian or member, or a student without a fee
    account."""
    if subject.kind != "student":
        return []
    accounts = repo.accounts_of_students(session, [subject.id])
    if not accounts:
        return []
    account_ids = [a.id for a in accounts]
    sections: list[ReportSection] = [_accounts_section(session, accounts)]
    for extra in (
        _receipts_section(session, account_ids),
        _concessions_section(session, account_ids),
        _refunds_section(session, account_ids),
    ):
        if extra is not None:
            sections.append(extra)
    matched = upi_repo.count_lines_matched_to_accounts(session, account_ids)
    if matched:
        sections.append(
            ReportSection(
                class_key=BANK,
                title="Bank statement lines linked to the student's receipts",
                columns=("Lines",),
                rows=((fmt_count(matched),),),
                note="The bank's narration text is not shown; it is erased after 90 days.",
            )
        )
    return sections


def fee_targets_of_student(
    session: Session, student_id: uuid.UUID
) -> tuple[list[uuid.UUID], list[uuid.UUID]]:
    """The ids of the student's fee accounts and of all their receipts (what parent dues and
    receipt links point at; used by ``app.public`` for the access report, FR-DPDP-009)."""
    account_ids = [a.id for a in repo.accounts_of_students(session, [student_id])]
    return account_ids, repo.receipt_ids_of_accounts(session, account_ids)


__all__ = ["fee_targets_of_student", "subject_report"]
