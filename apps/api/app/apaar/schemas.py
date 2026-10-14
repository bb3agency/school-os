"""Pydantic v2 IO models for the APAAR consent register (docs/09 APAAR consent; ADR-0039).

Inputs forbid unknown fields and NFC-normalise text. Responses carry ids, statuses, dates and
the C2 display fields the student list already shows; never an Aadhaar number (invariant 4).
"""

from __future__ import annotations

import datetime as dt
import unicodedata
import uuid
from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, StringConstraints

ConsentStatus = Literal["given", "refused", "pending", "withdrawn"]
FormLanguage = Literal["en", "te"]
Relationship = Literal["father", "mother", "guardian"]


def _nfc(value: Any) -> Any:
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value).strip()
    return value


Note = Annotated[
    str,
    BeforeValidator(_nfc),
    StringConstraints(min_length=1, max_length=500, pattern=r"^[^\x00-\x09\x0b-\x1f\x7f]*$"),
]


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class _Out(BaseModel):
    model_config = ConfigDict(from_attributes=True, frozen=True)


class ConsentIn(_In):
    """A parent's decision as written on the returned form (FR-APC-001..003).

    ``given`` needs the signed form (``evidence_document_id``: a document uploaded with purpose
    ``evidence``); ``refused`` and ``withdrawn`` may carry it. Every decision names who decided
    (``relationship``, and ``guardian_id`` when that guardian is on the student's record) and the
    date on the form (``decided_on``). ``pending`` records that a form went home and is awaited.
    """

    status: ConsentStatus
    relationship: Relationship | None = None
    guardian_id: uuid.UUID | None = None
    decided_on: dt.date | None = None
    form_language: FormLanguage | None = Field(
        default=None, description="Language of the form the parent signed (en or te)."
    )
    evidence_document_id: uuid.UUID | None = Field(
        default=None, description="The signed form, uploaded first as a document (evidence)."
    )
    note: Note | None = Field(
        default=None, description="Optional note (at most 500 characters; no Aadhaar numbers)."
    )


class ConsentEntryOut(_Out):
    id: uuid.UUID
    seq: int
    status: ConsentStatus
    relationship: Relationship | None
    guardian_id: uuid.UUID | None
    decided_on: dt.date | None
    form_language: FormLanguage | None
    evidence_document_id: uuid.UUID | None
    note: str | None
    recorded_by: uuid.UUID
    recorded_by_name: str | None
    recorded_at: dt.datetime


class StudentConsentOut(_Out):
    """Current state (``pending`` when nothing was recorded) and the full history, newest first.
    ``version`` is the number of recorded decisions (send it back as ``If-Match``)."""

    student_id: uuid.UUID
    status: ConsentStatus
    current: ConsentEntryOut | None
    history: list[ConsentEntryOut]
    version: int


class ConsentRowOut(_Out):
    """One student of the register list (FR-APC-005)."""

    student_id: uuid.UUID
    display_name: str | None
    admission_no: str | None
    class_section: str | None
    section_id: uuid.UUID | None
    status: ConsentStatus
    decided_on: dt.date | None
    has_form: bool
    recorded_at: dt.datetime | None
    version: int
    has_apaar_id: bool


class StatusCounts(_Out):
    total: int
    given: int
    refused: int
    pending: int
    withdrawn: int


class SectionSummaryOut(_Out):
    section_id: uuid.UUID
    class_section: str
    counts: StatusCounts


class ConsentSummaryOut(_Out):
    """Given / refused / pending / withdrawn per section of the current academic year, for the
    students the caller may see (FR-APC-005). Students with no decision count as pending."""

    totals: StatusCounts
    sections: list[SectionSummaryOut]


class ApaarSettingsOut(_Out):
    form_language: FormLanguage
    version: int


class ApaarSettingsIn(_In):
    form_language: FormLanguage


__all__ = [
    "ApaarSettingsIn",
    "ApaarSettingsOut",
    "ConsentEntryOut",
    "ConsentIn",
    "ConsentRowOut",
    "ConsentStatus",
    "ConsentSummaryOut",
    "FormLanguage",
    "SectionSummaryOut",
    "StatusCounts",
    "StudentConsentOut",
]
