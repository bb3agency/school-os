"""Pydantic IO models for UDISE+ correction requests to the MEO (FO-02; FR-UDI-001..FR-UDI-010;
docs/09 Data quality)."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.dq.schemas import PROFILE_PATTERN, StudentRef

RequestStatus = Literal["prepared", "sent", "done", "rejected"]
LineState = Literal["open", "fixed", "unavailable"]
AadhaarMatch = Literal["yes", "no", "not_recorded"]
AttributeKey = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{1,62}$")]
RecipientKey = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{1,62}$")]
ProfileKey = Annotated[str, StringConstraints(pattern=PROFILE_PATTERN, max_length=64)]


class _Out(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


class UdiseRecipientOut(_Out):
    key: str
    label: str


class UdiseLineOut(_Out):
    """One field of one student the school asks UDISE+ to correct. Values are C2 (name, date
    of birth, gender); never an Aadhaar number, and the Aadhaar-as-printed value only as
    ``matches_aadhaar`` yes/no (invariant 4)."""

    student: StudentRef
    class_section: str | None
    pen: str | None
    attribute_key: str
    field_label: str
    udise_value: str | None
    correct_value: str | None
    correct_source: str
    evidence: list[str]
    matches_aadhaar: AadhaarMatch
    state: LineState
    requested_in: uuid.UUID | None = None


class UdiseGroupOut(_Out):
    key: str
    label: str
    lines: list[UdiseLineOut]


class UdiseCandidatesOut(_Out):
    """What a new request would ask for now (the readiness differences UDISE+ must fix)."""

    profile_key: str
    verified: bool
    groups: list[UdiseGroupOut]
    lines_count: int
    students_count: int
    already_requested: int
    recipients: list[UdiseRecipientOut]


class UdiseLineRef(_In):
    student_id: uuid.UUID
    attribute_key: AttributeKey


class UdiseRequestCreate(_In):
    profile_key: ProfileKey
    section_ids: list[uuid.UUID] | None = Field(default=None, max_length=200)
    lines: list[UdiseLineRef] | None = Field(
        default=None,
        max_length=500,
        description="The lines to ask for; omitted: every open difference not yet requested.",
    )


class UdiseRequestOut(_Out):
    id: uuid.UUID
    profile_key: str
    status: RequestStatus
    lines_count: int
    students_count: int
    prepared_by: uuid.UUID
    prepared_at: dt.datetime
    sent_on: dt.date | None
    sent_to: str | None
    sent_to_label: str | None
    closed_at: dt.datetime | None
    version: int


class UdiseRequestDetailOut(UdiseRequestOut):
    verified: bool
    groups: list[UdiseGroupOut]
    fixed_count: int
    open_count: int
    recipients: list[UdiseRecipientOut]


class UdiseSentIn(_In):
    sent_on: dt.date
    sent_to: RecipientKey


class UdiseCloseIn(_In):
    outcome: Literal["done", "rejected"]


__all__ = [
    "UdiseCandidatesOut",
    "UdiseCloseIn",
    "UdiseGroupOut",
    "UdiseLineOut",
    "UdiseLineRef",
    "UdiseRecipientOut",
    "UdiseRequestCreate",
    "UdiseRequestDetailOut",
    "UdiseRequestOut",
    "UdiseSentIn",
]
