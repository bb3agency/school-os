"""Pydantic v2 IO models for the fee ledger and receipts (docs/09 "Fees"; FR-FEE-*; ADR-0042).

Inputs forbid unknown fields and NFC-normalise + trim text. Money is :class:`decimal.Decimal`
rupees with at most two decimals (``numeric(14,2)``, FR-FEE-028): JSON numbers or strings in,
strings out (exact, never a float). Reasons are 10..1000 characters and never a full Aadhaar
number (the service checks both with field-level codes). Outputs carry personal financial data
(C2): only fee permissions reach them (FR-FEE-024).
"""

from __future__ import annotations

import datetime as dt
import unicodedata
import uuid
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, StringConstraints

HeadKind = Literal["tuition", "admission", "exam", "books", "transport", "other"]
PlanStatus = Literal["draft", "active", "superseded"]
LateFeeKind = Literal["none", "flat_per_instalment", "per_day"]
CounterMode = Literal["cash", "cheque", "bank_transfer", "upi_qr"]
ReceiptMode = Literal["cash", "cheque", "bank_transfer", "upi_qr", "razorpay"]
ReceiptStatus = Literal["issued", "cancelled"]
PdfStatus = Literal["queued", "ready", "failed"]
RequestStatus = Literal["pending", "approved", "rejected", "withdrawn"]
ConcessionType = Literal["sibling", "staff_ward", "merit", "hardship", "rte", "other"]
ConcessionBasis = Literal["amount", "percent"]
AdjustmentReason = Literal[
    "late_fee_waiver", "plan_change", "concession_reversal", "correction", "other"
]
CancelReason = Literal[
    "wrong_student", "wrong_amount", "cheque_dishonoured", "duplicate_entry", "other"
]
RefundMode = Literal["cash", "cheque", "bank_transfer"]
RefundStatus = Literal[
    "pending", "approved", "processing", "processed", "failed", "rejected", "withdrawn"
]
EntryKind = Literal[
    "charge", "concession", "late_fee", "adjustment", "payment", "payment_reversal", "refund"
]
ApprovalKind = Literal["concession", "adjustment", "cancellation", "refund"]
PaperSize = Literal["A5", "A4"]


def _nfc(value: Any) -> Any:
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value).strip()
    return value


_NO_CONTROL = r"^[^\x00-\x1f\x7f]*$"
_NO_CONTROL_MULTILINE = r"^[^\x00-\x09\x0b-\x1f\x7f]*$"

Money = Annotated[Decimal, Field(max_digits=14, decimal_places=2)]
PositiveMoney = Annotated[Decimal, Field(gt=0, max_digits=14, decimal_places=2)]
Line = Annotated[
    str, BeforeValidator(_nfc), StringConstraints(min_length=1, max_length=80, pattern=_NO_CONTROL)
]
Label = Annotated[
    str, BeforeValidator(_nfc), StringConstraints(min_length=1, max_length=40, pattern=_NO_CONTROL)
]
# Length rules (10..1000, config.yaml) are checked by the service with field-level codes.
Note = Annotated[
    str, BeforeValidator(_nfc), StringConstraints(max_length=1000, pattern=_NO_CONTROL_MULTILINE)
]
HeadCode = Annotated[
    str,
    BeforeValidator(lambda v: _nfc(v).upper() if isinstance(v, str) else v),
    StringConstraints(pattern=r"^[A-Z0-9][A-Z0-9_-]{0,19}$"),
]
CategoryCode = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,31}$")]
InstalmentNo = Annotated[int, Field(ge=1, le=24)]
GstNote = Annotated[
    str, BeforeValidator(_nfc), StringConstraints(min_length=1, max_length=200, pattern=_NO_CONTROL)
]
Typed = Annotated[
    str, BeforeValidator(_nfc), StringConstraints(min_length=1, max_length=40, pattern=_NO_CONTROL)
]


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class _Out(BaseModel):
    model_config = ConfigDict(from_attributes=True, frozen=True)


# --- status and settings ------------------------------------------------------------------------


class FeeStatusOut(_Out):
    """The counter's context: fees are on for this school (otherwise 404 ``module_hidden``),
    today's IST date (receipts are always dated today, FR-FEE-022) and whether today is
    closed."""

    enabled: bool
    today: dt.date
    financial_year: str
    day_closed: bool
    paper_size: PaperSize


class FeeSettingsOut(_Out):
    paper_size: PaperSize
    gst_note: str
    reminder_offsets: list[int]
    min_reminder_amount: Money
    version: int


class FeeSettingsIn(_In):
    paper_size: PaperSize
    gst_note: GstNote
    reminder_offsets: list[Annotated[int, Field(ge=-60, le=60)]] = Field(max_length=5)
    min_reminder_amount: Money = Field(ge=0)


# --- heads -------------------------------------------------------------------------------------


class HeadCreate(_In):
    """A fee head (FR-FEE-002). ``gst_exempt = false`` marks a taxable head: it can be planned but
    never collected through receipts in R2 (FR-FEE-023)."""

    code: HeadCode
    name: Line
    kind: HeadKind
    gst_exempt: bool = True
    sort_order: int = Field(default=100, ge=0, le=10000)


class HeadPatch(_In):
    name: Line | None = None
    kind: HeadKind | None = None
    gst_exempt: bool | None = None
    active: bool | None = None
    sort_order: int | None = Field(default=None, ge=0, le=10000)


class HeadOut(_Out):
    id: uuid.UUID
    code: str
    name: str
    kind: HeadKind
    gst_exempt: bool
    active: bool
    sort_order: int
    version: int


# --- plans -------------------------------------------------------------------------------------


class LateFeeRule(_In):
    """``flat_per_instalment``: ``amount`` once per overdue instalment after ``grace_days``.
    ``per_day``: ``amount`` per day after ``grace_days`` up to ``cap`` per instalment. Never
    compounding (FR-FEE-009)."""

    kind: LateFeeKind = "none"
    amount: PositiveMoney | None = None
    grace_days: int = Field(default=0, ge=0, le=90)
    cap: PositiveMoney | None = None


class PlanLineIn(_In):
    head_id: uuid.UUID
    annual_amount: PositiveMoney


class InstalmentIn(_In):
    """One instalment: its number, label (``Term 1``), due date inside the academic year and the
    amount per head (head id -> amount; heads not listed are 0)."""

    instalment_no: InstalmentNo
    label: Label
    due_on: dt.date
    amounts: dict[uuid.UUID, Money] = Field(max_length=30)


class FeePlanCreate(_In):
    academic_year_id: uuid.UUID
    class_id: uuid.UUID
    category_code: CategoryCode | None = None
    name: Line
    lines: list[PlanLineIn] = Field(min_length=1, max_length=30)
    instalments: list[InstalmentIn] = Field(min_length=1, max_length=24)
    late_fee_rule: LateFeeRule = Field(default_factory=LateFeeRule)


class FeePlanPatch(_In):
    """Change a draft plan (``If-Match``). Lists replace the plan's lines or instalments."""

    name: Line | None = None
    lines: list[PlanLineIn] | None = Field(default=None, min_length=1, max_length=30)
    instalments: list[InstalmentIn] | None = Field(default=None, min_length=1, max_length=24)
    late_fee_rule: LateFeeRule | None = None


class PlanLineOut(_Out):
    head_id: uuid.UUID
    head_code: str
    head_name: str
    annual_amount: Money
    gst_exempt: bool


class InstalmentOut(_Out):
    instalment_no: int
    label: str
    due_on: dt.date
    amounts: dict[str, Money]
    total: Money


class FeePlanOut(_Out):
    id: uuid.UUID
    academic_year_id: uuid.UUID
    class_id: uuid.UUID
    category_code: str | None
    plan_version: int
    status: PlanStatus
    name: str
    late_fee_rule: dict[str, Any]
    previous_plan_id: uuid.UUID | None
    lines: list[PlanLineOut]
    instalments: list[InstalmentOut]
    annual_total: Money
    accounts: int = Field(description="Student fee accounts on this plan")
    version: int


class PlanApplyIn(_In):
    """Apply an active plan to the class's active students of its year (FR-FEE-005).
    ``section_ids`` limits it to some sections; a student admitted after an instalment's due
    date gets only later instalments unless ``include_past_instalments``; ``dry_run`` only
    counts."""

    section_ids: list[uuid.UUID] | None = Field(default=None, max_length=50)
    include_past_instalments: bool = False
    dry_run: bool = False


class SkippedStudent(_Out):
    student_id: uuid.UUID
    reason: Literal["account_on_other_plan", "other_category", "no_instalments_left"]


class PlanApplyOut(_Out):
    dry_run: bool
    students: int
    accounts_created: int
    already_present: int
    charges_posted: int
    skipped: list[SkippedStudent]


class PlanDifference(_Out):
    account_id: uuid.UUID
    student_id: uuid.UUID
    instalment_no: int
    head_id: uuid.UUID
    charged: Money
    planned: Money
    difference: Money


class PlanDifferencesOut(_Out):
    """Per account, instalment and head: what the previous version charged and what this one
    plans (FR-FEE-004). Proposing them creates adjustment requests that need approval."""

    plan_id: uuid.UUID
    previous_plan_id: uuid.UUID | None
    items: list[PlanDifference]


class ProposeAdjustmentsIn(_In):
    reason: Note


class ProposeAdjustmentsOut(_Out):
    proposed: int
    already_proposed: int


# --- accounts ----------------------------------------------------------------------------------


class AccountSearch(_In):
    """Find a student's fee account by part of a name or the admission number (in the body,
    never the URL), optionally in one section or class, for the current academic year unless
    ``academic_year_id`` is given."""

    query: Annotated[
        str,
        BeforeValidator(_nfc),
        StringConstraints(max_length=100, pattern=_NO_CONTROL),
    ] = ""
    section_id: uuid.UUID | None = None
    class_id: uuid.UUID | None = None
    academic_year_id: uuid.UUID | None = None
    has_dues: bool | None = None


class AccountSummary(_Out):
    account_id: uuid.UUID
    student_id: uuid.UUID
    display_name: str | None
    admission_no: str | None
    class_section: str | None
    academic_year_id: uuid.UUID
    balance: Money
    overdue: Money
    status: Literal["open", "closed"]


class DueLine(_Out):
    """One (instalment, head) line of a fee account; ``head_id`` null = the late-fee line of that
    instalment. ``due`` = charged - concessions + late fee + adjustments - paid + refunded."""

    instalment_no: int
    label: str
    due_on: dt.date | None
    head_id: uuid.UUID | None
    head_code: str | None
    head_name: str | None
    collectable: bool
    charged: Money
    concession: Money
    late_fee: Money
    adjustment: Money
    paid: Money
    due: Money
    overdue: bool


class AccountTotals(_Out):
    charged: Money
    concessions: Money
    late_fees: Money
    adjustments: Money
    paid: Money
    refunded: Money
    balance: Money
    overdue: Money
    collectable: Money = Field(
        description="What a receipt can take now: positive dues of collectable heads"
    )


class AccountOut(_Out):
    id: uuid.UUID
    student_id: uuid.UUID
    display_name: str | None
    admission_no: str | None
    class_section: str | None
    academic_year_id: uuid.UUID
    plan_id: uuid.UUID
    plan_name: str
    category_code: str | None
    status: Literal["open", "closed"]
    lines: list[DueLine]
    totals: AccountTotals
    pending_requests: int
    version: int


class StatementEntry(_Out):
    id: uuid.UUID
    entry_date: dt.date
    entry_kind: EntryKind
    instalment_no: int
    head_code: str | None
    amount: Money
    balance: Money
    receipt_id: uuid.UUID | None
    receipt_number: str | None
    source_id: uuid.UUID | None


class StatementOut(_Out):
    account_id: uuid.UUID
    entries: list[StatementEntry]
    balance: Money


# --- receipts ----------------------------------------------------------------------------------


class ModeDetailsIn(_In):
    """Cheque: ``cheque_number`` (6 digits), ``bank_name``, ``cheque_date``. Bank transfer:
    ``reference`` (12-22 letters or digits)."""

    cheque_number: Typed | None = None
    bank_name: Line | None = None
    cheque_date: dt.date | None = None
    reference: Typed | None = None


class AllocationIn(_In):
    instalment_no: InstalmentNo
    head_id: uuid.UUID | None = None
    amount: PositiveMoney


class ReceiptCreate(_In):
    """Take a payment at the counter (FR-FEE-010). ``amount`` > 0 and ≤ what is due now (no
    advances). ``allocations`` default to oldest due first; when given they must add up to the
    amount and stay within each line's due. ``utr``: the 12-digit UPI reference (``upi_qr``
    only)."""

    account_id: uuid.UUID
    amount: PositiveMoney
    mode: CounterMode
    mode_details: ModeDetailsIn = Field(default_factory=ModeDetailsIn)
    utr: Annotated[str, BeforeValidator(_nfc), StringConstraints(max_length=40)] | None = None
    allocations: list[AllocationIn] | None = Field(default=None, max_length=300)


class AllocationOut(_Out):
    instalment_no: int
    label: str
    head_id: uuid.UUID | None
    head_name: str
    amount: Money


class ReceiptLineOut(_Out):
    instalment_no: int
    label: str
    head_code: str | None
    head_name: str
    amount: Money


class CancellationOut(_Out):
    id: uuid.UUID
    receipt_id: uuid.UUID
    receipt_number: str
    reason_code: CancelReason
    reason: str
    status: RequestStatus
    requested_by: uuid.UUID
    requested_by_name: str | None
    requested_at: dt.datetime
    decided_by: uuid.UUID | None
    decided_by_name: str | None
    decided_at: dt.datetime | None
    decision_note: str | None
    cancelled_on: dt.date | None
    version: int


class RefundOut(_Out):
    id: uuid.UUID
    receipt_id: uuid.UUID
    receipt_number: str
    account_id: uuid.UUID
    amount: Money
    mode: Literal["original_method", "cash", "cheque", "bank_transfer"]
    reason: str
    status: RefundStatus
    requested_by: uuid.UUID
    requested_by_name: str | None
    requested_at: dt.datetime
    decided_by: uuid.UUID | None
    decided_by_name: str | None
    decided_at: dt.datetime | None
    decision_note: str | None
    voucher_number: str | None
    voucher_date: dt.date | None
    version: int


class ReceiptOut(_Out):
    id: uuid.UUID
    receipt_number: str
    financial_year: str
    receipt_date: dt.date
    amount: Money
    amount_in_words: str
    mode: ReceiptMode
    mode_details: dict[str, str]
    utr: str | None
    status: ReceiptStatus
    account_id: uuid.UUID
    student_id: uuid.UUID
    student_name: str
    class_section: str
    admission_no: str
    collected_by: uuid.UUID | None
    collected_by_name: str | None
    issued_at: dt.datetime
    lines: list[ReceiptLineOut]
    content_sha256: str
    pdf_status: PdfStatus
    cancelled_on: dt.date | None
    cancellation: CancellationOut | None
    refunded: Money
    refunds: list[RefundOut]
    version: int


class ReceiptListItem(_Out):
    id: uuid.UUID
    receipt_number: str
    receipt_date: dt.date
    amount: Money
    mode: ReceiptMode
    status: ReceiptStatus
    account_id: uuid.UUID
    student_id: uuid.UUID
    student_name: str
    class_section: str
    collected_by: uuid.UUID | None
    collected_by_name: str | None
    cancelled_on: dt.date | None
    cancellation_pending: bool
    refunded: Money


class ReceiptDownloadOut(_Out):
    url: str
    expires_at: dt.datetime
    filename: str


# --- requests (maker-checker) -------------------------------------------------------------------


class CancellationRequest(_In):
    """Ask for a receipt to be cancelled (FR-FEE-012); a different holder of
    ``fee.receipt.cancel`` decides."""

    reason_code: CancelReason
    reason: Note


class DecisionIn(_In):
    note: Note | None = None


class RejectIn(_In):
    reason: Note


class ConcessionTarget(_In):
    instalment_no: InstalmentNo
    head_id: uuid.UUID


class ConcessionCreate(_In):
    """A concession (FR-FEE-008): ``basis = percent`` takes ``value`` % of each target's charge;
    ``basis = amount`` spreads ``value`` rupees over the targets in order (oldest first)."""

    concession_type: ConcessionType
    basis: ConcessionBasis
    value: PositiveMoney
    targets: list[ConcessionTarget] = Field(min_length=1, max_length=300)
    reason: Note
    evidence_document_id: uuid.UUID | None = None


class ConcessionOut(_Out):
    id: uuid.UUID
    account_id: uuid.UUID
    concession_type: ConcessionType
    basis: ConcessionBasis
    value: Money
    targets: list[dict[str, Any]]
    reason: str
    evidence_document_id: uuid.UUID | None
    status: RequestStatus
    total_amount: Money | None
    requested_by: uuid.UUID
    requested_by_name: str | None
    requested_at: dt.datetime
    decided_by: uuid.UUID | None
    decided_by_name: str | None
    decided_at: dt.datetime | None
    decision_note: str | None
    version: int


class AdjustmentCreate(_In):
    """A manual adjustment (FR-FEE-007): positive adds to what is due, negative reduces it. A
    late-fee waiver (``reason_code = late_fee_waiver``) has no head and a negative amount."""

    instalment_no: InstalmentNo
    head_id: uuid.UUID | None = None
    amount: Money
    reason_code: AdjustmentReason
    reason: Note


class AdjustmentOut(_Out):
    id: uuid.UUID
    account_id: uuid.UUID
    instalment_no: int
    head_id: uuid.UUID | None
    amount: Money
    reason_code: AdjustmentReason
    reason: str
    source_plan_id: uuid.UUID | None
    status: RequestStatus
    requested_by: uuid.UUID
    requested_by_name: str | None
    requested_at: dt.datetime
    decided_by: uuid.UUID | None
    decided_by_name: str | None
    decided_at: dt.datetime | None
    decision_note: str | None
    version: int


class RefundCreate(_In):
    """Refund money against a receipt (FR-FEE-013): at most what it took and was not refunded
    yet. Cash, cheque and bank transfer refunds are written as a voucher when approved."""

    amount: PositiveMoney
    mode: RefundMode
    mode_details: ModeDetailsIn = Field(default_factory=ModeDetailsIn)
    reason: Note


class ApprovalItem(_Out):
    """One request waiting for a decision (``mine``: you made it, so you cannot decide it)."""

    kind: ApprovalKind
    id: uuid.UUID
    account_id: uuid.UUID
    student_id: uuid.UUID
    student_name: str | None
    class_section: str | None
    amount: Money | None
    summary_code: str
    reason: str
    requested_by: uuid.UUID
    requested_by_name: str | None
    requested_at: dt.datetime
    receipt_id: uuid.UUID | None
    receipt_number: str | None
    mine: bool
    can_decide: bool
    version: int


# --- reports -----------------------------------------------------------------------------------


class ModeTotal(_Out):
    mode: ReceiptMode
    count: int
    total: Money


class CollectorTotal(_Out):
    membership_id: uuid.UUID | None
    name: str | None
    count: int
    total: Money


class DayReceipt(_Out):
    id: uuid.UUID
    receipt_number: str
    amount: Money
    mode: ReceiptMode
    status: ReceiptStatus
    student_name: str
    class_section: str
    collected_by_name: str | None


class DayCancellation(_Out):
    cancellation_id: uuid.UUID
    receipt_id: uuid.UUID
    receipt_number: str
    receipt_date: dt.date
    amount: Money
    mode: ReceiptMode
    reason_code: CancelReason


class DayRefund(_Out):
    refund_id: uuid.UUID
    voucher_number: str
    receipt_number: str
    amount: Money
    mode: str


class DayCloseOut(_Out):
    close_date: dt.date
    receipt_count: int
    receipt_total: Money
    closed_by: uuid.UUID
    closed_by_name: str | None
    closed_at: dt.datetime


class DayCloseIn(_In):
    """The IST date to close (today or a recent day; never in the future)."""

    date: dt.date


class DailyReportOut(_Out):
    """The daily collection report (FR-FEE-018) for one IST date."""

    date: dt.date
    by_mode: list[ModeTotal]
    by_collector: list[CollectorTotal]
    receipts: list[DayReceipt]
    cancellations: list[DayCancellation]
    refunds: list[DayRefund]
    receipts_total: Money
    cancelled_total: Money
    refunded_total: Money
    net_total: Money
    cash_to_hand_over: Money
    cheque_to_hand_over: Money
    closed: DayCloseOut | None


class DuesRow(_Out):
    account_id: uuid.UUID
    student_id: uuid.UUID
    display_name: str | None
    admission_no: str | None
    class_section: str | None
    overdue: Money
    oldest_due_on: dt.date | None
    balance: Money
    last_payment_on: dt.date | None


class DuesOut(_Out):
    """Students with overdue amounts, oldest due first (FR-FEE-020)."""

    as_of: dt.date
    rows: list[DuesRow]
    students: int
    total_overdue: Money


# --- other modules' data contract (fees.service) -------------------------------------------------


class FeeAccountSummary(_Out):
    """A student's fee position for other modules (Ask's fee tool, parent pages, the TC preview
    as information only, FR-FEE-030). IDs and amounts only."""

    student_id: uuid.UUID
    account_id: uuid.UUID
    academic_year_id: uuid.UUID
    balance: Money
    overdue: Money
    oldest_due_on: dt.date | None
    last_payment_on: dt.date | None


class InstalmentDue(_Out):
    instalment_no: int
    label: str
    due_on: dt.date | None
    due: Money
    overdue: bool


class StudentDues(_Out):
    student_id: uuid.UUID
    account_id: uuid.UUID
    instalments: list[InstalmentDue]
    total_due: Money
    as_of: dt.date
