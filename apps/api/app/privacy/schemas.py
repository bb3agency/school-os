"""Pydantic v2 IO models for privacy notices, requests and record classes (docs/09 "R3: privacy";
ADR-0048; FR-DPDP-001..018).

Inputs forbid unknown fields and NFC-normalise text. Free texts (the requester's words, notes,
the response, nominee details) are returned only in the request detail, to readers who may see
the request; they are never logged or audited (FR-DPDP-007, PRV-033).
"""

from __future__ import annotations

import datetime as dt
import unicodedata
import uuid
from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, StringConstraints

from app.privacy.config import Erasure, LetterKey, TemplateKey

RequestKind = Literal[
    "access", "correction", "erasure", "nomination", "consent_withdrawal", "grievance", "other"
]
RequestStatus = Literal[
    "received", "in_progress", "waiting_for_requester", "decided", "closed", "withdrawn"
]
SubjectKindIn = Literal["student", "guardian", "member"]
Relationship = Literal["self", "father", "mother", "guardian", "nominee", "other"]
Verification = Literal["in_person_document", "guardian_phone_on_record", "staff_sign_in"]
Audience = Literal["parents", "staff"]
NoticeChannel = Literal["paper", "school_whatsapp_group", "sos_notice", "email", "other"]
NoticeLanguage = Literal["en", "te"]
LinkKind = Literal["change_request"]
ErasureOutcomeCode = Literal[
    "erasable", "refused_legal_obligation", "refused_official_record", "retention_running"
]


def _nfc(value: Any) -> Any:
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value).strip()
    return value


_NO_CONTROL = r"^[^\x00-\x09\x0b-\x1f\x7f]*$"
LongText = Annotated[
    str,
    BeforeValidator(_nfc),
    StringConstraints(min_length=1, max_length=4000, pattern=_NO_CONTROL),
]
ShortText = Annotated[
    str,
    BeforeValidator(_nfc),
    StringConstraints(min_length=1, max_length=120, pattern=r"^[^\x00-\x1f\x7f<>]*$"),
]


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class _Out(BaseModel):
    model_config = ConfigDict(from_attributes=True, frozen=True)


# --- templates and notices --------------------------------------------------------------------


class PrivacyTemplateOut(_Out):
    key: TemplateKey
    template_version: str
    audience: Audience
    languages: list[NoticeLanguage]
    placeholders: list[str]
    unfilled: list[str] = Field(
        description="Placeholders the school has not filled yet (shown as [not set])."
    )
    last_issued_version: str | None = Field(
        description="The template version of the latest notice issue recorded for the audience."
    )
    newer_version_available: bool = Field(
        description="A newer template version exists than the last one issued (FR-DPDP-003)."
    )


class PrivacyNoticeIn(_In):
    template_key: TemplateKey
    template_version: str = Field(pattern=r"^[0-9]{1,3}\.[0-9]{1,3}$")
    audience: Audience
    language: NoticeLanguage = "en"
    issued_on: dt.date
    channel: NoticeChannel
    document_id: uuid.UUID | None = Field(
        default=None, description="The final notice as issued, uploaded first as a document."
    )


class PrivacyNoticeOut(_Out):
    id: uuid.UUID
    template_key: TemplateKey
    template_version: str
    audience: Audience
    language: NoticeLanguage
    issued_on: dt.date
    channel: NoticeChannel
    document_id: uuid.UUID | None
    recorded_by_name: str | None
    recorded_at: dt.datetime


class PrivacyModuleFileOut(_Out):
    """A PDF made by the worker (access report or notice)."""

    export_id: uuid.UUID
    status: Literal["queued", "running", "ready", "failed", "expired"]
    error_code: str | None
    created_at: dt.datetime
    finished_at: dt.datetime | None
    expires_at: dt.datetime | None


class PrivacyDownloadOut(_Out):
    url: str
    expires_at: dt.datetime
    filename: str
    content_type: str


# --- record classes ---------------------------------------------------------------------------


class PrivacyRecordClassOut(_Out):
    key: str
    label: str
    owner: str
    tables: list[str]
    classification: Literal["C0", "C1", "C2", "C3"]
    retention: str
    basis: str
    erasure: Erasure
    source: str


class PrivacyRecordClassesOut(_Out):
    version: int
    data: list[PrivacyRecordClassOut]


# --- requests ---------------------------------------------------------------------------------


class PrivacySubjectIn(_In):
    """Exactly one of the three ids."""

    student_id: uuid.UUID | None = None
    guardian_id: uuid.UUID | None = None
    membership_id: uuid.UUID | None = None


class PrivacyRequestSearchIn(_In):
    kind: list[RequestKind] | None = Field(default=None, max_length=7)
    status: list[RequestStatus] | None = Field(default=None, max_length=6)
    overdue: bool = False
    subject: PrivacySubjectIn | None = None
    limit: int = Field(default=50, ge=1, le=200)
    cursor: str | None = Field(default=None, max_length=500)


class PrivacyRequestRowOut(_Out):
    id: uuid.UUID
    reference: str
    kind: RequestKind
    subject_kind: SubjectKindIn
    subject_label: str
    status: RequestStatus
    received_on: dt.date
    due_on: dt.date
    days_left: int = Field(description="Days until due (negative: overdue; IST dates).")
    overdue: bool


class PrivacyRequestPageOut(_Out):
    data: list[PrivacyRequestRowOut]
    next_cursor: str | None


class PrivacyRequestCreate(_In):
    kind: RequestKind
    subject: PrivacySubjectIn
    requester_relationship: Relationship
    requester_name: ShortText | None = Field(
        default=None, description="Who asked (encrypted; printed on letters)."
    )
    received_on: dt.date | None = Field(default=None, description="Default: today (IST).")
    request_text: LongText | None = Field(
        default=None, description="What they asked, in their words. Encrypted, never logged."
    )
    verification_method: Verification | None = None
    nominee_name: ShortText | None = None
    nominee_relationship: ShortText | None = None
    nominee_contact: ShortText | None = None


class PrivacyRequestPatch(_In):
    status: Literal["in_progress", "waiting_for_requester", "withdrawn"] | None = None
    notes: LongText | None = None
    verification_method: Verification | None = None
    response_text: LongText | None = None
    nomination_withdrawn: bool | None = Field(
        default=None, description="true: the nomination is withdrawn (nominations only)."
    )


class PrivacyEventOut(_Out):
    seq: int
    event: str
    detail: dict[str, Any]
    actor_name: str | None
    at: dt.datetime


class PrivacyLinkOut(_Out):
    link_kind: str
    target_id: uuid.UUID
    created_at: dt.datetime
    status: str | None = Field(
        default=None, description="The linked change request's status, when known."
    )


class PrivacyNomineeOut(_Out):
    name: str | None
    relationship: str | None
    contact: str | None
    withdrawn_at: dt.datetime | None


class PrivacyRequestOut(_Out):
    id: uuid.UUID
    reference: str
    kind: RequestKind
    subject_kind: SubjectKindIn
    student_id: uuid.UUID | None
    guardian_id: uuid.UUID | None
    membership_id: uuid.UUID | None
    subject_label: str
    requester_relationship: Relationship
    requester_name: str | None
    verification_method: Verification | None
    verified_at: dt.datetime | None
    received_on: dt.date
    due_on: dt.date
    days_left: int
    overdue: bool
    status: RequestStatus
    request_text: str | None
    notes: str | None
    response_text: str | None
    responded_on: dt.date | None
    nominee: PrivacyNomineeOut | None
    logged_by_name: str | None
    logged_by_me: bool
    decided_by_name: str | None
    decided_at: dt.datetime | None
    closed_at: dt.datetime | None
    report: PrivacyModuleFileOut | None
    report_released_at: dt.datetime | None
    can_decide: bool = Field(
        description="The caller holds privacy.request.decide and did not log the request."
    )
    events: list[PrivacyEventOut]
    links: list[PrivacyLinkOut]
    version: int


class PrivacyLinkIn(_In):
    kind: LinkKind
    target_id: uuid.UUID


class PrivacyErasurePlanRowOut(_Out):
    class_key: str
    label: str
    outcome: ErasureOutcomeCode
    reason: str
    until: dt.date | None = None
    count: int | None = Field(default=None, description="Records found for this subject.")
    manual_steps: list[str] = Field(default_factory=list)


class PrivacyErasurePlanOut(_Out):
    request_id: uuid.UUID
    rows: list[PrivacyErasurePlanRowOut]


class PrivacyErasureIn(_In):
    class_keys: list[Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{1,40}$")]] = Field(
        min_length=1, max_length=40
    )


class PrivacyErasureResultRowOut(_Out):
    class_key: str
    count: int
    manual_steps: list[str]


class PrivacyErasureResultOut(_Out):
    request_id: uuid.UUID
    erased: list[PrivacyErasureResultRowOut]
    manual_steps: list[str]


class PrivacyCloseIn(_In):
    response_text: LongText | None = Field(
        default=None, description="Required for grievances (FR-DPDP-016)."
    )


LetterQuery = LetterKey

__all__ = [
    "Audience",
    "LetterQuery",
    "PrivacyCloseIn",
    "PrivacyDownloadOut",
    "PrivacyErasureIn",
    "PrivacyErasurePlanOut",
    "PrivacyErasurePlanRowOut",
    "PrivacyErasureResultOut",
    "PrivacyErasureResultRowOut",
    "PrivacyEventOut",
    "PrivacyLinkIn",
    "PrivacyLinkOut",
    "PrivacyModuleFileOut",
    "PrivacyNomineeOut",
    "PrivacyNoticeIn",
    "PrivacyNoticeOut",
    "PrivacyRecordClassOut",
    "PrivacyRecordClassesOut",
    "PrivacyRequestCreate",
    "PrivacyRequestOut",
    "PrivacyRequestPageOut",
    "PrivacyRequestPatch",
    "PrivacyRequestRowOut",
    "PrivacyRequestSearchIn",
    "PrivacySubjectIn",
    "PrivacyTemplateOut",
    "RequestKind",
    "RequestStatus",
]
