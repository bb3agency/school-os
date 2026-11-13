"""Pydantic IO for fix-owner letters (FA-08; FR-DQ-049..FR-DQ-060; docs/09 Data quality).

The overview carries names, admission numbers and field labels (what the office needs to choose
whom to write to), never a record value and never an Aadhaar number (invariant 4); the values
appear only on the printed letters."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.dq.followup_config import FollowupChannel, FollowupEvent, FollowupOwner
from app.dq.schemas import StudentRef

FixLetterKey = Literal["parent_aadhaar", "board_registration"]
FixLetterAudience = Literal["parent", "office"]


class FixLetterRecipientOut(BaseModel):
    model_config = ConfigDict(frozen=True)

    key: str
    label: str


class FixLetterStudentOut(BaseModel):
    """One student a letter would cover now, with the fields it lists (labels only)."""

    model_config = ConfigDict(frozen=True)

    student: StudentRef
    section_id: uuid.UUID | None
    class_section: str | None
    fields: list[str]
    last_sent_on: dt.date | None = Field(
        description="The last time this letter's follow-up step was recorded for the student."
    )


class FixLetterOut(BaseModel):
    """One letter template for a readiness profile and the students it covers now."""

    model_config = ConfigDict(frozen=True)

    key: FixLetterKey
    label: str
    title: str
    audience: FixLetterAudience
    fix_owner: FollowupOwner
    version: int
    verified: bool
    followup_event: FollowupEvent
    followup_channel: FollowupChannel
    print_permission: str
    step_up: bool
    recipients: list[FixLetterRecipientOut]
    students_count: int
    lines_count: int
    students: list[FixLetterStudentOut]


class FixLettersOut(BaseModel):
    model_config = ConfigDict(frozen=True)

    profile_key: str
    verified: bool
    letters: list[FixLetterOut]


class FixLetterSentIn(BaseModel):
    """``POST .../fix-letters/{letter_key}/sent``: the letters were given to the parents, or the
    letter went to the board office. Records the letter's follow-up step (no free text, D56)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    student_ids: list[uuid.UUID] = Field(min_length=1, max_length=300)
    occurred_on: dt.date | None = Field(
        default=None, description="When it happened (default today; not in the future)."
    )

    @model_validator(mode="after")
    def _unique(self) -> FixLetterSentIn:
        if len(set(self.student_ids)) != len(self.student_ids):
            raise ValueError("student_ids must not repeat")
        return self


class FixLetterSentOut(BaseModel):
    model_config = ConfigDict(frozen=True)

    recorded: int
    fix_owner: FollowupOwner
    event: FollowupEvent
    channel: FollowupChannel


__all__ = [
    "FixLetterKey",
    "FixLetterOut",
    "FixLetterRecipientOut",
    "FixLetterSentIn",
    "FixLetterSentOut",
    "FixLetterStudentOut",
    "FixLettersOut",
]
