"""Pydantic v2 IO models for tenancy (FR-TEN-003, FR-TEN-010).

Inputs forbid unknown fields and NFC-normalise + trim text before validation (docs/05 §1).
"""

from __future__ import annotations

import datetime as dt
import unicodedata
import uuid
from typing import Annotated, Any, Literal, Self

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)


def nfc(value: Any) -> Any:
    """NFC-normalise and trim strings; leave other types for the type validator to reject."""
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value).strip()
    return value


_NO_CONTROL = r"^[^\x00-\x1f\x7f]+$"

NfcName = Annotated[
    str, BeforeValidator(nfc), StringConstraints(min_length=1, max_length=200, pattern=_NO_CONTROL)
]
NfcLabel = Annotated[
    str, BeforeValidator(nfc), StringConstraints(min_length=1, max_length=100, pattern=_NO_CONTROL)
]
SectionName = Annotated[
    str, BeforeValidator(nfc), StringConstraints(min_length=1, max_length=16, pattern=_NO_CONTROL)
]
TenantCode = Annotated[
    str, BeforeValidator(nfc), StringConstraints(pattern=r"^[a-z][a-z0-9-]{1,31}$")
]
BoardCode = Annotated[
    str, BeforeValidator(nfc), StringConstraints(pattern=r"^[A-Z][A-Z0-9_]{1,15}$")
]
ClassCode = Annotated[
    str, BeforeValidator(nfc), StringConstraints(pattern=r"^[A-Z0-9][A-Z0-9_-]{0,15}$")
]
YearLabel = Annotated[str, BeforeValidator(nfc), StringConstraints(pattern=r"^\d{4}-\d{2}$")]

# School calendar dates (academic years, enrolments): years 2000-2999 only. A date at the edge
# of the calendar (year 1 or 9999) reached the date maths and the database unchecked (audit
# 2026-10-06 hardening; the same bounds as the billing dates of R-13).
_SCHOOL_YEARS = (2000, 2999)


def _school_year(value: dt.date) -> dt.date:
    if not _SCHOOL_YEARS[0] <= value.year <= _SCHOOL_YEARS[1]:
        raise ValueError(f"must be between the years {_SCHOOL_YEARS[0]} and {_SCHOOL_YEARS[1]}")
    return value


SchoolDate = Annotated[dt.date, AfterValidator(_school_year)]

SortOrder = Annotated[int, Field(ge=0, le=10000)]

TenantStatus = Literal["provisioning", "active", "suspended", "offboarding", "deleted"]
Tier = Literal["shared", "dedicated"]


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class _Out(BaseModel):
    model_config = ConfigDict(from_attributes=True, frozen=True)


# --- tenants -------------------------------------------------------------------------------


class TenantProvisionIn(_In):
    code: TenantCode
    name: NfcName
    boards: list[BoardCode] = Field(default_factory=list, max_length=8)
    plan_tier: Tier = "shared"
    deployment_mode: Tier = "shared"

    @field_validator("boards")
    @classmethod
    def _unique_boards(cls, boards: list[str]) -> list[str]:
        if len(set(boards)) != len(boards):
            raise ValueError("boards must be unique")
        return boards


class TenantProvisioned(_Out):
    tenant_id: uuid.UUID
    code: str
    status: TenantStatus
    key_version: int
    key_id: str


class TenantKeyVersion(_Out):
    """One wrapped key version of a school (never key material; SEC-012, 07 §8)."""

    key_version: int
    key_id: str
    created_at: dt.datetime
    retired_at: dt.datetime | None
    current: bool


class TenantStatusChange(_Out):
    tenant_id: uuid.UUID
    previous: TenantStatus
    current: TenantStatus


class TenantUsage(_Out):
    """Counts only (never personal data) for the control plane."""

    active_memberships: int
    users: int
    sections: int
    academic_years: int


class TenantDataCounts(_Out):
    """Offboarding (ADR-0029): rows per category (owning module, or table for what remains after
    a purge) and files of one school. Counts only."""

    rows: dict[str, int]
    objects: int


class TenantPurgeResult(_Out):
    """Rows per category removed by the offboarding purge, profiles cleared, files deleted."""

    rows: dict[str, int]
    profiles_cleared: int
    objects_deleted: int


class TenantKeysDestroyed(_Out):
    """Crypto-shredding result: wrapped key rows deleted and their versions (no key material)."""

    count: int
    key_versions: list[int]


# --- academic years ------------------------------------------------------------------------


def _check_year(label: str | None, starts_on: dt.date | None, ends_on: dt.date | None) -> None:
    if starts_on is not None and ends_on is not None and not starts_on < ends_on:
        raise ValueError("starts_on must be before ends_on")
    if label is not None:
        first, second = int(label[:4]), int(label[5:])
        if second != (first + 1) % 100:
            raise ValueError("label must name consecutive years, e.g. 2026-27")
        if starts_on is not None and starts_on.year != first:
            raise ValueError("label must start with the year of starts_on")


class AcademicYearCreate(_In):
    label: YearLabel
    starts_on: SchoolDate
    ends_on: SchoolDate
    is_current: bool = False

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        _check_year(self.label, self.starts_on, self.ends_on)
        return self


class AcademicYearUpdate(_In):
    label: YearLabel | None = None
    starts_on: SchoolDate | None = None
    ends_on: SchoolDate | None = None

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        _check_year(self.label, self.starts_on, self.ends_on)
        return self


class AcademicYearOut(_Out):
    id: uuid.UUID
    label: str
    starts_on: dt.date
    ends_on: dt.date
    is_current: bool
    archived_at: dt.datetime | None = Field(
        default=None, description="When it was archived (hidden from lists); null while in use."
    )
    in_use: bool | None = Field(
        default=None,
        description="Whether it has active enrolments, so archiving it answers 409 "
        "``structure_in_use`` (FR-TEN-010). Given by the list and get routes; null elsewhere.",
    )
    version: int
    created_at: dt.datetime
    updated_at: dt.datetime


# --- classes -------------------------------------------------------------------------------


class ClassCreate(_In):
    """``display_te`` is optional while Telugu is hidden (ADR-0036): left out, the English name
    is stored in its place. While Telugu is shown it is required (422)."""

    code: ClassCode
    display_en: NfcLabel
    display_te: NfcLabel | None = None
    sort_order: SortOrder


class ClassUpdate(_In):
    """The code is immutable (exports and registers refer to it)."""

    display_en: NfcLabel | None = None
    display_te: NfcLabel | None = None
    sort_order: SortOrder | None = None


class ClassOut(_Out):
    id: uuid.UUID
    code: str
    display_en: str
    display_te: str
    sort_order: int
    archived_at: dt.datetime | None = Field(
        default=None, description="When it was archived (hidden from lists); null while in use."
    )
    in_use: bool | None = Field(
        default=None,
        description="Whether it has active enrolments, so archiving it answers 409 "
        "``structure_in_use`` (FR-TEN-010). Given by the list and get routes; null elsewhere.",
    )
    version: int
    created_at: dt.datetime
    updated_at: dt.datetime


# --- sections ------------------------------------------------------------------------------


class SectionCreate(_In):
    academic_year_id: uuid.UUID
    class_id: uuid.UUID
    name: SectionName
    class_teacher_membership_id: uuid.UUID | None = None


class SectionUpdate(_In):
    """Omit a field to keep it; send ``class_teacher_membership_id: null`` to clear it."""

    name: SectionName | None = None
    class_teacher_membership_id: uuid.UUID | None = None


class SectionOut(_Out):
    id: uuid.UUID
    academic_year_id: uuid.UUID
    class_id: uuid.UUID
    name: str
    class_teacher_membership_id: uuid.UUID | None
    archived_at: dt.datetime | None = Field(
        default=None, description="When it was archived (hidden from lists); null while in use."
    )
    in_use: bool | None = Field(
        default=None,
        description="Whether it has active enrolments, so archiving it answers 409 "
        "``structure_in_use`` (FR-TEN-010). Given by the list and get routes; null elsewhere.",
    )
    version: int
    created_at: dt.datetime
    updated_at: dt.datetime


# --- school settings (FR-TEN-012) -------------------------------------------------------------

DateFormat = Literal["DD/MM/YYYY", "DD-MM-YYYY", "YYYY-MM-DD"]
Language = Literal["en", "te"]
# FR-TEN-020 (ADR-0041): the boards SchoolOS has profiles for (AP State Board, CBSE, CISCE).
SupportedBoard = Literal["BSEAP", "CBSE", "CISCE"]
SUPPORTED_BOARDS: tuple[str, ...] = ("BSEAP", "CBSE", "CISCE")
OperatingMode = Literal["full", "alongside"]
ErpName = Annotated[
    str, BeforeValidator(nfc), StringConstraints(max_length=80, pattern=r"^[^\x00-\x1f\x7f<>]*$")
]


def _default_languages() -> list[Language]:
    return ["en", "te"]


def _unique_languages(v: list[Language] | None) -> list[Language] | None:
    if v is not None and len(set(v)) != len(v):
        raise ValueError("languages must be unique")
    return v


LetterheadText = Annotated[
    str, BeforeValidator(nfc), StringConstraints(max_length=300, pattern=r"^[^\x00-\x1f\x7f]*$")
]


class CertificateLetterhead(BaseModel):
    """What certificates print at the top and at the signature (FR-CERT-013, US-1108). The
    English school name is the school's name; empty fields are left out of the page."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    school_name_te: LetterheadText = Field(default="", max_length=200)
    address_en: LetterheadText = ""
    address_te: LetterheadText = ""
    affiliation: LetterheadText = Field(
        default="",
        max_length=200,
        description="Recognition or affiliation line, e.g. the recognition order or UDISE code.",
    )
    place: LetterheadText = Field(default="", max_length=80)


# --- R2-1A certificates (ADR-0045; FR-CERT-015, FR-CERT-018) ---------------------------------
# TC format keys a school may choose: the built-in generic format and the versioned format files
# in app/certificates/formats/ (kept equal by tests/certificates/test_formats.py).
TcFormatKey = Literal["generic-v1", "ap-tc-2027"]
# Restricted (C3) attributes a TC prints only with print_restricted_tc_fields (owner decision D15);
# audited with these keys when the setting changes.
RESTRICTED_TC_FIELDS: tuple[str, ...] = ("caste", "religion")


class CertificateFormatChoice(BaseModel):
    """The certificate format a school uses per certificate type (only the TC has a choice)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    transfer: TcFormatKey = Field(
        default="generic-v1",
        description="``generic-v1`` (built in) or ``ap-tc-2027`` (Andhra Pradesh layout, built "
        "from public sources and not yet confirmed with a school; ADR-0045).",
    )


# --- end R2-1A ----------------------------------------------------------------------------------


# --- R3-1A privacy contact (ADR-0048 decision 3; FR-DPDP-004, FR-DPDP-006) ----------------------
ContactName = Annotated[
    str,
    BeforeValidator(nfc),
    StringConstraints(min_length=2, max_length=120, pattern=r"^[^\x00-\x1f\x7f<>]*$"),
]
# E.164 (+91...) or an Indian landline with its STD code (e.g. 0863-2345678).
ContactPhone = Annotated[
    str,
    BeforeValidator(nfc),
    StringConstraints(pattern=r"^(\+[1-9][0-9]{7,14}|0[1-9][0-9]{1,4}[- ]?[0-9]{5,8})$"),
]
ContactEmail = Annotated[
    str,
    BeforeValidator(nfc),
    StringConstraints(max_length=254, pattern=r"^[^@\s<>]{1,64}@[^@\s<>]+\.[A-Za-z]{2,}$"),
]


class PrivacyContact(BaseModel):
    """The school's grievance and data-protection contact, printed on notices, letters and
    access reports and shown in the staff footer (PRV-011)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: ContactName = Field(description="A name or a designation such as 'The Principal'.")
    phone: ContactPhone | None = None
    email: ContactEmail | None = None


# --- end R3-1A ----------------------------------------------------------------------------------


class TenantSettings(BaseModel):
    """Validated school settings stored in ``core.tenants.settings`` (FR-TEN-012).

    Other keys in the stored object (e.g. retention rules, ``/admin/retention`` in M1) are kept
    untouched when these settings change.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    languages: list[Language] = Field(
        default_factory=_default_languages, min_length=1, max_length=2
    )
    date_format: DateFormat = "DD/MM/YYYY"
    idle_timeout_minutes: int = Field(default=15, ge=5, le=30)
    ai_features_enabled: bool = True
    ai_monthly_budget_inr: int = Field(default=5000, ge=0, le=10_000_000)
    ai_memory_enabled: bool = Field(
        default=True,
        description="Ask may remember each person's own preferences and work context "
        "(ADR-0034). Off: nothing is saved, suggested or used for anyone in the school.",
    )
    certificate_letterhead: CertificateLetterhead = Field(default_factory=CertificateLetterhead)
    class_boards: dict[ClassCode, SupportedBoard] = Field(
        default_factory=dict,
        max_length=20,
        description="Board of each class code where the school runs more than one board "
        "(FR-TEN-021); classes not listed follow the school's only board.",
    )
    operating_mode: OperatingMode = Field(
        default="full",
        description="``alongside``: SchoolOS is the records-and-readiness layer next to the "
        "school's current ERP, which keeps fees and other office work (FR-TEN-022, ADR-0041).",
    )
    current_erp_name: ErpName = Field(
        default="",
        description="Name of the school's current ERP, shown on the refresh-from-ERP path.",
    )
    parent_language: Language = Field(
        default="en",
        description="Language of everything parents receive: WhatsApp messages, parent link "
        "pages, printed parent slips and forms (ADR-0044, FR-LANG-001). Independent of the "
        "staff UI language; a guardian's own choice overrides it.",
    )
    # R2-1A certificates (ADR-0045).
    certificate_format: CertificateFormatChoice = Field(
        default_factory=CertificateFormatChoice,
        description="Certificate format per type (FR-CERT-015); issued certificates keep the "
        "format they were issued in.",
    )
    print_restricted_tc_fields: bool = Field(
        default_factory=bool,  # False; optional in the schema, like certificate_format
        description="Print religion and caste on transfer certificates instead of labelled "
        "blanks (FR-CERT-018, owner decision D15; an exception to FR-CERT-009 for the TC only). "
        "Audited with the field keys.",
    )
    fees_enabled: bool = Field(
        default_factory=bool,  # False; optional in the schema
        description="The school keeps its fee ledger and receipts in SchoolOS (R2-1B, FR-FEE-001; "
        "also needs the platform flag ``fees.enabled``). In ``alongside`` mode this is how the "
        "owner turns fees on despite the ERP (ADR-0042 decision 12). Audited with its value.",
    )
    # R3-1A (ADR-0048): privacy contact and the target for answering requests.
    privacy_contact: PrivacyContact | None = Field(
        default=None,
        description="The school's privacy and grievance contact (FR-DPDP-004); audited with "
        "field names only.",
    )
    privacy_request_target_days: int = Field(
        default=7,
        ge=1,
        le=7,
        description="Days the school gives itself to answer a privacy request (1-7; NFR-PRV-004).",
    )

    @field_validator("languages")
    @classmethod
    def _languages(cls, v: list[Language]) -> list[Language]:
        _unique_languages(v)
        return v


class TenantSettingsPatch(_In):
    """Send only the settings to change."""

    languages: list[Language] | None = Field(default=None, min_length=1, max_length=2)
    date_format: DateFormat | None = None
    idle_timeout_minutes: int | None = Field(default=None, ge=5, le=30)
    ai_features_enabled: bool | None = None
    ai_monthly_budget_inr: int | None = Field(default=None, ge=0, le=10_000_000)
    ai_memory_enabled: bool | None = None
    certificate_letterhead: CertificateLetterhead | None = None
    boards: list[SupportedBoard] | None = Field(
        default=None,
        max_length=3,
        description="The boards the school prepares students for (FR-TEN-020); stored in "
        "``core.tenants.boards``. Board pre-check profiles are listed for these boards only.",
    )
    class_boards: dict[ClassCode, SupportedBoard] | None = Field(default=None, max_length=20)
    operating_mode: OperatingMode | None = None
    current_erp_name: ErpName | None = None
    parent_language: Language | None = Field(
        default=None,
        description="``en`` or ``te``: the language parents receive (ADR-0044). Audited with "
        "its new value.",
    )
    # R2-1A certificates (ADR-0045). Both audited with their new values.
    certificate_format: CertificateFormatChoice | None = None
    print_restricted_tc_fields: bool | None = None
    fees_enabled: bool | None = Field(
        default=None,
        description="Turn the fee ledger and receipts on or off for the school (FR-FEE-001). "
        "Recorded data is kept when it is turned off.",
    )
    # R3-1A (ADR-0048). Both audited with field names only.
    privacy_contact: PrivacyContact | None = None
    privacy_request_target_days: int | None = Field(default=None, ge=1, le=7)

    @field_validator("languages")
    @classmethod
    def _languages(cls, v: list[Language] | None) -> list[Language] | None:
        return _unique_languages(v)

    @field_validator("boards")
    @classmethod
    def _unique_boards(cls, v: list[str] | None) -> list[str] | None:
        if v is not None and len(set(v)) != len(v):
            raise ValueError("boards must be unique")
        return v


class TenantOut(_Out):
    id: uuid.UUID
    code: str
    name: str
    boards: list[str]
    state_code: str
    status: TenantStatus
    plan_tier: Tier
    deployment_mode: Tier
    settings: TenantSettings
    modules_hidden: list[str] = Field(
        default_factory=list,
        description="Modules the school's operating mode hides, e.g. ``tally`` while SchoolOS "
        "runs alongside another ERP (FR-TEN-022).",
    )
    parent_language_unreviewed: list[str] = Field(
        default_factory=list,
        description="Parent-facing Telugu texts the school would use that a fluent reviewer "
        "has not signed off yet (FR-LANG-005); empty while the parent language is English.",
    )
    version: int
