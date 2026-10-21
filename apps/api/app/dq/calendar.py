"""Portal and board calendar: expected windows, states, applicability and reminder lead days
(FR-RDY-001..003, FR-RDY-006, FR-RDY-008, US-3101, US-3103, ADR-0053).

Pure module (import-linter contract ``dq-pure-library``): no database, no routes. The database
side (confirmed dates, the school's own events, reminders, tasks and notifications) lives in
:mod:`app.dq.calendar_service`.

- :func:`load_calendar` reads ``config/portal_calendar.yaml`` once and validates it (unique keys,
  known portals, boards and readiness profiles, a window that closes on or after it opens, lead
  days). Every date in the file is an expectation (``verified: false``, owner decision D3); a
  date the school confirmed always wins (:class:`Window`).
- :func:`applies` decides whether an event concerns a school from its boards and classes
  (FR-RDY-002; boards and class map as FR-TEN-020/021).
- :func:`state` gives ``expected``, ``confirmed``, ``open`` or ``closed`` (FR-RDY-003). An
  expected window is never shown as "open now": nobody has confirmed it is.
- :func:`due_lead_day` picks the reminder to send today (FR-RDY-006, FR-RDY-008): the smallest
  lead day at or above the days left that was not sent yet, so a missed morning catches up once
  and a rerun sends nothing.
"""

from __future__ import annotations

import datetime as dt
import functools
import re
import unicodedata
import uuid
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from importlib import resources
from typing import Any, Final, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.dq.profiles import load_profiles

Portal = Literal["udise_plus", "bseap", "cbse", "cisce", "apaar", "school"]
State = Literal["expected", "confirmed", "open", "closed"]

PORTALS: Final[tuple[str, ...]] = ("udise_plus", "bseap", "cbse", "cisce", "apaar")
# The boards a school can declare (app.tenancy.schemas.SUPPORTED_BOARDS; a test keeps them equal,
# this module may not import tenancy).
BOARDS: Final[tuple[str, ...]] = ("BSEAP", "CBSE", "CISCE")
STATES: Final[tuple[State, ...]] = ("expected", "confirmed", "open", "closed")
EVENT_KEY_PATTERN: Final = r"^[a-z][a-z0-9_]{2,63}$"
OWN_KEY_PATTERN: Final = r"^school:[a-z0-9][a-z0-9-]{1,47}$"
ANY_KEY_PATTERN: Final = r"^([a-z][a-z0-9_]{2,63}|school:[a-z0-9][a-z0-9-]{1,47})$"
OWN_PREFIX: Final = "school:"
YEAR_PATTERN: Final = r"^(20[0-9]{2})-([0-9]{2})$"
CLASS_PATTERN: Final = r"^[A-Z0-9]{1,8}$"
MAX_LEAD_DAY: Final = 120


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PortalLabel(_Model):
    en: str = Field(min_length=1, max_length=60)
    te: str = Field(min_length=1, max_length=60)


def _lead_days(value: Sequence[int]) -> tuple[int, ...]:
    days = tuple(value)
    if not days:
        raise ValueError("lead_days must not be empty")
    if any(d < 0 or d > MAX_LEAD_DAY for d in days):
        raise ValueError(f"lead days must be between 0 and {MAX_LEAD_DAY}")
    if len(set(days)) != len(days) or list(days) != sorted(days, reverse=True):
        raise ValueError("lead_days must be distinct and in descending order")
    return days


class PortalEvent(_Model):
    """One expected window from the calendar file (never official; ``verified: false``)."""

    key: str = Field(pattern=EVENT_KEY_PATTERN)
    year: str = Field(pattern=YEAR_PATTERN)
    portal: Literal["udise_plus", "bseap", "cbse", "cisce", "apaar"]
    label_en: str = Field(min_length=3, max_length=120)
    label_te: str = Field(min_length=3, max_length=160)
    profile_keys: tuple[str, ...] = Field(default=(), max_length=5)
    boards: tuple[str, ...] = ()
    classes: tuple[str, ...] = ()
    opens_on: dt.date
    opens_approximate: bool = False
    closes_on: dt.date
    closes_approximate: bool = False
    lead_days: tuple[int, ...]
    source: tuple[str, ...] = Field(min_length=1)
    verified: bool

    @field_validator("boards")
    @classmethod
    def _boards(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        unknown = sorted(set(value) - set(BOARDS))
        if unknown:
            raise ValueError(f"unknown boards {unknown}")
        return value

    @field_validator("classes")
    @classmethod
    def _classes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not re.match(CLASS_PATTERN, c) for c in value):
            raise ValueError("class codes must look like IX or 10")
        return value

    @field_validator("source")
    @classmethod
    def _sources(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not s.startswith("https://") for s in value):
            raise ValueError("sources must be https URLs")
        return value

    @field_validator("lead_days")
    @classmethod
    def _leads(cls, value: tuple[int, ...]) -> tuple[int, ...]:
        return _lead_days(value)

    @model_validator(mode="after")
    def _window(self) -> PortalEvent:
        if self.closes_on < self.opens_on:
            raise ValueError(f"{self.key}: closes_on is before opens_on")
        start = int(self.year[:4])
        if not start <= self.opens_on.year <= start + 2:
            raise ValueError(f"{self.key}: dates do not belong to academic year {self.year}")
        return self


class PortalCalendar(_Model):
    version: int = Field(ge=1)
    default_lead_days: tuple[int, ...]
    portals: Mapping[str, PortalLabel]
    events: tuple[PortalEvent, ...]

    def by_key(self) -> dict[str, PortalEvent]:
        return {e.key: e for e in self.events}

    def for_year(self, year_key: str) -> list[PortalEvent]:
        return [e for e in self.events if e.year == year_key]

    def portal_label(self, portal: str) -> PortalLabel | None:
        return self.portals.get(portal)


class CalendarConfigError(ValueError):
    """``portal_calendar.yaml`` is malformed (raised when the API starts)."""


def _parse_year(year_key: Any, items: Any, default: tuple[int, ...]) -> list[PortalEvent]:
    if not isinstance(year_key, str) or not re.match(YEAR_PATTERN, year_key):
        raise CalendarConfigError(f"bad academic year {year_key!r} (use 2026-27)")
    if int(year_key[5:]) != (int(year_key[2:4]) + 1) % 100:
        raise CalendarConfigError(f"bad academic year {year_key!r} (use 2026-27)")
    if not isinstance(items, list):
        raise CalendarConfigError(f"{year_key}: expected a list of events")
    known_profiles = load_profiles()
    events: list[PortalEvent] = []
    for item in items:
        if not isinstance(item, dict):
            raise CalendarConfigError(f"{year_key}: each event must be a mapping")
        try:
            event = PortalEvent.model_validate({"lead_days": default, **item, "year": year_key})
        except ValueError as exc:
            raise CalendarConfigError(f"{year_key}/{item.get('key')}: {exc}") from exc
        unknown = sorted(set(event.profile_keys) - set(known_profiles))
        if unknown:
            raise CalendarConfigError(f"{event.key}: unknown profiles {unknown}")
        events.append(event)
    return events


def parse_calendar(raw: Any) -> PortalCalendar:
    """Validate the raw YAML mapping (FR-RDY-001)."""
    if not isinstance(raw, dict):
        raise CalendarConfigError("portal_calendar.yaml: expected a mapping")
    try:
        default = _lead_days(raw.get("default_lead_days") or ())
    except ValueError as exc:
        raise CalendarConfigError(f"default_lead_days: {exc}") from exc
    portals = raw.get("portals")
    if not isinstance(portals, dict) or set(portals) != set(PORTALS):
        raise CalendarConfigError(f"portals must name exactly {sorted(PORTALS)}")
    years = raw.get("years")
    if not isinstance(years, dict) or not years:
        raise CalendarConfigError("years must be a non-empty mapping")
    events = [
        event
        for year_key, items in years.items()
        for event in _parse_year(year_key, items, default)
    ]
    keys = [e.key for e in events]
    if len(set(keys)) != len(keys):
        raise CalendarConfigError("event keys must be unique in the whole file")
    try:
        return PortalCalendar(
            version=raw.get("version", 0),
            default_lead_days=default,
            portals={k: PortalLabel.model_validate(v) for k, v in portals.items()},
            events=tuple(events),
        )
    except ValueError as exc:
        raise CalendarConfigError(str(exc)) from exc


@functools.cache
def load_calendar() -> PortalCalendar:
    """``app/dq/config/portal_calendar.yaml``, validated once."""
    text = resources.files("app.dq").joinpath("config/portal_calendar.yaml").read_text("utf-8")
    return parse_calendar(yaml.safe_load(text))


# --- years ------------------------------------------------------------------------------------


def year_key(starts_on: dt.date) -> str:
    """The calendar file's key for a school year starting on ``starts_on`` ("2026-27")."""
    return f"{starts_on.year}-{(starts_on.year + 1) % 100:02d}"


# --- windows and states -----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Window:
    """The dates in force: confirmed by the school, else the file's expectation."""

    opens_on: dt.date
    closes_on: dt.date
    confirmed: bool
    opens_approximate: bool = False
    closes_approximate: bool = False


def expected_window(event: PortalEvent) -> Window:
    return Window(
        opens_on=event.opens_on,
        closes_on=event.closes_on,
        confirmed=False,
        opens_approximate=event.opens_approximate,
        closes_approximate=event.closes_approximate,
    )


def state(window: Window, today: dt.date) -> State:
    """``closed`` after the window; a confirmed window is ``open`` while it runs and
    ``confirmed`` before; an unconfirmed one stays ``expected`` until it has passed."""
    if today > window.closes_on:
        return "closed"
    if not window.confirmed:
        return "expected"
    if window.opens_on <= today:
        return "open"
    return "confirmed"


def days_left(window: Window, today: dt.date) -> int:
    """Days until the window closes (0 on the closing day, negative once closed)."""
    return (window.closes_on - today).days


def due_lead_day(lead_days: Iterable[int], left: int, sent: Collection[int]) -> int | None:
    """The reminder to send now: the smallest lead day ``>= left`` (so a missed morning still
    sends that step once), unless it was sent already; nothing once the window closed or before
    the first lead day."""
    if left < 0:
        return None
    candidates = [d for d in lead_days if d >= left]
    if not candidates:
        return None
    lead = min(candidates)
    return None if lead in sent else lead


# --- applicability (FR-RDY-002) ---------------------------------------------------------------


def applies(
    event: PortalEvent,
    *,
    boards: Collection[str],
    class_boards: Mapping[str, str],
    class_codes: Collection[str],
) -> bool:
    """Whether a calendar event concerns the school.

    Boards: a portal event (no boards) applies everywhere; a board event applies to schools
    that declared that board, or no board at all (FR-TEN-020). Classes: the school must have
    one of the event's classes that follows one of its boards here (FR-TEN-021: classes not in
    the class map follow the school's only board); a school with no classes yet is not ruled
    out by class."""
    if event.boards and boards and not set(event.boards) & set(boards):
        return False
    if not event.classes or not class_codes:
        return True
    default_board = next(iter(boards)) if len(boards) == 1 else None
    for code in event.classes:
        if code not in class_codes:
            continue
        board = class_boards.get(code, default_board)
        if not event.boards or board is None or board in event.boards:
            return True
    return False


# --- the school's own events ------------------------------------------------------------------


def is_own_key(event_key: str) -> bool:
    return event_key.startswith(OWN_PREFIX)


def own_event_key(label: str, *, salt: uuid.UUID) -> str:
    """``school:<slug>-<6 hex>`` for a school's own event: readable, unique enough per school and
    year (a clash answers 409), never personal (the label is the school's own words)."""
    ascii_label = unicodedata.normalize("NFKD", label).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_label.lower()).strip("-")[:40].strip("-")
    return f"{OWN_PREFIX}{slug or 'event'}-{salt.hex[-6:]}"


__all__ = [
    "ANY_KEY_PATTERN",
    "BOARDS",
    "PORTALS",
    "STATES",
    "CalendarConfigError",
    "PortalCalendar",
    "PortalEvent",
    "State",
    "Window",
    "applies",
    "days_left",
    "due_lead_day",
    "expected_window",
    "is_own_key",
    "load_calendar",
    "own_event_key",
    "parse_calendar",
    "state",
    "year_key",
]
