"""Pydantic v2 IO models for register-photo extraction (US-402, docs/09 Imports and extraction).

Inputs forbid unknown fields and NFC-normalise text. Extracted values in responses are already
masked (Aadhaar-like numbers as ``XXXX XXXX 1234``); nothing here carries page text.
"""

from __future__ import annotations

import datetime as dt
import unicodedata
import uuid
from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, StringConstraints

from app.students.schemas import StudentSummary


def _nfc(value: Any) -> Any:
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value).strip()
    return value


FieldKey = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{1,63}$")]
FieldValue = Annotated[
    str,
    BeforeValidator(_nfc),
    StringConstraints(max_length=200, pattern=r"^[^\x00-\x1f\x7f]*$"),
]
RollNo = Annotated[
    str,
    BeforeValidator(_nfc),
    StringConstraints(min_length=1, max_length=16, pattern=r"^[^\x00-\x1f\x7f]*$"),
]
BatchStatus = Literal["queued", "processing", "review", "completed", "failed"]
PageStatus = Literal["queued", "done", "failed"]
ItemStatus = Literal["pending_review", "confirmed", "rejected"]
RejectReason = Literal["not_a_student_row", "duplicate", "unreadable", "other"]
StudentStatus = Literal["provisional", "active", "left", "graduated"]
ImageUnavailable = Literal["withheld_sensitive_number", "not_visible", "not_ready"]


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class _Out(BaseModel):
    model_config = ConfigDict(from_attributes=True, frozen=True)


# --- inputs ---------------------------------------------------------------------------------


class BatchCreate(_In):
    """Start extraction for register-page photos already uploaded as ``register_scan``
    documents (JPG or PNG, one page each, malware scan passed)."""

    document_ids: list[uuid.UUID] = Field(min_length=1, max_length=50)


class ItemConfirm(_In):
    """The values a person read on the page (edited where the machine was wrong).

    Keys are register fields (``admission_no``, ``full_name``, ``dob`` (YYYY-MM-DD), ``gender``,
    ``father_name``, ``mother_name``, ``admission_date``, ``mother_tongue``, ``nationality``); an
    empty or ``null`` value is not recorded. Send ``student_id`` to add the values to an
    existing student; otherwise a new student is created (``full_name`` required), optionally
    enrolled in ``section_id``.
    """

    fields: dict[FieldKey, FieldValue | None] = Field(min_length=1, max_length=20)
    student_id: uuid.UUID | None = None
    section_id: uuid.UUID | None = None
    roll_no: RollNo | None = None
    student_status: StudentStatus = "active"


class ItemReject(_In):
    reason: RejectReason


# --- outputs --------------------------------------------------------------------------------


class FieldOut(_Out):
    value: str
    confidence: float | None
    bbox: list[float] | None
    masked: bool
    low_confidence: bool


class PageOut(_Out):
    """One register page. ``aadhaar_detected``: its text showed a full Aadhaar number (PRV-016);
    the image is then either ``image_redacted`` (number blacked out; ``document_version_no`` is
    the redacted copy) or ``image_withheld`` (could not be redacted: the original was discarded
    and the page's rows cannot be confirmed)."""

    id: uuid.UUID
    document_id: uuid.UUID
    document_version_no: int
    page_no: int
    seq: int
    status: PageStatus
    error_code: str | None
    aadhaar_detected: bool
    image_withheld: bool
    image_redacted: bool
    row_count: int
    low_confidence_count: int
    processed_at: dt.datetime | None


class BatchOut(_Out):
    id: uuid.UUID
    source: str
    status: BatchStatus
    provider: str
    error_code: str | None
    page_count: int
    pages_done: int
    pages_failed: int
    pages_withheld: int
    items_total: int
    items_pending: int
    items_confirmed: int
    items_rejected: int
    items_low_confidence: int
    created_by: uuid.UUID
    created_at: dt.datetime
    processed_at: dt.datetime | None
    completed_at: dt.datetime | None
    version: int


class BatchDetail(BatchOut):
    """A batch with progress per page (US-402 AC4)."""

    pages: list[PageOut]


class ItemOut(_Out):
    id: uuid.UUID
    batch_id: uuid.UUID
    page_id: uuid.UUID
    document_id: uuid.UUID
    page_no: int
    row_index: int
    status: ItemStatus
    fields: dict[str, FieldOut]
    low_confidence: bool
    low_confidence_fields: list[str]
    masked: bool
    reviewed_by: uuid.UUID | None
    reviewed_at: dt.datetime | None
    reject_reason: RejectReason | None
    student_id: uuid.UUID | None
    value_ids: list[uuid.UUID]
    corrected_fields: list[str]
    created_student: bool
    version: int


class PageImage(_Out):
    url: str
    expires_at: dt.datetime
    mime_type: str


class ItemDetail(ItemOut):
    """One row for review with its page image beside it (US-402 AC1).

    ``image`` is a presigned link valid for 5 minutes, or ``null`` with ``image_unavailable``:
    ``withheld_sensitive_number`` (the page showed a full Aadhaar number, PRV-016),
    ``not_visible`` (you may not open the document) or ``not_ready``. ``possible_matches`` are
    students of this school with the same admission number (link instead of creating twice).
    """

    image: PageImage | None
    image_unavailable: ImageUnavailable | None
    possible_matches: list[StudentSummary]
