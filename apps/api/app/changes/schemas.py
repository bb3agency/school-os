"""Pydantic v2 IO models for change requests (FR-CR-001..005, docs/09 Change requests).

Inputs forbid unknown fields and NFC-normalise + trim text. The requested value is a string on
the wire (``new_value``; dates as ``YYYY-MM-DD``) or, for dates, ``new_value_date`` (docs/09
§5.3); the attribute definition validates it in the service. Values of C3 attributes are never
returned in clear: ``masked: true`` and ``"••••"``.
"""

from __future__ import annotations

import datetime as dt
import unicodedata
import uuid
from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, BeforeValidator, ConfigDict, StringConstraints, model_validator

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
Status = Literal["pending", "approved", "rejected", "expired", "cancelled"]


def _nfc(value: Any) -> Any:
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value).strip()
    return value


_NO_CONTROL = r"^[^\x00-\x1f\x7f]*$"
# Reason and decision notes may span lines; other control characters are refused.
_NO_CONTROL_MULTILINE = r"^[^\x00-\x09\x0b-\x1f\x7f]*$"

AttributeKey = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{1,63}$")]
RawValue = Annotated[
    str,
    BeforeValidator(_nfc),
    StringConstraints(min_length=1, max_length=1000, pattern=_NO_CONTROL),
]
# Length rules (10..1000, config.yaml) are checked by the service with field-level codes.
Note = Annotated[
    str, BeforeValidator(_nfc), StringConstraints(max_length=1000, pattern=_NO_CONTROL_MULTILINE)
]


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class _Out(BaseModel):
    model_config = ConfigDict(from_attributes=True, frozen=True)


class ChangeRequestCreate(_In):
    """Request a correction of one identity attribute from one source (FR-CR-001)."""

    student_id: uuid.UUID
    attribute_key: AttributeKey
    target_source: Source = "admission_register"
    new_value: RawValue | None = None
    new_value_date: dt.date | None = None
    reason: Note
    evidence_document_id: uuid.UUID

    @model_validator(mode="after")
    def _one_value(self) -> Self:
        if (self.new_value is None) == (self.new_value_date is None):
            raise ValueError("send exactly one of new_value or new_value_date")
        return self

    @property
    def raw_value(self) -> str:
        if self.new_value_date is not None:
            return self.new_value_date.isoformat()
        return self.new_value or ""  # _one_value guarantees one of the two


class ApproveIn(_In):
    note: Note | None = None


class RejectIn(_In):
    reason: Note


class ChangeRequestOut(_Out):
    id: uuid.UUID
    student_id: uuid.UUID
    attribute_key: str
    attribute_label_en: str
    attribute_label_te: str
    target_source: str
    old_value_id: uuid.UUID | None
    old_value: str | None
    new_value: str
    masked: bool
    reason: str
    evidence_document_id: uuid.UUID
    status: Status
    requested_by: uuid.UUID
    requested_at: dt.datetime
    decided_by: uuid.UUID | None
    decided_at: dt.datetime | None
    decision_note: str | None
    applied_value_id: uuid.UUID | None
    expires_at: dt.datetime
    version: int
    # For the screen: may the caller approve/reject (holds the permission, is not the requester,
    # the request is still open) or cancel it (is the requester, still open)?
    can_decide: bool
    can_cancel: bool
