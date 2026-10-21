"""Data-principal access report contribution of the Tally connector (R3-1A; FR-DPDP-009,
ADR-0048 decision 6, PRV-031).

The Tally party ledgers the school linked to a student: ledger name, group, closing balance,
the date of that balance and when it was last synced. Unlinked ledgers are never read
(ADR-0032 §6); device keys, enrolment codes and sync batches are never shown. The snapshot is a
copy of the school's own accounts in Tally: this module never erases it.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.report_format import cap_rows, fmt_bool, fmt_date, fmt_datetime, fmt_money, fmt_text
from app.core.subjects import ReportSection, Subject
from app.tally import repository as repo

CLASS_KEY = "tally_ledgers"
COLUMNS = (
    "Tally ledger",
    "Group",
    "Closing balance",
    "Balance as of",
    "Last synced",
    "Still in Tally",
)


def subject_report(session: Session, subject: Subject) -> list[ReportSection]:
    """The ledgers linked to the student, newest snapshot first; [] for other subject kinds or
    when no ledger is linked."""
    if subject.kind != "student":
        return []
    balances = repo.linked_balances(session, [subject.id])
    if not balances:
        return []
    balances.sort(key=lambda b: (b.synced_at, b.ledger_name), reverse=True)
    rows = [
        (
            fmt_text(b.ledger_name),
            fmt_text(b.group_name),
            fmt_money(b.closing_balance),
            fmt_date(b.as_of),
            fmt_datetime(b.synced_at),
            fmt_bool(b.present),
        )
        for b in balances
    ]
    kept, note = cap_rows(rows)
    return [
        ReportSection(
            class_key=CLASS_KEY,
            title="Tally ledgers linked to the student",
            columns=COLUMNS,
            rows=tuple(kept),
            note=note,
        )
    ]


__all__ = ["subject_report"]
