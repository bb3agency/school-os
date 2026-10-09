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
    kind: Literal["date", "choice", "text"]
    required: bool
    max_length: int | None
    choices: list[ChoiceOut]


class CertificateTypeOut(_Out):
    key: CertificateType
    label_en: str
    label_te: str
    requires_approval: bool
    ends_enrolment: bool
    printed: list[str]
    inputs: list[InputOut]


# --- preview -------------------------------------------------------------------------------------


class PrintedField(_Out):
    """One value the certificate prints, with where it comes from (US-1101 AC1, AC4)."""

    key: str
    label_en: str
    label_te: str
    value: str | None
    source: str | None
    verified: bool
    provisional: bool


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


__all__ = [
    "ApproveIn",
    "Blocker",
    "CertificateContent",
    "CertificateOut",
    "CertificatePreview",
    "CertificateRequest",
    "CertificateType",
    "CertificateTypeOut",
    "ChoiceOut",
    "ContentLine",
    "DownloadUrlOut",
    "DuplicateRequest",
    "InputOut",
    "PdfStatus",
    "PreviewWarning",
    "PrintedField",
    "ReasonIn",
    "RegisterKind",
    "Status",
]
