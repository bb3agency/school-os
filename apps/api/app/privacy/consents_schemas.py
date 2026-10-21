"""Pydantic v2 IO models for consents for optional purposes (docs/09 R3 "Consents for optional
purposes"; ADR-0049; FR-DPDP-020..026).

Inputs forbid unknown fields and NFC-normalise text. Responses carry ids, statuses, dates and the
C2 display fields the student screens already show; never an Aadhaar number (invariant 4).
"""

from __future__ import annotations

import datetime as dt
import unicodedata
import uuid
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

ConsentStatus = Literal["given", "refused", "pending", "withdrawn"]
# The current answer of one purpose: a recorded status, or nothing recorded yet.
PurposeAnswer = Literal["given", "refused", "pending", "withdrawn", "not_answered"]
ConsentState = Literal["given", "not_given"]
FormLanguage = Literal["en", "te"]
Relationship = Literal["father", "mother", "guardian"]


def _nfc(value: Any) -> Any:
    if isinstance(value, str):
        return " ".join(unicodedata.normalize("NFC", value).split())
    return value


def _nfc_keep_lines(value: Any) -> Any:
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value).strip()
    return value


_NO_CONTROL = r"^[^\x00-\x09\x0b-\x1f\x7f]*$"
Note = Annotated[
    str,
    BeforeValidator(_nfc_keep_lines),
    StringConstraints(min_length=1, max_length=500, pattern=_NO_CONTROL),
]
Label = Annotated[
    str, BeforeValidator(_nfc), StringConstraints(min_length=2, max_length=120, pattern=_NO_CONTROL)
]
Description = Annotated[
    str,
    BeforeValidator(_nfc_keep_lines),
    StringConstraints(min_length=10, max_length=1000, pattern=_NO_CONTROL),
]
PurposeKey = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{2,40}$")]


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class _Out(BaseModel):
    model_config = ConfigDict(from_attributes=True, frozen=True)


# --- purposes -------------------------------------------------------------------------------


class PurposeCreate(_In):
    """Enable a suggested purpose (``suggested_key`` only; its text is copied and may be edited
    later) or add the school's own (``key``, ``label_en``, ``description``; ``label_te``
    optional)."""

    suggested_key: PurposeKey | None = None
    key: PurposeKey | None = None
    label_en: Label | None = None
    label_te: Label | None = None
    description: Description | None = Field(
        default=None,
        description="What is used, why, who receives it and for how long (10-1000 characters).",
    )

    @model_validator(mode="after")
    def _one_kind(self) -> PurposeCreate:
        own = (self.key, self.label_en, self.description)
        if self.suggested_key is None and any(v is None for v in own):
            raise ValueError("give suggested_key, or key, label_en and description")
        return self


class PurposeUpdate(_In):
    """Edit the text or switch a purpose on or off. Changing the text of a purpose families have
    answered makes a new ``form_version``; earlier answers stay with their version."""

    label_en: Label | None = None
    label_te: Label | None = None
    clear_label_te: bool = False
    description: Description | None = None
    active: bool | None = None


class PurposeOut(_Out):
    id: uuid.UUID
    key: str
    label_en: str
    label_te: str | None
    description: str
    form_version: int
    active: bool
    suggested_key: str | None
    answered: int = Field(description="Decisions recorded for this purpose (all versions).")
    current_version_answered: bool = Field(
        description="True when a decision refers to the current form_version: a text change "
        "will make a new version."
    )
    version: int


class PurposeSuggestionOut(_Out):
    key: str
    label_en: str
    label_te: str | None
    description: str
    enabled: bool = Field(description="True when the school already has a purpose from it.")


class PurposeListOut(_Out):
    data: list[PurposeOut]
    suggestions: list[PurposeSuggestionOut]


# --- decisions ------------------------------------------------------------------------------


class PurposeConsentIn(_In):
    """A family's decision on one purpose as written on the returned form (FR-DPDP-021).

    ``given`` needs the signed form (``evidence_document_id``: a document uploaded with purpose
    ``evidence``). Every decision but ``pending`` names who decided (``relationship``, and
    ``guardian_id`` when that guardian is on the student's record) and the date on the form."""

    purpose_id: uuid.UUID
    status: ConsentStatus
    relationship: Relationship | None = None
    guardian_id: uuid.UUID | None = None
    decided_on: dt.date | None = None
    form_language: FormLanguage | None = None
    evidence_document_id: uuid.UUID | None = Field(
        default=None, description="The signed form, uploaded first as a document (evidence)."
    )
    note: Note | None = Field(
        default=None, description="Optional note (at most 500 characters; no Aadhaar numbers)."
    )


class PurposeConsentEntryOut(_Out):
    id: uuid.UUID
    purpose_id: uuid.UUID
    form_version: int
    seq: int
    status: ConsentStatus
    relationship: Relationship | None
    guardian_id: uuid.UUID | None
    decided_on: dt.date | None
    form_language: FormLanguage | None
    evidence_document_id: uuid.UUID | None
    note: str | None
    privacy_request_id: uuid.UUID | None
    recorded_by: uuid.UUID
    recorded_by_name: str | None
    recorded_at: dt.datetime


class PurposeStateOut(_Out):
    """One purpose for one student: the current answer and (in the register) the history."""

    purpose_id: uuid.UUID
    key: str
    label_en: str
    active: bool
    form_version: int
    answer: PurposeAnswer
    state: ConsentState = Field(
        description="given only when the latest decision is given; anything else (no decision, "
        "pending, refused, withdrawn, purpose switched off) is not_given (FR-DPDP-024)."
    )
    current: PurposeConsentEntryOut | None
    history: list[PurposeConsentEntryOut] = Field(default_factory=list)


class StudentConsentsOut(_Out):
    """Every purpose of the school for one student. ``version`` is the number of decisions
    recorded for the student (send it back as ``If-Match``)."""

    student_id: uuid.UUID
    purposes: list[PurposeStateOut]
    version: int


class ApaarStateOut(_Out):
    """The APAAR register's current decision (read-only here; recorded on /apaar)."""

    status: ConsentStatus
    decided_on: dt.date | None
    has_form: bool
    recorded_at: dt.datetime | None


class OptinCategoryOut(_Out):
    category: str
    status: str
    decided_on: dt.date | None


class GuardianOptinsBrief(_Out):
    guardian_id: uuid.UUID
    full_name: str | None
    relationship: str | None
    categories: list[OptinCategoryOut]


class WhatsappStateOut(_Out):
    """The guardians' WhatsApp opt-ins (read-only here; recorded in the opt-in panel)."""

    available: bool = Field(description="False when WhatsApp messaging is off on this host.")
    guardians: list[GuardianOptinsBrief]


class ConsentViewOut(_Out):
    """The one read-only consent view of a student (FR-DPDP-025): optional purposes, the APAAR
    decision and the guardians' WhatsApp opt-ins, each from its own register."""

    student_id: uuid.UUID
    purposes: list[PurposeStateOut]
    apaar: ApaarStateOut | None = Field(
        description="Null when the reader cannot see the APAAR register (apaar.consent.read)."
    )
    whatsapp: WhatsappStateOut | None = Field(
        description="Null when the reader cannot see opt-ins (message.read)."
    )


class AnswerCounts(_Out):
    total: int
    given: int
    refused: int
    pending: int
    withdrawn: int
    not_answered: int


class PurposeSectionCountsOut(_Out):
    section_id: uuid.UUID
    class_section: str
    counts: AnswerCounts


class PurposeSummaryOut(_Out):
    purpose_id: uuid.UUID
    key: str
    label_en: str
    totals: AnswerCounts
    sections: list[PurposeSectionCountsOut]


class PurposeConsentSummaryOut(_Out):
    """Counts per active purpose and section of the current academic year, for the students the
    caller may see (FR-DPDP-026). Counts only."""

    purposes: list[PurposeSummaryOut]


__all__ = [
    "AnswerCounts",
    "ApaarStateOut",
    "ConsentState",
    "ConsentStatus",
    "ConsentViewOut",
    "FormLanguage",
    "GuardianOptinsBrief",
    "OptinCategoryOut",
    "PurposeAnswer",
    "PurposeConsentEntryOut",
    "PurposeConsentIn",
    "PurposeConsentSummaryOut",
    "PurposeCreate",
    "PurposeListOut",
    "PurposeOut",
    "PurposeSectionCountsOut",
    "PurposeStateOut",
    "PurposeSuggestionOut",
    "PurposeSummaryOut",
    "PurposeUpdate",
    "StudentConsentsOut",
    "WhatsappStateOut",
]
