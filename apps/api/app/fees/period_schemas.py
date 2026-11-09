"""IO models of the accountant and management reports (FM-04; FR-FEE-061..075).

Read-only reports over the ``fin`` ledger: amounts are :class:`decimal.Decimal` with two places
(``Money``); dates are IST calendar dates. No free-text reasons in any of these models (they are
offered in register downloads only, on request: FR-FEE-067).
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Literal

from app.fees.schemas import (
    AdjustmentReason,
    CancelReason,
    ConcessionBasis,
    ConcessionType,
    Money,
    ReceiptMode,
    ReceiptStatus,
    RefundMode,
    _Out,
)

ReportFormat = Literal["csv", "xlsx"]
CollectionPart = Literal["receipts", "heads", "days", "modes", "collectors"]
RegisterKind = Literal["concessions", "adjustments", "cancellations", "refunds"]

# --- collection register (FR-FEE-061..063) -------------------------------------------------------


class PeriodReceipt(_Out):
    """One receipt numbered in the period. ``status`` is today's status: a receipt cancelled
    later keeps its number in the series and is listed again under ``cancellations``."""

    id: uuid.UUID
    receipt_number: str
    receipt_date: dt.date
    amount: Money
    mode: ReceiptMode
    status: ReceiptStatus
    student_name: str
    class_section: str
    collected_by_name: str | None


class PeriodCancellation(_Out):
    """A receipt cancellation approved in the period (dated the day it was approved)."""

    cancellation_id: uuid.UUID
    receipt_id: uuid.UUID
    receipt_number: str
    receipt_date: dt.date
    cancelled_on: dt.date
    amount: Money
    mode: ReceiptMode
    reason_code: CancelReason
    requested_by_name: str | None
    approved_by_name: str | None


class PeriodRefund(_Out):
    """A refund paid in the period (voucher date)."""

    refund_id: uuid.UUID
    voucher_number: str
    voucher_date: dt.date
    receipt_number: str
    amount: Money
    mode: RefundMode
    requested_by_name: str | None
    approved_by_name: str | None


class DayTotal(_Out):
    date: dt.date
    receipts: int
    gross: Money
    cancelled: Money
    refunded: Money
    net: Money


class HeadTotal(_Out):
    """Collected per fee head (``head_id`` null: late fees). Cancellations and refunds of the
    period are subtracted on the head they were allocated to."""

    head_id: uuid.UUID | None
    head_code: str | None
    head_name: str
    collected: Money
    cancelled: Money
    refunded: Money
    net: Money


class PeriodModeTotal(_Out):
    mode: str
    receipts: int
    gross: Money
    cancelled: Money
    refunded: Money
    net: Money


class PeriodCollectorTotal(_Out):
    """Receipts per collector (``membership_id`` null: online payments)."""

    membership_id: uuid.UUID | None
    name: str | None
    receipts: int
    gross: Money


class CollectionRegisterOut(_Out):
    """The collection register of an IST date range (FR-FEE-061): gross receipts numbered in the
    period, less cancellations approved and refunds paid in the period. ``receipts`` is cut at
    the screen limit (``truncated``); the totals cover every receipt."""

    date_from: dt.date
    date_to: dt.date
    by_day: list[DayTotal]
    by_head: list[HeadTotal]
    by_mode: list[PeriodModeTotal]
    by_collector: list[PeriodCollectorTotal]
    receipts: list[PeriodReceipt]
    receipts_count: int
    truncated: bool
    cancellations: list[PeriodCancellation]
    refunds: list[PeriodRefund]
    receipts_total: Money
    cancelled_total: Money
    refunded_total: Money
    net_total: Money


# --- demand, collection, balance (FR-FEE-064, FR-FEE-065) ----------------------------------------


class DcbRow(_Out):
    """One class and section (``section_id`` null: students with no active enrolment in that
    year, e.g. left). ``net_demand`` = demand - concessions + late fees + adjustments - written
    off; ``balance`` = net demand - collected + refunded."""

    class_id: uuid.UUID | None
    class_label: str
    section_id: uuid.UUID | None
    section_label: str | None
    accounts: int
    demand: Money
    concessions: Money
    late_fees: Money
    adjustments: Money
    written_off: Money
    net_demand: Money
    collected: Money
    refunded: Money
    balance: Money


class EarlierYearBalance(_Out):
    """Earlier-year dues still open (FM-01): accounts of a year before the report's year."""

    academic_year_id: uuid.UUID
    academic_year_label: str
    accounts: int
    balance: Money
    written_off: Money


class DcbOut(_Out):
    academic_year_id: uuid.UUID
    academic_year_label: str
    as_of: dt.date
    rows: list[DcbRow]
    totals: DcbRow
    earlier_years: list[EarlierYearBalance]
    earlier_years_balance: Money


# --- registers (FR-FEE-066, FR-FEE-067) ----------------------------------------------------------


class ConcessionEntry(_Out):
    concession_id: uuid.UUID
    account_id: uuid.UUID
    student_id: uuid.UUID
    display_name: str | None
    admission_no: str | None
    class_section: str | None
    academic_year_label: str | None
    concession_type: ConcessionType
    basis: ConcessionBasis
    value: Money
    amount: Money
    requested_by_name: str | None
    approved_by_name: str | None
    approved_on: dt.date


class AdjustmentEntry(_Out):
    """An approved adjustment; ``write_off`` shows as "Written off by management" (FM-01)."""

    adjustment_id: uuid.UUID
    account_id: uuid.UUID
    student_id: uuid.UUID
    display_name: str | None
    admission_no: str | None
    class_section: str | None
    academic_year_label: str | None
    reason_code: AdjustmentReason
    reason_label: str
    head_name: str | None
    instalment_no: int
    amount: Money
    requested_by_name: str | None
    approved_by_name: str | None
    approved_on: dt.date


class TypeTotal(_Out):
    code: str
    count: int
    total: Money


class RegistersOut(_Out):
    """Approved concessions and adjustments (write-offs included) and receipt cancellations and
    refunds of an IST date range, each list cut at the screen limit (``truncated``)."""

    date_from: dt.date
    date_to: dt.date
    concessions: list[ConcessionEntry]
    concessions_by_type: list[TypeTotal]
    concessions_total: Money
    adjustments: list[AdjustmentEntry]
    adjustments_by_reason: list[TypeTotal]
    written_off_total: Money
    cancellations: list[PeriodCancellation]
    cancelled_total: Money
    refunds: list[PeriodRefund]
    refunded_total: Money
    truncated: bool


# --- ageing (FR-FEE-068) -------------------------------------------------------------------------


class AgeingBucket(_Out):
    """Days past due, inclusive; ``to_days`` null for the last ("more than") bucket."""

    from_days: int
    to_days: int | None


class AgeingRow(_Out):
    """One student: overdue amounts of the current year by age, then earlier-year dues."""

    student_id: uuid.UUID
    display_name: str | None
    admission_no: str | None
    class_section: str | None
    bands: list[Money]
    earlier_years: Money
    overdue: Money
    oldest_due_on: dt.date | None


class AgeingClassRow(_Out):
    class_section: str | None
    students: int
    bands: list[Money]
    earlier_years: Money
    overdue: Money


class AgeingOut(_Out):
    as_of: dt.date
    bands: list[AgeingBucket]
    rows: list[AgeingRow]
    truncated: bool
    by_class: list[AgeingClassRow]
    totals: list[Money]
    earlier_years_total: Money
    overdue_total: Money
    not_yet_due_total: Money


# --- receipt series check (FR-FEE-070) -----------------------------------------------------------


class SeriesCheck(_Out):
    """One numbering series of a financial year: numbers used, cancelled and missing. A gap-free
    series has ``missing_count`` 0 and ``last_number`` equal to the counter."""

    series: Literal["receipt", "refund"]
    financial_year: str
    first_number: str | None
    last_number: str | None
    used: int
    cancelled: int
    counter: int
    missing_count: int
    missing: list[int]


class SeriesOut(_Out):
    financial_year: str
    series: list[SeriesCheck]
    gap_free: bool


__all__ = [
    "AdjustmentEntry",
    "AgeingBucket",
    "AgeingClassRow",
    "AgeingOut",
    "AgeingRow",
    "CollectionPart",
    "CollectionRegisterOut",
    "ConcessionEntry",
    "DayTotal",
    "DcbOut",
    "DcbRow",
    "EarlierYearBalance",
    "HeadTotal",
    "PeriodCancellation",
    "PeriodCollectorTotal",
    "PeriodModeTotal",
    "PeriodReceipt",
    "PeriodRefund",
    "RegisterKind",
    "RegistersOut",
    "ReportFormat",
    "SeriesCheck",
    "SeriesOut",
    "TypeTotal",
]
