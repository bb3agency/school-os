"""Safety check kinds and policy review rules (``checks.yaml``, ``policies.yaml``; FC-09, FC-10;
FR-COMP-046, FR-COMP-047, FR-COMP-050, FR-COMP-056, FR-COMP-061).

Pure module (no database or web). ``verified: false``: the frequencies and the review interval
are SchoolOS proposals from secondary sources; the school decides and SchoolOS only reminds.

- :func:`add_months` moves a date by whole months (the 31st becomes the month's last day).
- :func:`check_state` says whether a periodic check is ``ok``, ``due_soon``, ``overdue`` or
  ``never`` done (``logged`` for a kind without a frequency).
- :func:`review_state` does the same for a policy's review date.
- Reminders reuse :func:`app.compliance.config.due_reminder` with the next due date as the
  "valid until" date (the same lead-day and repeat rules as certificates).
"""

from __future__ import annotations

import calendar
import datetime as dt
import re
from collections.abc import Sequence
from functools import lru_cache
from importlib import resources
from typing import Final, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.compliance.config import BOARDS

KEY: Final = re.compile(r"^[a-z][a-z0-9_]{1,62}$")
OTHER: Final = "other"

CheckState = Literal["ok", "due_soon", "overdue", "never", "logged"]
ReviewState = Literal["ok", "due_soon", "overdue"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Reminders(_Model):
    lead_days: tuple[int, ...] = Field(min_length=1, max_length=10)
    overdue_every_days: int = Field(ge=1, le=180)

    @field_validator("lead_days")
    @classmethod
    def _leads(cls, value: tuple[int, ...]) -> tuple[int, ...]:
        if any(d < 0 or d > 365 for d in value) or len(set(value)) != len(value):
            raise ValueError("lead days are distinct, 0..365")
        if 0 not in value:
            raise ValueError("lead day 0 (the due day) is required")
        return tuple(sorted(value, reverse=True))


class CheckKind(_Model):
    label: str = Field(min_length=3, max_length=120)
    every_months: int | None = Field(default=None, ge=1, le=60)
    boards: tuple[str, ...] = ()
    drill: bool = False
    note: str = Field(min_length=3, max_length=300)

    @field_validator("boards")
    @classmethod
    def _boards(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        unknown = [b for b in value if b not in BOARDS]
        if unknown:
            raise ValueError(f"unknown boards {unknown}")
        return value

    def applies_to(self, boards: Sequence[str]) -> bool:
        return not self.boards or any(b in self.boards for b in boards)


class ChecksCatalogue(_Model):
    version: int = Field(ge=1)
    verified: bool
    source: str = Field(min_length=10)
    reminders: Reminders
    due_soon_days: int = Field(ge=1, le=90)
    kinds: dict[str, CheckKind]

    @model_validator(mode="after")
    def _kinds(self) -> ChecksCatalogue:
        if OTHER not in self.kinds:
            raise ValueError("the reserved kind 'other' is required")
        if self.kinds[OTHER].every_months is not None:
            raise ValueError("'other' has no frequency")
        for key in self.kinds:
            if not KEY.fullmatch(key):
                raise ValueError(f"bad kind key {key!r}")
        return self

    def kinds_for(self, boards: Sequence[str]) -> dict[str, CheckKind]:
        """The kinds that apply to a school with ``boards`` (``other`` left out)."""
        return {k: v for k, v in self.kinds.items() if k != OTHER and v.applies_to(boards)}


class PolicyRules(_Model):
    version: int = Field(ge=1)
    verified: bool
    source: str = Field(min_length=10)
    review_months: int = Field(ge=1, le=60)
    reminders: Reminders
    due_soon_days: int = Field(ge=1, le=180)
    max_roles: int = Field(ge=1, le=50)


def parse_checks(raw: object) -> ChecksCatalogue:
    return ChecksCatalogue.model_validate(raw)


def parse_policies(raw: object) -> PolicyRules:
    return PolicyRules.model_validate(raw)


def _read(name: str) -> object:
    return yaml.safe_load(resources.files("app.compliance").joinpath(name).read_text("utf-8"))


@lru_cache(maxsize=1)
def checks() -> ChecksCatalogue:
    return parse_checks(_read("checks.yaml"))


@lru_cache(maxsize=1)
def policies() -> PolicyRules:
    return parse_policies(_read("policies.yaml"))


def add_months(day: dt.date, months: int) -> dt.date:
    """``day`` moved by ``months`` whole months; a day past the month's end becomes its last
    day (31 Jan + 1 month = 28 or 29 Feb)."""
    index = day.year * 12 + day.month - 1 + months
    year, month = divmod(index, 12)
    last = calendar.monthrange(year, month + 1)[1]
    return dt.date(year, month + 1, min(day.day, last))


def next_due(kind: CheckKind, last_done: dt.date | None) -> dt.date | None:
    """FR-COMP-050: the day the next check falls due (``None``: never done, or no frequency)."""
    if last_done is None or kind.every_months is None:
        return None
    return add_months(last_done, kind.every_months)


def check_state(
    kind: CheckKind, last_done: dt.date | None, today: dt.date, *, due_soon_days: int
) -> CheckState:
    """FR-COMP-050: ``never`` (no entry), ``logged`` (no frequency), else from the next due
    date: ``overdue`` once it has passed, ``due_soon`` within ``due_soon_days``, else ``ok``."""
    if last_done is None:
        return "never"
    due = next_due(kind, last_done)
    if due is None:
        return "logged"
    left = (due - today).days
    if left < 0:
        return "overdue"
    return "due_soon" if left <= due_soon_days else "ok"


def review_state(review_due: dt.date, today: dt.date, *, due_soon_days: int) -> ReviewState:
    """FR-COMP-061: a policy review is ``overdue`` after its date (due through the date
    itself), ``due_soon`` within ``due_soon_days``, else ``ok``."""
    left = (review_due - today).days
    if left < 0:
        return "overdue"
    return "due_soon" if left <= due_soon_days else "ok"


__all__ = [
    "OTHER",
    "CheckKind",
    "CheckState",
    "ChecksCatalogue",
    "PolicyRules",
    "Reminders",
    "ReviewState",
    "add_months",
    "check_state",
    "checks",
    "next_due",
    "parse_checks",
    "parse_policies",
    "policies",
    "review_state",
]
