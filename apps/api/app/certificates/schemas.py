"""Pydantic v2 IO models for certificates and registers (US-1101..US-1108; docs/09
Certificates).

Inputs forbid unknown fields and NFC-normalise + trim text; the service checks each type's inputs
against ``config.yaml`` and refuses full Aadhaar numbers (FR-CERT-009). Outputs carry the printed
values of a certificate (C2 only: no restricted fields are ever printed).
"""

from __future__ import annotations

import datetime as dt
import unicodedata
import uuid
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, StringConstraints

CertificateType = Literal["transfer", "bonafide", "study", "conduct"]
Status = Literal["pending", "issued", "rejected", "withdrawn", "cancelled"]
PdfStatus = Literal["none", "queued", "ready", "failed"]
RegisterKind = Literal["transfer", "certificates"]


def _nfc(value: Any) -> Any:
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value).strip()
    return value


_NO_CONTROL = r"^[^\x00-\x1f\x7f]*$"
_NO_CONTROL_MULTILINE = r"^[^\x00-\x09\x0b-\x1f\x7f]*$"
InputKey = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,40}$")]
InputValue = Annotated[
    str, BeforeValidator(_nfc), StringConstraints(max_length=1000, pattern=_NO_CONTROL)
]
# Length rules (10..1000, config.yaml) are checked by the service with field-level codes.
Note = Annotated[
    str, BeforeValidator(_nfc), StringConstraints(max_length=1000, pattern=_NO_CONTROL_MULTILINE)
]


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class _Out(BaseModel):
    model_config = ConfigDict(from_attributes=True, frozen=True)


# --- inputs --------------------------------------------------------------------------------------


class CertificateRequest(_In):
    """Prepare (types that need approval) or issue a certificate for one student. ``inputs``
    are the values the type asks for (``GET /certificates/types``); dates as ``YYYY-MM-DD``."""

    certificate_type: CertificateType
    inputs: dict[InputKey, InputValue] = Field(default_factory=dict, max_length=20)


class DuplicateRequest(_In):
    """Why a duplicate is needed (10..1000 characters), e.g. the original was lost."""

    reason: Note


# SHA-256 (lowercase hex) of the draft the approver read (``CertificateOut.draft_sha256``).
DraftSha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


class ApproveIn(_In):
    """``draft_sha256``: the ``draft_sha256`` of the certificate as the approver read it (``GET
    /certificates/{id}``). If what the certificate would print changed since, approval is
    refused with 409 ``certificate_draft_changed`` (audit 2026-10-05 A-11)."""

    draft_sha256: DraftSha256
    note: Note | None = None


class ReasonIn(_In):
    """Reason for rejecting a request or cancelling an issued certificate (10..1000)."""

    reason: Note


# --- type catalog --------------------------------------------------------------------------------


class ChoiceOut(_Out):
    value: str
    label_en: str
    label_te: str


class InputOut(_Out):
    key: str
    # R2 formats: the input's own label (format inputs have no web catalogue entry). Optional in
    # the schema (default_factory) so older clients and fixtures stay valid.
    label_en: str = Field(default_factory=str)
    label_te: str = Field(default_factory=str)
    kind: Literal["date", "choice", "text", "number"]
    required: bool
    max_length: int | None
    # kind ``number``: the largest value accepted (0..max_value).
    max_value: int | None = None
    choices: list[ChoiceOut]


class CertificateTypeOut(_Out):
    key: CertificateType
    label_en: str
    label_te: str
    requires_approval: bool
    ends_enrolment: bool
    printed: list[str]
    inputs: list[InputOut]
    # Transfer certificates: the school's TC format (FR-CERT-015); ``null`` for other types.
    format_key: str | None = None
    format_version: int | None = None
    format_verified: bool | None = None


# --- preview -------------------------------------------------------------------------------------


PrintedFill = Literal["record", "input", "derived", "identifier", "restricted"]


def _record_fill() -> PrintedFill:
    return "record"


class PrintedField(_Out):
    """One value the certificate prints, with where it comes from (US-1101 AC1, AC4)."""

    key: str
    label_en: str
    label_te: str
    value: str | None
    source: str | None
    verified: bool
    provisional: bool
    # R2 formats (FR-CERT-015..018): ``record`` (a student value), ``input`` (typed when the TC
    # is prepared), ``derived`` (worked out from the record), ``identifier`` (PEN / APAAR ID under
    # their printing rule), ``restricted`` (religion, caste: blank unless the school prints them).
    fill: PrintedFill = Field(default_factory=_record_fill)
    # A restricted value the school prints but the caller may not see (no student.read_sensitive).
    masked: bool = Field(default_factory=bool)


class Blocker(_Out):
    """Why the certificate cannot be issued now (FR-CERT-002). ``finding_id`` links a DQ
    finding; ``attribute_key`` names the field (a change request corrects it)."""

    code: Literal[
        "dq_blocker",
        "missing_value",
        "no_enrolment",
        "student_not_active",
        "no_current_year",
        "transfer_certificate_exists",
        "change_request_pending",
    ]
    attribute_key: str | None = None
    finding_id: uuid.UUID | None = None
    rule_id: str | None = None


class PreviewWarning(_Out):
    code: Literal["provisional_value"]
    attribute_key: str


class TcFeeNote(_Out):
    """The student's outstanding fees on the TC preview, for staff information only (FR-CERT-025,
    US-2005; ADR-0042 decision 13; owner decision D16). Given only for a transfer certificate,
    while fees are on and to a caller holding ``finance.read`` school-wide. It is never printed,
    never stored on the certificate and never blocks issue or approval (FR-FEE-030)."""

    balance: Decimal = Field(
        max_digits=14, decimal_places=2, description="Outstanding balance of the current year."
    )
    overdue: Decimal = Field(
        max_digits=14, decimal_places=2, description="Part of the balance already overdue."
    )
    oldest_due_on: dt.date | None
    last_payment_on: dt.date | None
    account_id: uuid.UUID = Field(description="The fee account, to open it on the fee screens.")


class TcInputSuggestion(_Out):
    """A TC input SchoolOS suggests from its records (FT-08; FR-YRE-014..FR-YRE-016): shown as
    "From SchoolOS" next to the input, which the person may change. Never filled in silently:
    the request still carries what the person typed or accepted."""

    key: Literal[
        "promotion",
        "working_days",
        "days_attended",
        "application_date",
        "leaving_date",
        "leaving_reason",
        "conduct",
    ]
    value: str = Field(max_length=20)
    source: Literal["year_result", "attendance_register", "tc_request", "conduct_certificate"] = (
        Field(
            description="year_result: the confirmed annual result; attendance_register: days "
            "marked in the register up to today; tc_request: the open TC request in the request "
            "log (FO-09: the day it was received is the application date); conduct_certificate: "
            "the student's latest issued conduct certificate"
        )
    )


class CertificatePreview(_Out):
    student_id: uuid.UUID
    certificate_type: CertificateType
    requires_approval: bool
    fields: list[PrintedField]
    class_label: str | None
    academic_year_label: str | None
    blockers: list[Blocker]
    warnings: list[PreviewWarning]
    can_issue: bool
    # Transfer certificates: the format the TC would be prepared in (FR-CERT-015).
    format_key: str | None = None
    format_version: int | None = None
    # FR-CERT-025: information for finance readers only; never printed, never a blocker.
    fee_note: TcFeeNote | None = None
    # FT-08 (FR-YRE-014): "From SchoolOS" values for TC inputs the format asks for.
    input_suggestions: list[TcInputSuggestion] = Field(default_factory=list)


# --- certificates --------------------------------------------------------------------------------


class ContentLine(_Out):
    key: str
    label_en: str
    label_te: str
    value: str | None


class CertificateContent(_Out):
    """The printed values, frozen at issue (FR-CERT-003)."""

    certificate_type: CertificateType
    title_en: str
    title_te: str
    serial: str
    academic_year_label: str
    issued_on: dt.date
    school_name_en: str
    school_name_te: str
    school_address_en: str
    school_address_te: str
    school_affiliation: str
    school_place: str
    student_name: str
    admission_no: str
    class_label_en: str | None
    class_label_te: str | None
    fields: list[ContentLine]
    details: list[ContentLine]
    blanks: list[ContentLine]


class CertificateOut(_Out):
    id: uuid.UUID
    student_id: uuid.UUID
    certificate_type: CertificateType
    status: Status
    requires_approval: bool
    inputs: dict[str, str]
    # Transfer certificates: the format it was prepared in (``generic-v1`` for TCs from before
    # R2); ``null`` for other types (FR-CERT-015).
    format_key: str | None = None
    format_version: int | None = None
    original_certificate_id: uuid.UUID | None
    duplicate_no: int | None
    duplicate_reason: str | None
    academic_year_id: uuid.UUID | None
    serial: str | None
    student_name: str | None
    admission_no: str | None
    content: CertificateContent | None
    requested_by: uuid.UUID
    requested_at: dt.datetime
    decided_by: uuid.UUID | None
    decided_at: dt.datetime | None
    decision_note: str | None
    issued_by: uuid.UUID | None
    issued_at: dt.datetime | None
    cancelled_by: uuid.UUID | None
    cancelled_at: dt.datetime | None
    cancel_reason: str | None
    document_id: uuid.UUID | None
    pdf_status: PdfStatus
    version: int
    # A pending request read alone (GET /certificates/{id}): SHA-256 of what it would print if
    # approved now (serial number and issue date left out); the approval sends it back. ``null``
    # otherwise (lists, decided certificates, no current academic year).
    draft_sha256: str | None = None
    # For the screen: what the caller may do with it now.
    can_approve: bool
    can_withdraw: bool
    can_cancel: bool
    can_duplicate: bool


class DownloadUrlOut(_Out):
    url: str
    expires_at: dt.datetime
    filename: str


# --- R2-1A: formats and verification (ADR-0045; FR-CERT-015..024) ----------------------------

FormatFill = Literal["attribute", "input", "derived", "identifier", "restricted", "blank"]


class FormatSourceOut(_Out):
    title: str
    url: str | None


class FormatLineOut(_Out):
    key: str
    label_en: str
    label_te: str
    fill: FormatFill
    restricted: bool


class CertificateFormatOut(_Out):
    """A TC format a school may choose (``GET /certificates/formats``). ``verified`` is false
    until a school confirmed the layout (owner decision D3)."""

    key: str
    version: int
    certificate_type: Literal["transfer"]
    label_en: str
    label_te: str
    builtin: bool
    verified: bool
    sources: list[FormatSourceOut]
    notes: str
    lines: list[FormatLineOut]
    inputs: list[InputOut]


VerificationStatus = Literal["active", "withdrawn", "none"]


class VerificationOut(_Out):
    """The verification code of a certificate (``GET /certificates/{id}/verification``). ``code``
    is printed on the paper (four groups); ``url`` is the QR's link, ``null`` while verification
    is off (``SOS_VERIFY_BASE_URL`` unset). ``check_count`` counts successful public lookups of
    this code (FR-CERT-024); nothing else about who checked is kept. ``version`` is the ETag for
    a withdrawal."""

    certificate_id: uuid.UUID
    status: VerificationStatus
    code: str | None
    url: str | None
    created_reason: Literal["issued", "duplicate", "added_later"] | None
    created_at: dt.datetime | None
    check_count: int
    last_checked_at: dt.datetime | None
    withdrawn_at: dt.datetime | None
    withdraw_reason: str | None
    version: int
    can_add: bool
    can_withdraw: bool


class VerificationReasonIn(_In):
    """Why a code is added to a certificate issued before R2, or withdrawn (10..500 characters,
    no Aadhaar numbers)."""

    reason: Note


class PublicDuplicate(_Out):
    copy_no: int
    original_serial: str
    issued_on: dt.date


class PublicCertificateOut(_Out):
    """What the public verification page shows for a live code (FR-CERT-021; ADR-0045 decision
    5): the school's printed name and place, the type, serial and issue date, and for a valid
    certificate exactly its frozen printed content. A cancelled certificate (or a copy of a
    cancelled original) shows no content. Nothing comes from the live student record."""

    status: Literal["valid", "cancelled"]
    certificate_type: CertificateType
    title_en: str
    school_name: str
    school_place: str
    serial: str
    issued_on: dt.date
    duplicate: PublicDuplicate | None
    cancelled_on: dt.date | None
    content: CertificateContent | None


# --- TC request log (FO-09; FR-CERT-026..FR-CERT-045) ---------------------------------------------

TcRequestStatus = Literal["received", "in_preparation", "ready", "issued", "cancelled"]
TcRequestChannel = Literal["paper", "phone", "in_person", "other"]
LeavingReason = Literal["parent_request", "parent_transferred", "course_completed", "other"]


class TcRequestIn(_In):
    """Log a TC request that reached the office on paper, by phone or in person (FR-CERT-026).
    ``received_on`` (not in the future) becomes the TC's application date; ``leaving_date`` and
    ``leaving_reason`` are what the parent asked for and are suggested on the TC. Nothing about
    the person who asked is stored (least personal data)."""

    student_id: uuid.UUID
    received_on: dt.date
    channel: TcRequestChannel
    leaving_date: dt.date | None = None
    leaving_reason: LeavingReason | None = None


class TcRequestStatusIn(_In):
    """Move a request on (FR-CERT-030..FR-CERT-034): ``in_preparation`` (from received),
    ``issued`` (handed over; from ready) or ``cancelled`` (from any open state, with a 10..1000
    character ``reason``). ``ready`` is set by SchoolOS when the request's TC is issued."""

    status: Literal["in_preparation", "issued", "cancelled"]
    reason: Note | None = None


class TcRequestOut(_Out):
    """One TC request with its steps (who and when), how long it has been open and whether it is
    overdue (FR-CERT-035..FR-CERT-038). Staff names are members' display names; the student's
    name and admission number are the record's current values (C2)."""

    id: uuid.UUID
    student_id: uuid.UUID
    student_name: str | None
    admission_no: str | None
    class_label: str | None = Field(description="The class and section the student is in now.")
    status: TcRequestStatus
    received_on: dt.date
    channel: TcRequestChannel
    leaving_date: dt.date | None
    leaving_reason: LeavingReason | None
    certificate_id: uuid.UUID | None
    certificate_status: Status | None
    certificate_serial: str | None
    received_by: uuid.UUID
    received_by_name: str | None
    received_at: dt.datetime
    preparing_by: uuid.UUID | None
    preparing_by_name: str | None
    preparing_at: dt.datetime | None
    ready_by: uuid.UUID | None
    ready_by_name: str | None
    ready_at: dt.datetime | None
    issued_by: uuid.UUID | None
    issued_by_name: str | None
    issued_at: dt.datetime | None
    cancelled_by: uuid.UUID | None
    cancelled_by_name: str | None
    cancelled_at: dt.datetime | None
    cancel_reason: str | None
    days_open: int = Field(
        ge=0,
        description="Days from the day received to the day the TC was ready (or to today while "
        "it is still received or in preparation).",
    )
    overdue: bool = Field(
        description="Still received or in preparation more than overdue_after_days after it "
        "was received."
    )
    fee_note: TcFeeNote | None = Field(
        default=None,
        description="Outstanding fees, information only (FR-CERT-025, D16): only on one request "
        "read by a holder of finance.read school-wide while fees are on; never blocks the TC.",
    )
    can_prepare: bool
    can_hand_over: bool
    can_cancel: bool
    version: int


class TcRequestList(_Out):
    """A page of the request log, newest received first, with the school's overdue count among
    the requests the caller can see (FR-CERT-035, FR-CERT-037)."""

    data: list[TcRequestOut]
    next_cursor: str | None
    overdue_count: int = Field(ge=0)
    overdue_after_days: int = Field(ge=1)
    overdue_verified: bool = Field(
        description="false: the number of days is a default not yet confirmed by the school."
    )


__all__ = [
    "ApproveIn",
    "Blocker",
    "CertificateContent",
    "CertificateFormatOut",
    "CertificateOut",
    "CertificatePreview",
    "CertificateRequest",
    "CertificateType",
    "CertificateTypeOut",
    "ChoiceOut",
    "ContentLine",
    "DownloadUrlOut",
    "DuplicateRequest",
    "FormatFill",
    "FormatLineOut",
    "FormatSourceOut",
    "InputOut",
    "PdfStatus",
    "PreviewWarning",
    "PrintedField",
    "PublicCertificateOut",
    "PublicDuplicate",
    "ReasonIn",
    "RegisterKind",
    "Status",
    "TcRequestChannel",
    "TcRequestIn",
    "TcRequestList",
    "TcRequestOut",
    "TcRequestStatus",
    "TcRequestStatusIn",
    "VerificationOut",
    "VerificationReasonIn",
    "VerificationStatus",
]
