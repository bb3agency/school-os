"""Follow-ups on fixes outside the school (FO-01) and the UDISE+ to-do list (FO-03): the
configuration and the pure rules (no database; import-linter ``dq-pure-library``).

- :func:`load_followup_config` reads ``app/dq/config/udise_followup.yaml`` once, validated.
- :func:`followup_state` turns one student's recorded steps for one fix owner into its state
  (``not_started``, ``waiting``, ``overdue``, ``update_reported``) and why it is overdue.
- :func:`movement_overdue` says whether a UDISE+ to-do item has waited too long.
"""

from __future__ import annotations

import datetime as dt
import functools
from collections.abc import Sequence
from dataclasses import dataclass
from importlib import resources
from typing import Any, Final, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

FollowupOwner = Literal["parent_aadhaar", "school_udise"]
FollowupEvent = Literal[
    "slip_given",
    "parent_contacted",
    "promised_by",
    "update_reported",
    "request_sent",
    "followed_up",
]
FollowupChannel = Literal["in_person", "phone", "whatsapp", "letter", "email"]
FollowupState = Literal["not_started", "waiting", "overdue", "update_reported"]
OverdueReason = Literal["no_contact", "promise_passed"]
MovementKind = Literal["transfer_out", "left_without_tc", "admission", "progression"]
MovementAction = Literal[
    "transfer_out", "left_without_tc", "admission_without_pen", "admission_with_pen", "progression"
]

OWNERS: Final[tuple[FollowupOwner, ...]] = ("parent_aadhaar", "school_udise")
EVENTS: Final[tuple[FollowupEvent, ...]] = (
    "slip_given",
    "parent_contacted",
    "promised_by",
    "update_reported",
    "request_sent",
    "followed_up",
)
CHANNELS: Final[tuple[FollowupChannel, ...]] = ("in_person", "phone", "whatsapp", "letter", "email")
ACTIONS: Final[tuple[MovementAction, ...]] = (
    "transfer_out",
    "left_without_tc",
    "admission_without_pen",
    "admission_with_pen",
    "progression",
)


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Label(_Model):
    en: str = Field(min_length=3, max_length=200)


class OwnerSpec(_Model):
    label: Label
    events: tuple[FollowupEvent, ...] = Field(min_length=1)


class EventSpec(_Model):
    en: str = Field(min_length=3, max_length=120)
    channels: tuple[FollowupChannel, ...] = ()


class FollowupsSpec(_Model):
    owners: dict[FollowupOwner, OwnerSpec]
    overdue_after_days: int = Field(ge=1, le=120)
    max_backdate_days: int = Field(ge=0, le=365)
    max_promise_days: int = Field(ge=1, le=365)
    events: dict[FollowupEvent, EventSpec]
    channels: dict[FollowupChannel, Label]

    @model_validator(mode="after")
    def _complete(self) -> FollowupsSpec:
        if set(self.owners) != set(OWNERS):
            raise ValueError("followups.owners must name parent_aadhaar and school_udise")
        if set(self.events) != set(EVENTS):
            raise ValueError("followups.events must describe every event")
        if set(self.channels) != set(CHANNELS):
            raise ValueError("followups.channels must describe every channel")
        if "promised_by" in self.owners["school_udise"].events:
            raise ValueError("a promise is the parent's step only")
        return self


class MovementsSpec(_Model):
    verified: bool
    source: tuple[str, ...] = Field(min_length=1)
    window_days_before_year: int = Field(ge=0, le=366)
    overdue_after_days: int = Field(ge=1, le=120)
    kinds: dict[MovementAction, Label]

    @model_validator(mode="after")
    def _complete(self) -> MovementsSpec:
        if set(self.kinds) != set(ACTIONS):
            raise ValueError("movements.kinds must describe every action")
        return self


class FollowupConfig(_Model):
    version: int = Field(ge=1)
    followups: FollowupsSpec
    movements: MovementsSpec

    def channels_for(self, event: FollowupEvent) -> tuple[FollowupChannel, ...]:
        return self.followups.events[event].channels


def parse_followup_config(raw: Any) -> FollowupConfig:
    return FollowupConfig.model_validate(raw)


@functools.cache
def load_followup_config() -> FollowupConfig:
    """``app/dq/config/udise_followup.yaml``, validated once."""
    text = resources.files("app.dq").joinpath("config/udise_followup.yaml").read_text("utf-8")
    return parse_followup_config(yaml.safe_load(text))


# --- follow-up state ------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Step:
    """One recorded step (dates and codes only)."""

    event: str
    occurred_on: dt.date
    promised_on: dt.date | None = None


@dataclass(frozen=True, slots=True)
class State:
    state: FollowupState
    overdue_reason: OverdueReason | None
    last_event: str | None
    last_on: dt.date | None
    promised_on: dt.date | None
    steps: int


def followup_state(steps: Sequence[Step], today: dt.date, overdue_after_days: int) -> State:
    """The state of one (student, fix owner) follow-up from its steps (any order).

    - No step: ``not_started``.
    - The last step is ``update_reported``: ``update_reported`` (the office now records the new
      value and the next readiness check closes the follow-up).
    - A promised date (the latest promise not followed by a reported update) before today:
      ``overdue`` (``promise_passed``).
    - Nothing recorded for more than ``overdue_after_days`` days: ``overdue`` (``no_contact``).
    - Otherwise ``waiting``."""
    if not steps:
        return State("not_started", None, None, None, None, 0)
    ordered = sorted(steps, key=lambda s: s.occurred_on)
    last = ordered[-1]
    promised: dt.date | None = None
    for step in ordered:
        if step.event == "promised_by":
            promised = step.promised_on
        elif step.event == "update_reported":
            promised = None
    if last.event == "update_reported":
        return State("update_reported", None, last.event, last.occurred_on, None, len(ordered))
    if promised is not None and promised < today:
        return State(
            "overdue", "promise_passed", last.event, last.occurred_on, promised, len(steps)
        )
    if (today - last.occurred_on).days > overdue_after_days:
        return State("overdue", "no_contact", last.event, last.occurred_on, promised, len(steps))
    return State("waiting", None, last.event, last.occurred_on, promised, len(steps))


def movement_overdue(
    event_on: dt.date, today: dt.date, overdue_after_days: int, *, open_: bool
) -> bool:
    """An open to-do item older than ``overdue_after_days`` days waits too long."""
    return open_ and (today - event_on).days > overdue_after_days
