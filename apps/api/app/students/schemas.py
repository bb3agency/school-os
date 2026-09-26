"""Pydantic v2 IO models for students (FR-STU-001..012, docs/09 Students).

Inputs forbid unknown fields (no mass assignment) and NFC-normalise + trim text. Values are
strings on the wire (dates as ``YYYY-MM-DD``); the attribute definition validates and converts
them in the service, so the same rules apply to API, import and extraction writes. C3 values are
never part of a list/detail response: they appear masked (``"••••"``) for callers who may reveal
them, and are omitted for everyone else.
"""

from __future__ import annotations

import datetime as dt
import unicodedata
import uuid
from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, StringConstraints


def nfc(value: Any) -> Any:
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value).strip()
    return value


_NO_CONTROL = r"^[^\x00-\x1f\x7f]*$"

Source = Literal[
    "admission_register",
    "aadhaar_as_printed",
    "udise_plus",
    "board_registration",
    "birth_certificate",
    "parent_form",
    "tc_incoming",
    "manual_entry",
]
StudentStatus = Literal["provisional", "active", "left", "graduated"]
Relationship = Literal["father", "mother", "guardian"]
Verification = Literal["unverified", "verified", "rejected"]
VerifyDecision = Literal["verified", "rejected"]
AttributeKey = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{1,63}$")]
RawValue = Annotated[
    str,
    BeforeValidator(nfc),
    StringConstraints(min_length=1, max_length=1000, pattern=_NO_CONTROL),
]
PersonName = Annotated[
    str,
    BeforeValidator(nfc),
    StringConstraints(min_length=1, max_length=120, pattern=_NO_CONTROL),
]
ShortText = Annotated[
    str, BeforeValidator(nfc), StringConstraints(min_length=1, max_length=500, pattern=_NO_CONTROL)
]
PhoneText = Annotated[
    str, BeforeValidator(nfc), StringConstraints(min_length=6, max_length=20, pattern=_NO_CONTROL)
]
RollNo = Annotated[
    str, BeforeValidator(nfc), StringConstraints(min_length=1, max_length=16, pattern=_NO_CONTROL)
]


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class _Out(BaseModel):
    model_config = ConfigDict(from_attributes=True, frozen=True)


# --- attribute catalog ------------------------------------------------------------------------


class AttributeOut(_Out):
    key: str
    data_type: str
    classification: str
    is_identity: bool
    label_en: str
    label_te: str
    sort_order: int
    allowed_sources: list[str] | None
    allowed_values: list[str] | None
    precedence: list[str]
    is_global: bool


# --- values -------------------------------------------------------------------------------------


class ValueIn(_In):
    """One observed value from one source (FR-STU-002). Identity attributes from the admission
    register cannot be changed here once recorded: use a change request."""

    attribute_key: AttributeKey
    source: Source
    value: RawValue
    evidence_document_id: uuid.UUID | None = None


class VerifyIn(_In):
    status: VerifyDecision = "verified"


class ValueOut(_Out):
    id: uuid.UUID
    attribute_key: str
    source: str
    value: str | None
    masked: bool
    verification_status: Verification
    verified_by: uuid.UUID | None
    verified_at: dt.datetime | None
    recorded_by: uuid.UUID
    recorded_at: dt.datetime
    evidence_document_id: uuid.UUID | None
    import_batch_id: uuid.UUID | None
    change_request_id: uuid.UUID | None
    superseded_by: uuid.UUID | None
    current: bool


class CanonicalOut(_Out):
    value: str | None
    source: str | None
    verified: bool
    provisional: bool
    masked: bool
    conflicts: list[str]


# --- students -----------------------------------------------------------------------------------


class StudentCreate(_In):
    """New student with its first values (docs/09: source required per value).

    ``values`` must include ``full_name``. ``section_id`` enrols the student in that section's
    academic year.
    """

    values: list[ValueIn] = Field(min_length=1, max_length=40)
    status: StudentStatus = "active"
    section_id: uuid.UUID | None = None
    roll_no: RollNo | None = None


class StudentPatch(_In):
    status: StudentStatus


class EnrollmentOut(_Out):
    id: uuid.UUID
    student_id: uuid.UUID
    section_id: uuid.UUID
    academic_year_id: uuid.UUID
    roll_no: str | None
    status: str
    started_on: dt.date | None
    ended_on: dt.date | None
    version: int


class EnrollmentIn(_In):
    """Enrol in a section; an active enrolment in the same academic year becomes ``transferred``."""

    section_id: uuid.UUID
    roll_no: RollNo | None = None
    started_on: dt.date | None = None


class ClassSection(_Out):
    section_id: uuid.UUID
    class_id: uuid.UUID
    academic_year_id: uuid.UUID
    label: str
    roll_no: str | None = None


class StudentOut(_Out):
    """Canonical profile + current per-source values (US-301)."""

    id: uuid.UUID
    status: str
    admission_no: str | None
    version: int
    created_at: dt.datetime
    updated_at: dt.datetime
    enrollment: ClassSection | None
    canonical: dict[str, CanonicalOut]
    values: dict[str, list[ValueOut]]
    sensitive_revealable: bool


class StudentMatch(_Out):
    field: str | None
    score: float | None


class StudentSummary(_Out):
    id: uuid.UUID
    display_name: str | None
    admission_no: str | None
    status: str
    class_section: str | None
    section_id: uuid.UUID | None
    match: StudentMatch


class ValueRecorded(_Out):
    id: uuid.UUID
    student_id: uuid.UUID
    attribute_key: str
    source: str
    superseded: uuid.UUID | None
    student_version: int


class SearchFilters(_In):
    query: str | None = None
    section_id: uuid.UUID | None = None
    class_id: uuid.UUID | None = None
    status: StudentStatus | None = None
    admission_no: str | None = None


# --- sensitive reveal ---------------------------------------------------------------------------


class RevealIn(_In):
    """Reveal one C3 field (audited). ``guardian_phone``/``guardian_address`` need
    ``guardian_id``; ``value_id`` reveals a specific (possibly historical) value."""

    attribute_key: AttributeKey
    value_id: uuid.UUID | None = None
    guardian_id: uuid.UUID | None = None


class RevealOut(_Out):
    attribute_key: str
    source: str | None
    value: str | None
    display: str | None
    value_id: uuid.UUID | None
    guardian_id: uuid.UUID | None


# --- guardians ----------------------------------------------------------------------------------


class GuardianCreate(_In):
    """Add a new guardian (``full_name`` ...) or link an existing one (``guardian_id``)."""

    relationship: Relationship
    is_primary: bool = False
    guardian_id: uuid.UUID | None = None
    full_name: PersonName | None = None
    phone: PhoneText | None = None
    address: ShortText | None = None


class GuardianPatch(_In):
    """Omit a field to keep it; ``phone``/``address`` ``null`` clears it."""

    full_name: PersonName | None = None
    phone: PhoneText | None = None
    address: ShortText | None = None
    relationship: Relationship | None = None
    is_primary: bool | None = None


class GuardianOut(_Out):
    id: uuid.UUID
    full_name: str
    relationship: str
    is_primary: bool
    has_phone: bool
    has_address: bool
    phone: str | None
    address: str | None
    masked: bool
    version: int
