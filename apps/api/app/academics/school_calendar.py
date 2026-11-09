"""School calendar rules (FT-06; FR-CAL-001..FR-CAL-020): ``app/academics/calendar.yaml`` and the
pure "is this a working day?" resolution. No database, no I/O beyond reading the config.

A day of an academic year is decided in this order (FR-CAL-004):

1. outside the year's dates: not a school day (``outside_year``);
2. an entry for the day's class (a per-class exception) decides;
3. otherwise a whole-school entry decides (``holiday``, ``vacation`` or an extra ``working_day``);
4. otherwise the weekly pattern: a weekday in ``weekly_off`` (``weekly_off``), or a Saturday whose
   number in the month is in ``saturdays_off`` (``saturday_off``); every other day is ``working``.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources
from typing import Literal, Protocol

import yaml
from pydantic import BaseModel, ConfigDict, Field

EntryKind = Literal["holiday", "vacation", "working_day"]
DayKind = Literal[
    "working", "working_day", "weekly_off", "saturday_off", "holiday", "vacation", "outside_year"
]
SATURDAY = 5


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CalendarLimits(_Model):
    max_entry_days: int = Field(ge=1, le=92)
    max_entries_per_year: int = Field(ge=10, le=1000)
    max_name_chars: int = Field(ge=10, le=80)


class DefaultPattern(_Model):
    verified: bool
    source: str = Field(min_length=1)
    weekly_off: tuple[int, ...] = Field(max_length=6)
    saturdays_off: tuple[int, ...] = Field(max_length=5)


class SuggestionItem(_Model):
    key: str = Field(pattern=r"^[a-z][a-z0-9_]{1,62}$")
    name: str = Field(min_length=1, max_length=80)
    kind: EntryKind
    starts_on: dt.date
    ends_on: dt.date


class SuggestionSet(_Model):
    key: str = Field(pattern=r"^[a-z][a-z0-9_]{1,62}$")
    label: str = Field(min_length=1)
    items: tuple[SuggestionItem, ...]


class Suggestions(_Model):
    verified: bool
    source: str = Field(min_length=1)
    sets: tuple[SuggestionSet, ...]


class CalendarConfig(_Model):
    version: int = Field(ge=1)
    limits: CalendarLimits
    default_pattern: DefaultPattern
    suggestions: Suggestions

    def suggestion(self, key: str) -> SuggestionItem | None:
        for group in self.suggestions.sets:
            for item in group.items:
                if item.key == key:
                    return item
        return None


@lru_cache(maxsize=1)
def load_calendar_config() -> CalendarConfig:
    raw = yaml.safe_load(
        resources.files("app.academics").joinpath("calendar.yaml").read_text("utf-8")
    )
    return CalendarConfig.model_validate(raw)


class EntryLike(Protocol):
    @property
    def id(self) -> uuid.UUID: ...

    @property
    def class_id(self) -> uuid.UUID | None: ...

    @property
    def kind(self) -> str: ...

    @property
    def name(self) -> str: ...

    @property
    def starts_on(self) -> dt.date: ...

    @property
    def ends_on(self) -> dt.date: ...


@dataclass(frozen=True, slots=True)
class DayInfo:
    on_date: dt.date
    working: bool
    kind: DayKind
    name: str | None = None
    entry_id: uuid.UUID | None = None


def dates(first: dt.date, last: dt.date) -> Iterator[dt.date]:
    day = first
    while day <= last:
        yield day
        day += dt.timedelta(days=1)


def saturday_number(day: dt.date) -> int:
    """1 for the first Saturday of the month, 2 for the second..."""
    return (day.day - 1) // 7 + 1


@dataclass(frozen=True, slots=True)
class Calendar:
    """One academic year's calendar: the year's dates, the weekly pattern and the entries."""

    starts_on: dt.date
    ends_on: dt.date
    weekly_off: frozenset[int]
    saturdays_off: frozenset[int]
    entries: tuple[EntryLike, ...]

    @classmethod
    def build(
        cls,
        starts_on: dt.date,
        ends_on: dt.date,
        weekly_off: Iterable[int],
        saturdays_off: Iterable[int],
        entries: Iterable[EntryLike],
    ) -> Calendar:
        return cls(
            starts_on, ends_on, frozenset(weekly_off), frozenset(saturdays_off), tuple(entries)
        )

    def _by_date(self, class_id: uuid.UUID | None) -> tuple[dict[dt.date, EntryLike], ...]:
        own: dict[dt.date, EntryLike] = {}
        school: dict[dt.date, EntryLike] = {}
        for entry in self.entries:
            if entry.class_id is None:
                target = school
            elif entry.class_id == class_id:
                target = own
            else:
                continue
            for day in dates(entry.starts_on, entry.ends_on):
                target[day] = entry
        return own, school

    def _pattern(self, day: dt.date) -> DayInfo:
        if day.weekday() in self.weekly_off:
            return DayInfo(day, False, "weekly_off")
        if day.weekday() == SATURDAY and saturday_number(day) in self.saturdays_off:
            return DayInfo(day, False, "saturday_off")
        return DayInfo(day, True, "working")

    def days(self, first: dt.date, last: dt.date, class_id: uuid.UUID | None) -> list[DayInfo]:
        """Every date from ``first`` to ``last`` with its kind, for a class (``None``: the
        whole-school calendar only)."""
        own, school = self._by_date(class_id)
        out: list[DayInfo] = []
        for day in dates(first, last):
            if not self.starts_on <= day <= self.ends_on:
                out.append(DayInfo(day, False, "outside_year"))
                continue
            entry = own.get(day) or school.get(day)
            if entry is None:
                out.append(self._pattern(day))
            elif entry.kind == "working_day":
                out.append(DayInfo(day, True, "working_day", entry.name, entry.id))
            else:
                kind: DayKind = "vacation" if entry.kind == "vacation" else "holiday"
                out.append(DayInfo(day, False, kind, entry.name, entry.id))
        return out

    def day(self, on_date: dt.date, class_id: uuid.UUID | None) -> DayInfo:
        return self.days(on_date, on_date, class_id)[0]

    def working_days(
        self, first: dt.date, last: dt.date, class_id: uuid.UUID | None
    ) -> list[dt.date]:
        return [d.on_date for d in self.days(first, last, class_id) if d.working]


def overlapping(
    entries: Sequence[EntryLike], class_id: uuid.UUID | None, first: dt.date, last: dt.date
) -> list[EntryLike]:
    """Entries of the same scope (the same class, or whole school) that share a date with
    ``first..last`` (FR-CAL-006: entries of one scope never overlap)."""
    return [
        e for e in entries if e.class_id == class_id and e.starts_on <= last and first <= e.ends_on
    ]


__all__ = [
    "Calendar",
    "CalendarConfig",
    "DayInfo",
    "DayKind",
    "EntryKind",
    "EntryLike",
    "SuggestionItem",
    "dates",
    "load_calendar_config",
    "overlapping",
    "saturday_number",
]
