"""Compliance certificate catalogue and rules (``catalogue.yaml``; FC-01; FR-COMP-001,
FR-COMP-002, FR-COMP-010..FR-COMP-013).

Pure module (no database or web): the certificate kinds with their typical validity and the
boards they apply to, the reminder lead days, and the two pure rules every caller shares:
:func:`state` (valid / expiring / expired / no expiry) and :func:`due_reminder` (which reminder,
if any, is due today). ``verified: false``: the catalogue is a SchoolOS proposal from secondary
sources, and the school always types the real dates printed on its certificate.
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources
from typing import Final, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

KEY: Final = re.compile(r"^[a-z][a-z0-9_]{1,62}$")
OTHER: Final = "other"
# ``last_reminded_lead`` after the expiry notice (lead days are 0..365).
EXPIRED_LEAD: Final = -1
BOARDS: Final = frozenset({"BSEAP", "CBSE", "CISCE"})

State = Literal["valid", "expiring", "expired", "no_expiry"]
DisclosurePolicy = Literal["warn", "refuse"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Kind(_Model):
    label: str = Field(min_length=3, max_length=120)
    authority: str = Field(min_length=3, max_length=200)
    typical_validity_months: int | None = Field(default=None, ge=1, le=240)
    boards: tuple[str, ...] = ()
    disclosure_items: tuple[str, ...] = ()
    note: str = Field(min_length=3, max_length=300)

    @field_validator("boards")
    @classmethod
    def _boards(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        unknown = [b for b in value if b not in BOARDS]
        if unknown:
            raise ValueError(f"unknown boards {unknown}")
        return value

    def applies_to(self, boards: Sequence[str]) -> bool:
        """A kind with no boards applies to every school; otherwise to a school with one."""
        return not self.boards or any(b in self.boards for b in boards)


class Reminders(_Model):
    lead_days: tuple[int, ...] = Field(min_length=1, max_length=10)
    expired_every_days: int = Field(ge=1, le=180)

    @field_validator("lead_days")
    @classmethod
    def _leads(cls, value: tuple[int, ...]) -> tuple[int, ...]:
        if any(d < 0 or d > 365 for d in value) or len(set(value)) != len(value):
            raise ValueError("lead days are distinct, 0..365")
        if 0 not in value:
            raise ValueError("lead day 0 (the day it lapses) is required")
        return tuple(sorted(value, reverse=True))


class Catalogue(_Model):
    version: int = Field(ge=1)
    verified: bool
    source: str = Field(min_length=10)
    reminders: Reminders
    expiring_days: int = Field(ge=1, le=365)
    disclosure: DisclosurePolicy
    kinds: dict[str, Kind]

    @model_validator(mode="after")
    def _kinds(self) -> Catalogue:
        if OTHER not in self.kinds:
            raise ValueError("the reserved kind 'other' is required")
        for key, kind in self.kinds.items():
            if not KEY.fullmatch(key):
                raise ValueError(f"bad kind key {key!r}")
            if key == OTHER and kind.disclosure_items:
                raise ValueError("'other' backs no disclosure item")
            for item in kind.disclosure_items:
                if not KEY.fullmatch(item):
                    raise ValueError(f"{key}: bad disclosure item {item!r}")
        return self

    def kinds_for(self, boards: Sequence[str]) -> dict[str, Kind]:
        """The catalogue kinds that apply to a school with ``boards`` (``other`` left out)."""
        return {k: v for k, v in self.kinds.items() if k != OTHER and v.applies_to(boards)}

    def kinds_for_item(self, item_key: str) -> tuple[str, ...]:
        """The kinds that back one CBSE Appendix IX item, in catalogue order."""
        return tuple(k for k, v in self.kinds.items() if item_key in v.disclosure_items)


def parse(raw: object) -> Catalogue:
    return Catalogue.model_validate(raw)


@lru_cache(maxsize=1)
def catalogue() -> Catalogue:
    raw = yaml.safe_load(
        resources.files("app.compliance").joinpath("catalogue.yaml").read_text("utf-8")
    )
    return parse(raw)


def state(valid_until: dt.date | None, today: dt.date, *, expiring_days: int) -> State:
    """FR-COMP-010: a certificate without a "valid until" date never lapses; one whose date is
    before today has expired (valid through the date itself); one lapsing within
    ``expiring_days`` is expiring."""
    if valid_until is None:
        return "no_expiry"
    left = (valid_until - today).days
    if left < 0:
        return "expired"
    return "expiring" if left <= expiring_days else "valid"


@dataclass(frozen=True, slots=True)
class DueReminder:
    lead: int
    days: int
    expired: bool


def due_reminder(  # noqa: PLR0911 - one answer per case reads best
    valid_until: dt.date | None,
    today: dt.date,
    *,
    lead_days: Sequence[int],
    expired_every_days: int,
    last_lead: int | None,
    last_on: dt.date | None,
) -> DueReminder | None:
    """FR-COMP-011, FR-COMP-012: the reminder due today, if any.

    Before the date: the smallest lead day at or above the days left, when no reminder for
    that lead day or a smaller one was sent (a missed day sends only the latest step). From the
    day after the date: the expiry notice (lead :data:`EXPIRED_LEAD`) once, then again every
    ``expired_every_days`` days while the certificate stays expired."""
    if valid_until is None:
        return None
    left = (valid_until - today).days
    if left < 0:
        if last_lead is None or last_lead >= 0:
            return DueReminder(lead=EXPIRED_LEAD, days=-left, expired=True)
        if last_on is None or (today - last_on).days >= expired_every_days:
            return DueReminder(lead=EXPIRED_LEAD, days=-left, expired=True)
        return None
    candidates = [d for d in lead_days if d >= left]
    if not candidates:
        return None
    lead = min(candidates)
    if last_lead is not None and last_lead <= lead:
        return None
    return DueReminder(lead=lead, days=left, expired=False)


__all__ = [
    "BOARDS",
    "EXPIRED_LEAD",
    "OTHER",
    "Catalogue",
    "DisclosurePolicy",
    "DueReminder",
    "Kind",
    "Reminders",
    "State",
    "catalogue",
    "due_reminder",
    "parse",
    "state",
]
