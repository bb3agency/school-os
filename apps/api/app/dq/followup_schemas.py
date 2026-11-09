"""Pydantic IO for follow-ups on fixes outside the school (FO-01) and the UDISE+ to-do list
(FO-03) (FR-UDI-021..FR-UDI-040; docs/09 Data quality). Codes, dates, ids and counts only:
never a value of the record and never an Aadhaar number (invariant 4)."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.dq.followup_config import (
    FollowupChannel,
    FollowupEvent,
    FollowupOwner,
    FollowupState,
    MovementAction,
    MovementKind,
    OverdueReason,
)
from app.dq.schemas import ReadinessStatus, StudentRef

ITEM_KEY_PATTERN = r"^[a-z_]{3,30}:[0-9a-f-]{36}$"
MovementState = Literal["open", "done", "not_needed"]
MarkState = Literal["done", "not_needed"]


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class _Out(BaseModel):
    model_config = ConfigDict(frozen=True)


# --- follow-ups (FO-01) -------------------------------------------------------------------------


class FollowupIn(_In):
    """``POST /dq/readiness/{profile_key}/follow-ups``: one step for one or more students (one
    click "slips given" for a printed section). ``channel`` is required for the steps that name
    one (``parent_contacted``, ``request_sent``, ``followed_up``) and refused for the others;
    ``promised_on`` exactly for ``promised_by``. No free text (D56)."""

    student_ids: list[uuid.UUID] = Field(min_length=1, max_length=300)
    fix_owner: FollowupOwner
    event: FollowupEvent
    channel: FollowupChannel | None = None
    occurred_on: dt.date | None = Field(
        default=None, description="When it happened (default today; not in the future)."
    )
    promised_on: dt.date | None = None

    @model_validator(mode="after")
    def _unique(self) -> FollowupIn:
        if len(set(self.student_ids)) != len(self.student_ids):
            raise ValueError("student_ids must not repeat")
        return self


class FollowupRecordedOut(_Out):
    recorded: int


class FollowupCounts(_Out):
    """Open follow-ups by state (``open`` = all of them)."""

    open: int
    not_started: int
    waiting: int
    overdue: int
    update_reported: int


class FollowupOwnerCounts(_Out):
    fix_owner: FollowupOwner
    counts: FollowupCounts


class FollowupItemOut(_Out):
    """One student's follow-up for one fix owner, while the readiness check still finds a
    difference that owner must fix."""

    student: StudentRef
    section_id: uuid.UUID | None
    readiness_status: ReadinessStatus
    fix_owner: FollowupOwner
    state: FollowupState
    overdue_reason: OverdueReason | None
    last_event: FollowupEvent | None
    last_on: dt.date | None
    promised_on: dt.date | None
    steps: int


class FollowupListOut(_Out):
    profile_key: str
    today: dt.date
    overdue_after_days: int
    totals: list[FollowupOwnerCounts]
    items: list[FollowupItemOut]


class FollowupStepOut(_Out):
    id: uuid.UUID
    fix_owner: FollowupOwner
    event: FollowupEvent
    channel: FollowupChannel | None
    occurred_on: dt.date
    promised_on: dt.date | None
    recorded_at: dt.datetime


class FollowupHistoryOut(_Out):
    """Every step recorded for one student and profile, oldest first, and the open
    follow-ups (one per fix owner the readiness check still names)."""

    student_id: uuid.UUID
    profile_key: str
    open: list[FollowupItemOut]
    steps: list[FollowupStepOut]


# --- UDISE+ to-do list (FO-03) ------------------------------------------------------------------


class MovementCounts(_Out):
    promoted: int
    held_back: int
    graduated: int


class MovementItemOut(_Out):
    """One step to repeat in UDISE+ after something happened in SchoolOS. ``action`` says what
    to do (an admission with a PEN is imported by PEN, never created again)."""

    item_key: str
    kind: MovementKind
    action: MovementAction
    student: StudentRef | None
    class_section: str | None
    event_on: dt.date
    certificate_serial: str | None
    progression: MovementCounts | None
    state: MovementState
    overdue: bool
    marked_at: dt.datetime | None
    version: int | None


class MovementTotals(_Out):
    open: int
    overdue: int
    done: int
    not_needed: int


class MovementListOut(_Out):
    """The UDISE+ to-do list (newest first), computed from SchoolOS records on every read."""

    verified: bool
    source: list[str]
    since: dt.date | None
    today: dt.date
    overdue_after_days: int
    totals: MovementTotals
    items: list[MovementItemOut]


class MovementMarkIn(_In):
    state: MarkState
