"""Pydantic v2 IO models for the school's breach register (docs/09 R3 "Breach register and
incidents"; ADR-0050; FR-DPDP-032..038).

The description, containment steps and close reason are free text the school writes: returned
to readers of the register, never logged or audited (invariant 5). Everything else is codes,
counts and times.
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
    field_validator,
    model_validator,
)

from app.privacy.breach_config import Category, DraftKey

Origin = Literal["school", "processor_notice"]
BreachStatus = Literal["open", "contained", "closed"]
DutyKey = Literal["board_notice", "board_report", "people_notified"]
DutyMethod = Literal[
    "email", "portal", "letter", "phone", "in_person", "whatsapp", "sos_notice", "other"
]


def _nfc(value: Any) -> Any:
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value).strip()
    return value


_NO_CONTROL = r"^[^\x00-\x09\x0b-\x1f\x7f]*$"
LongText = Annotated[
    str,
    BeforeValidator(_nfc),
    StringConstraints(min_length=10, max_length=4000, pattern=_NO_CONTROL),
]
Reason = Annotated[
    str,
    BeforeValidator(_nfc),
    StringConstraints(min_length=10, max_length=500, pattern=_NO_CONTROL),
]
Count = Annotated[int, Field(ge=0, le=1_000_000)]


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class _Out(BaseModel):
    model_config = ConfigDict(from_attributes=True, frozen=True)


def _unique(value: list[str]) -> list[str]:
    if len(set(value)) != len(value):
        raise ValueError("categories must not repeat")
    return value


class BreachCreate(_In):
    """Record a breach (FR-DPDP-033). ``origin`` ``processor_notice`` is for a notice SchoolOS
    gave by phone or e-mail on a dedicated host (FR-DPDP-038); on the shared platform those
    arrive by themselves."""

    origin: Origin = "school"
    noticed_at: dt.datetime = Field(description="T0: when the school noticed it (not future).")
    description: LongText = Field(description="What happened. Encrypted; never logged.")
    categories: list[Category] = Field(min_length=1, max_length=10)
    approx_children: Count = 0
    approx_guardians: Count = 0
    approx_staff: Count = 0
    containment: LongText | None = Field(
        default=None, description="Steps taken to contain it. Encrypted; never logged."
    )

    @field_validator("categories")
    @classmethod
    def _cats(cls, value: list[str]) -> list[str]:
        return _unique(value)


class DutyIn(_In):
    done_at: dt.datetime
    method: DutyMethod


class BreachUpdate(_In):
    """Change facts, status or record a duty as done (FR-DPDP-034). Closing needs all three
    duties recorded or a ``close_reason``."""

    description: LongText | None = None
    containment: LongText | None = None
    categories: list[Category] | None = Field(default=None, min_length=1, max_length=10)
    approx_children: Count | None = None
    approx_guardians: Count | None = None
    approx_staff: Count | None = None
    status: BreachStatus | None = None
    board_notice: DutyIn | None = None
    board_report: DutyIn | None = None
    people_notified: DutyIn | None = None
    close_reason: Reason | None = Field(
        default=None, description="Why the breach is closed with a duty not recorded."
    )

    @field_validator("categories")
    @classmethod
    def _cats(cls, value: list[str] | None) -> list[str] | None:
        return None if value is None else _unique(value)


class AffectedIn(_In):
    """The full list of affected people (replaces the previous list; ids only)."""

    student_ids: list[uuid.UUID] = Field(default_factory=list, max_length=5000)
    guardian_ids: list[uuid.UUID] = Field(default_factory=list, max_length=5000)
    membership_ids: list[uuid.UUID] = Field(default_factory=list, max_length=5000)

    @model_validator(mode="after")
    def _unique_ids(self) -> AffectedIn:
        for name in ("student_ids", "guardian_ids", "membership_ids"):
            ids = getattr(self, name)
            if len(set(ids)) != len(ids):
                raise ValueError(f"{name} must not repeat")
        return self


class DutyOut(_Out):
    key: DutyKey
    due_at: dt.datetime | None = Field(
        description="When it is due (null: without delay, no fixed clock)."
    )
    done_at: dt.datetime | None
    method: DutyMethod | None
    overdue: bool
    default_rule: bool = Field(
        description="True for the Board's initial notice: the 24 h limit is a SchoolOS default "
        "(to confirm), not a number in the Rules."
    )


class NextDutyOut(_Out):
    key: DutyKey
    due_at: dt.datetime | None
    hours_left: int | None = Field(
        description="Whole hours until due (negative when overdue); null without a clock."
    )


class ProcessorFactsOut(_Out):
    """What SchoolOS reported (codes, counts and times only)."""

    severity: str | None = None
    approx_people: int | None = None
    window_from: dt.datetime | None = None
    window_to: dt.datetime | None = None
    opened_at: dt.datetime | None = None


class BreachOut(_Out):
    id: uuid.UUID
    origin: Origin
    platform_incident_id: uuid.UUID | None
    noticed_at: dt.datetime
    categories: list[str]
    approx_children: int
    approx_guardians: int
    approx_staff: int
    status: BreachStatus
    duties: list[DutyOut]
    next_duty: NextDutyOut | None
    processor_facts: ProcessorFactsOut | None
    closed_at: dt.datetime | None
    created_at: dt.datetime
    version: int


class AffectedCountsOut(_Out):
    students: int
    guardians: int
    staff: int


class BreachDetailOut(BreachOut):
    description: str | None
    containment: str | None
    close_reason: str | None
    affected: AffectedCountsOut
    recorded_by_name: str | None


class AffectedOut(_Out):
    breach_id: uuid.UUID
    student_ids: list[uuid.UUID]
    guardian_ids: list[uuid.UUID]
    membership_ids: list[uuid.UUID]


class DraftOut(_Out):
    """A draft filled from the record and the school's privacy contact (FR-DPDP-036); unfilled
    placeholders print ``[not set]``. Nothing is sent."""

    draft: DraftKey
    template_version: str
    title: str
    text: str


class NoticeDraftOut(_Out):
    """The parent notice created in M4 (``source = staff_text``; approval needed)."""

    notice_id: uuid.UUID
    status: str


__all__ = [
    "AffectedCountsOut",
    "AffectedIn",
    "AffectedOut",
    "BreachCreate",
    "BreachDetailOut",
    "BreachOut",
    "BreachStatus",
    "BreachUpdate",
    "DraftOut",
    "DutyIn",
    "DutyKey",
    "DutyMethod",
    "DutyOut",
    "NextDutyOut",
    "NoticeDraftOut",
    "Origin",
    "ProcessorFactsOut",
]
