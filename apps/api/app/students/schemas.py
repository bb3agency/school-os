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

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StringConstraints,
)

from app.authz.http import DEFAULT_LIMIT, MAX_LIMIT


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


# School calendar dates (academic years, enrolments): years 2000-2999 only. A date at the edge
# of the calendar (year 1 or 9999) reached the date maths and the database unchecked (audit
# 2026-10-06 hardening; the same bounds as the billing dates of R-13).
_SCHOOL_YEARS = (2000, 2999)


def _school_year(value: dt.date) -> dt.date:
    if not _SCHOOL_YEARS[0] <= value.year <= _SCHOOL_YEARS[1]:
        raise ValueError(f"must be between the years {_SCHOOL_YEARS[0]} and {_SCHOOL_YEARS[1]}")
    return value


SchoolDate = Annotated[dt.date, AfterValidator(_school_year)]


class EnrollmentIn(_In):
    """Enrol in a section; an active enrolment in the same academic year becomes ``transferred``."""

    section_id: uuid.UUID
    roll_no: RollNo | None = None
    started_on: SchoolDate | None = None


class EnrollmentPatch(_In):
    """Correct an enrolment: roll number (``null`` clears it) and/or the section, which must be
    another section of the same class in the same academic year (an active enrolment only).
    Omit a field to keep it. Moving to another class is a new enrolment, not a correction."""

    roll_no: RollNo | None = None
    section_id: uuid.UUID | None = None


EnrollmentEndStatus = Literal["completed", "transferred"]


class EnrollmentEnd(_In):
    """Close an active enrolment: ``completed`` (year finished, left the school) or
    ``transferred`` (moved elsewhere). ``ended_on`` defaults to today (India time)."""

    status: EnrollmentEndStatus = "completed"
    ended_on: SchoolDate | None = None


# --- promotions (FR-TEN-011, US-202 AC2) --------------------------------------------------------

PromotionOutcome = Literal["promoted", "held_back", "graduated", "skipped"]
PromotionSkipReason = Literal["left", "graduated", "already_enrolled"]
PromotionRunStatus = Literal["committed", "undone"]
MAX_PROMOTION_IDS = 5000


class SectionMapEntry(_In):
    """Send the students of ``from_section_id`` whose target class is the class of
    ``to_section_id`` to that section (default: the section with the same name)."""

    from_section_id: uuid.UUID
    to_section_id: uuid.UUID


class PromotionIn(_In):
    """Move this year's active enrolments into ``to_academic_year_id``: class N -> N+1 by class
    order, ``held_back_student_ids`` into the same class again, the final class graduates."""

    to_academic_year_id: uuid.UUID
    held_back_student_ids: list[uuid.UUID] = Field(
        default_factory=list, max_length=MAX_PROMOTION_IDS
    )
    section_map: list[SectionMapEntry] = Field(default_factory=list, max_length=500)


class PromotionCommitIn(PromotionIn):
    """The preview request plus, optionally, the preview's ``plan_fingerprint``: when sent, the
    commit is refused (409 ``promotion_plan_changed``) if enrolments changed since the preview."""

    plan_fingerprint: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")] | None = None


class PromotionStudentOut(_Out):
    student_id: uuid.UUID
    enrollment_id: uuid.UUID
    from_section_id: uuid.UUID
    outcome: PromotionOutcome
    to_section_id: uuid.UUID | None
    reason: str | None = Field(
        default=None,
        description="Why a student is skipped (left, graduated, already_enrolled) or cannot be "
        "placed (no_target_section).",
    )


class PromotionGroupOut(_Out):
    """Students moving from one section to one target section with one outcome."""

    from_section_id: uuid.UUID
    from_label: str
    outcome: PromotionOutcome
    to_section_id: uuid.UUID | None
    to_label: str | None
    count: int


class PromotionProblemOut(_Out):
    """Students who cannot be placed: add the section in the new year or map it."""

    code: Literal["no_target_section"]
    from_section_id: uuid.UUID
    from_label: str
    target_class_id: uuid.UUID
    count: int


class PromotionCounts(_Out):
    promoted: int
    held_back: int
    graduated: int
    skipped: int


class PromotionPreviewOut(_Out):
    from_academic_year_id: uuid.UUID
    to_academic_year_id: uuid.UUID
    counts: PromotionCounts
    groups: list[PromotionGroupOut]
    problems: list[PromotionProblemOut]
    students: list[PromotionStudentOut]
    plan_fingerprint: str
    can_commit: bool


class PromotionRunOut(_Out):
    id: uuid.UUID
    from_academic_year_id: uuid.UUID
    to_academic_year_id: uuid.UUID
    status: PromotionRunStatus
    counts: PromotionCounts
    plan_fingerprint: str
    committed_by: uuid.UUID | None
    committed_by_name: str | None = Field(
        default=None,
        description="Display name of who committed it (no contact details); null when they "
        "are no longer a member of this school.",
    )
    committed_at: dt.datetime
    undo_until: dt.datetime
    can_undo: bool = Field(description="Committed and still within 24 hours of the commit.")
    undone_by: uuid.UUID | None
    undone_by_name: str | None = Field(
        default=None, description="Display name of who undid it (no contact details)."
    )
    undone_at: dt.datetime | None
    version: int


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
    academic_year_id: uuid.UUID | None = None
    apaar_id: str | None = None


class StudentSearchIn(_In):
    """``POST /students/search`` body (SEC-008): names and admission numbers are personal data
    and never travel in the URL, where proxies and load balancers log them. Same filters, page
    size and cursor as ``GET /students``."""

    query: str | None = Field(
        default=None,
        max_length=200,
        description="Partial name (English or Telugu), parent name, admission number or "
        "class/section token such as 9b or IX-B.",
    )
    section_id: uuid.UUID | None = None
    class_id: uuid.UUID | None = None
    status: StudentStatus | None = None
    admission_no: str | None = Field(default=None, max_length=32)
    academic_year_id: uuid.UUID | None = Field(
        default=None,
        description="Academic year whose enrolments give the class and section shown and "
        "filtered on; the current year when left out. Scoped holders still see only students "
        "of their sections/classes this year. Unknown years answer 422.",
    )
    apaar_id: str | None = Field(
        default=None,
        max_length=32,
        description="Exact APAAR ID (FR-STU-016, ADR-0037): 12 digits, spaces or hyphens "
        "between the groups allowed. Matches only the student's current APAAR ID values "
        "(verified or recorded, not rejected), within the caller's scope; anything else "
        "answers 422 digits12_required. The only search field where a 12-digit number is "
        "accepted: query and admission_no still refuse one (aadhaar_full_number_rejected).",
    )
    limit: int = Field(
        default=DEFAULT_LIMIT, ge=1, le=MAX_LIMIT, description="Page size (max 200)."
    )
    cursor: str | None = Field(
        default=None, max_length=512, description="Opaque cursor from next_cursor."
    )

    def filters(self) -> SearchFilters:
        return SearchFilters(
            query=self.query,
            section_id=self.section_id,
            class_id=self.class_id,
            status=self.status,
            admission_no=self.admission_no,
            academic_year_id=self.academic_year_id,
            apaar_id=self.apaar_id,
        )


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
