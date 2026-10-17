"""Pydantic v2 IO models for UPI settings, bank statements and UPI confirmations (docs/09 "Fees";
FR-FEE-014..017; ADR-0042 decision 9).

Inputs forbid unknown fields and NFC-normalise + trim text. Money is :class:`decimal.Decimal`
rupees (strings out, exact). Statement lines are personal financial data (C2): only
``fee.reconcile`` reaches them; the narration is shown masked (account, card, mobile and UPI IDs
already masked when stored) and is gone after 90 days.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Annotated, Literal

from pydantic import BeforeValidator, Field, StringConstraints

from app.fees.schemas import Money, _In, _nfc, _Out

MatchStatus = Literal["unmatched", "proposed", "confirmed", "ignored"]
MatchKind = Literal["utr_exact", "amount_date", "manual"]
RefKind = Literal["utr", "reference", "none"]
IgnoreReason = Literal["not_a_fee", "recorded_elsewhere", "duplicate_credit", "other"]
LineException = Literal[
    "no_receipt",
    "amount_mismatch",
    "receipt_cancelled",
    "receipt_already_confirmed",
    "confirmed_receipt_cancelled",
]
UtrState = Literal["free", "in_use", "invalid"]

_NO_CONTROL = r"^[^\x00-\x1f\x7f]*$"

PayeeName = Annotated[
    str, BeforeValidator(_nfc), StringConstraints(min_length=2, max_length=60, pattern=_NO_CONTROL)
]
Vpa = Annotated[
    str,
    BeforeValidator(_nfc),
    StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{1,63}@[A-Za-z][A-Za-z0-9]{1,63}$"),
]
PresetKey = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_-]{1,39}$")]


# --- UPI settings and QR (FR-FEE-015) ------------------------------------------------------------


class UpiSettingsIn(_In):
    payee_name: PayeeName
    vpa: Vpa
    confirm_within_days: int = Field(default=7, ge=1, le=30)


class UpiSettingsOut(_Out):
    """The school's UPI details. ``configured`` false: nothing set yet (no QR at the counter)."""

    configured: bool
    payee_name: str | None
    vpa: str | None
    confirm_within_days: int
    upi_uri: str | None = Field(
        description="upi://pay?pa=..&pn=..&cu=INR (no amount): what the static QR encodes"
    )
    version: int


class UtrCheckIn(_In):
    utr: Annotated[str, StringConstraints(min_length=1, max_length=40)]


class UtrCheckOut(_Out):
    """The counter's UTR check (FR-FEE-014): ``invalid`` (not 12 digits), ``in_use`` (on another
    issued receipt: its id and number), ``free``. ``seen_in_bank``: a bank credit with this UTR is
    already imported (the amount is not shown at the counter)."""

    state: UtrState
    receipt_id: uuid.UUID | None = None
    receipt_number: str | None = None
    seen_in_bank: bool = False


# --- statements (FR-FEE-016) ---------------------------------------------------------------------


class StatementImportIn(_In):
    document_id: uuid.UUID = Field(description="An import_file the caller uploaded (CSV or XLSX)")
    preset: PresetKey | None = Field(
        default=None, description="A preset key from GET /fees/bank-statement-presets; none: detect"
    )


class StatementPresetOut(_Out):
    preset: str
    version: int
    name: str
    verified: bool


class StatementImportOut(_Out):
    id: uuid.UUID
    preset: str
    preset_version: int
    file_kind: str
    period_from: dt.date | None
    period_to: dt.date | None
    line_count: int
    credit_count: int
    duplicate_count: int
    proposed_count: int
    imported_by: uuid.UUID
    imported_by_name: str | None
    imported_at: dt.datetime
    file_deleted_at: dt.datetime


class ReceiptRef(_Out):
    """The receipt a line is proposed for or confirms (or conflicts with)."""

    id: uuid.UUID
    receipt_number: str
    receipt_date: dt.date
    amount: Money
    status: str
    utr: str | None
    student_name: str
    class_section: str


class StatementLineOut(_Out):
    id: uuid.UUID
    import_id: uuid.UUID
    line_no: int
    value_date: dt.date
    amount: Money
    ref_kind: RefKind
    utr_or_ref: str | None
    narration: str | None = Field(description="Masked; null once erased (90 days)")
    narration_erased: bool
    match_status: MatchStatus
    match_kind: MatchKind | None
    receipt: ReceiptRef | None
    exception: LineException | None = Field(
        description="Why an unmatched (or confirmed) line needs a person; computed when read"
    )
    conflict_receipt: ReceiptRef | None = Field(
        description="The receipt with this line's UTR when it does not match (never corrected)"
    )
    ignore_reason: IgnoreReason | None
    decided_by_name: str | None
    decided_at: dt.datetime | None
    version: int


class StatementDetailOut(_Out):
    statement: StatementImportOut
    lines: list[StatementLineOut]


class ConfirmIn(_In):
    line_ids: list[uuid.UUID] = Field(min_length=1, max_length=5000)


class ConfirmSkipped(_Out):
    line_id: uuid.UUID
    code: str


class ConfirmOut(_Out):
    confirmed: int
    already_confirmed: int
    skipped: list[ConfirmSkipped]


class MatchIn(_In):
    receipt_id: uuid.UUID


class IgnoreIn(_In):
    reason: IgnoreReason


class RematchOut(_Out):
    looked_at: int
    proposed: int


# --- exceptions and report (FR-FEE-017) ----------------------------------------------------------


class UnconfirmedReceipt(_Out):
    id: uuid.UUID
    receipt_number: str
    receipt_date: dt.date
    amount: Money
    utr: str | None
    student_name: str
    class_section: str
    days_waiting: int
    proposed_line_id: uuid.UUID | None


class UpiExceptionsOut(_Out):
    as_of: dt.date
    confirm_within_days: int
    unconfirmed_receipts: list[UnconfirmedReceipt]
    unconfirmed_count: int
    lines: list[StatementLineOut] = Field(
        description="Unmatched credits and lines whose receipt was cancelled after confirmation"
    )
    lines_count: int
    proposed_count: int


class UpiReportRow(_Out):
    receipt_date: dt.date
    confirmed_count: int
    confirmed_total: Money
    unconfirmed_count: int
    unconfirmed_total: Money


class UpiReportOut(_Out):
    date_from: dt.date
    date_to: dt.date
    rows: list[UpiReportRow]
    confirmed_count: int
    confirmed_total: Money
    unconfirmed_count: int
    unconfirmed_total: Money
    overdue_count: int


__all__ = [
    "ConfirmIn",
    "ConfirmOut",
    "IgnoreIn",
    "MatchIn",
    "RematchOut",
    "StatementDetailOut",
    "StatementImportIn",
    "StatementImportOut",
    "StatementLineOut",
    "StatementPresetOut",
    "UpiExceptionsOut",
    "UpiReportOut",
    "UpiSettingsIn",
    "UpiSettingsOut",
    "UtrCheckIn",
    "UtrCheckOut",
]
