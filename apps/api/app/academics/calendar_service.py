"""School calendar: working days and holidays per academic year (FT-06; lead decision D85;
FR-CAL-001..FR-CAL-020). Public functions are re-exported by :mod:`app.academics.service`.

**What it holds.** Per academic year a weekly pattern (weekdays off, Saturdays of the month off)
and entries: holidays, vacations and extra working days, for the whole school or one class
(a per-class exception, e.g. an exam day). Entries of one scope never overlap (409
``calendar_overlap``). Holiday suggestions come from ``app/academics/calendar.yaml``
(``verified: false``); a suggestion becomes part of the calendar only when a person adds it.

**Who.** Reading the calendar needs ``student.read_basic`` (like the academic years); changing it
needs ``tenant.structure.manage`` school-wide (the academic structure). Changes are audited in the
same transaction (``calendar.*``; kinds, dates, keys and counts only).

**Effect on attendance (FR-CAL-007).** Once a year has a calendar, attendance dated on a day that
is not a working day for the section's class is refused (422 ``holiday``; a per-cell issue in
sheets) unless the day is made an extra working day. Without a calendar nothing changes: a school
day is a date with any mark. Working-day counts (:func:`attendance_summary`) feed the register,
the TC and the year-end pack (FT-04, FT-07, FT-08).

No personal data is stored here. Logs carry ids and counts only (invariant 5).
"""

from __future__ import annotations

import calendar as stdcal
import datetime as dt
import uuid
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from typing import Any, Final
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.academics import repository as repo
from app.academics.models import CalendarEntry, SchoolCalendar
from app.academics.schemas import (
    CalendarDayOut,
    CalendarEntryAddedOut,
    CalendarEntryIn,
    CalendarEntryOut,
    CalendarMonthOut,
    CalendarOut,
    CalendarPatternIn,
    CalendarSuggestionOut,
    CalendarSuggestionsOut,
)
from app.academics.school_calendar import Calendar, DayInfo, load_calendar_config, overlapping
from app.audit import service as audit
from app.authz.context import UserContext
from app.core.errors import Conflict, NotFound, PreconditionFailed, ValidationFailed
from app.core.ids import new_id
from app.core.logging import get_context, get_logger
from app.students import service as students
from app.tenancy import service as tenancy
from app.tenancy.schemas import AcademicYearOut

log = get_logger(__name__)

CALENDAR_READ: Final = "student.read_basic"
CALENDAR_MANAGE: Final = "tenant.structure.manage"
HOLIDAY_CODE: Final = "holiday"
IST: Final = ZoneInfo("Asia/Kolkata")


def _request_id() -> str | None:
    value = get_context().get("request_id")
    return value if isinstance(value, str) else None


def _error(field: str, code: str) -> dict[str, str]:
    return {"field": field, "code": code, "message_key": f"errors.{code}"}


def _audit(session: Session, action: str, year_id: uuid.UUID, summary: Mapping[str, Any]) -> None:
    audit.record(
        session,
        action=action,
        resource_type="academic_year",
        resource_id=year_id,
        summary=summary,
        actor_type="user",
        request_id=_request_id(),
    )


def _year(session: Session, year_id: uuid.UUID) -> AcademicYearOut:
    try:
        return tenancy.get_academic_year(session, year_id)
    except NotFound:
        raise NotFound("Academic year not found") from None


def _build(year: AcademicYearOut, row: SchoolCalendar, entries: list[CalendarEntry]) -> Calendar:
    return Calendar.build(year.starts_on, year.ends_on, row.weekly_off, row.saturdays_off, entries)


def year_calendar(session: Session, year: AcademicYearOut) -> Calendar | None:
    """The year's calendar, or None when the school has not set it up (old rule applies)."""
    row = repo.get_calendar(session, year.id)
    if row is None:
        return None
    return _build(year, row, repo.calendar_entries(session, year.id))


def _entry_out(entry: CalendarEntry) -> CalendarEntryOut:
    return CalendarEntryOut(
        id=entry.id,
        kind=entry.kind,
        name=entry.name,
        starts_on=entry.starts_on,
        ends_on=entry.ends_on,
        class_id=entry.class_id,
        suggestion=entry.suggestion_key,
        days=(entry.ends_on - entry.starts_on).days + 1,
    )


def _calendar_out(
    session: Session, year: AcademicYearOut, row: SchoolCalendar | None
) -> CalendarOut:
    if row is None:
        pattern = load_calendar_config().default_pattern
        return CalendarOut(
            academic_year_id=year.id,
            starts_on=year.starts_on,
            ends_on=year.ends_on,
            set_up=False,
            weekly_off=list(pattern.weekly_off),
            saturdays_off=list(pattern.saturdays_off),
            entries=[],
            working_days=None,
            version=0,
        )
    entries = repo.calendar_entries(session, year.id)
    cal = _build(year, row, entries)
    return CalendarOut(
        academic_year_id=year.id,
        starts_on=year.starts_on,
        ends_on=year.ends_on,
        set_up=True,
        weekly_off=sorted(row.weekly_off),
        saturdays_off=sorted(row.saturdays_off),
        entries=[_entry_out(e) for e in entries],
        working_days=len(cal.working_days(year.starts_on, year.ends_on, None)),
        version=row.version,
    )


def get_calendar(session: Session, ctx: UserContext, year_id: uuid.UUID) -> CalendarOut:
    """A year's calendar (FR-CAL-001). Before set-up: the default pattern, ``set_up`` false."""
    year = _year(session, year_id)
    return _calendar_out(session, year, repo.get_calendar(session, year.id))


def set_pattern(
    session: Session,
    ctx: UserContext,
    year_id: uuid.UUID,
    data: CalendarPatternIn,
    *,
    expected_version: int | None = None,
) -> CalendarOut:
    """Save the weekly pattern; the first save sets up the calendar (FR-CAL-001). 412
    ``calendar_changed`` when ``expected_version`` is stale (0: not set up yet). Audit
    ``calendar.set_up`` / ``calendar.pattern_changed``."""
    year = _year(session, year_id)
    weekly = sorted(set(data.weekly_off))
    saturdays = sorted(set(data.saturdays_off))
    if len(weekly) > 6:
        raise ValidationFailed([_error("weekly_off", "no_working_weekday")])
    row = repo.get_calendar(session, year.id, for_update=True)
    current = row.version if row is not None else 0
    if expected_version is not None and expected_version != current:
        raise PreconditionFailed(
            "Someone else changed the calendar since you opened it. Reload, then save again.",
            code="calendar_changed",
        )
    if row is None:
        row = repo.insert_calendar(
            session,
            {
                "id": new_id(),
                "academic_year_id": year.id,
                "weekly_off": weekly,
                "saturdays_off": saturdays,
                "created_by": ctx.user_id,
                "updated_by": ctx.user_id,
            },
        )
        action = "calendar.set_up"
    else:
        before = (sorted(row.weekly_off), sorted(row.saturdays_off))
        if before == (weekly, saturdays):
            return _calendar_out(session, year, row)
        row = repo.update_calendar(
            session,
            row.id,
            {"weekly_off": weekly, "saturdays_off": saturdays, "updated_by": ctx.user_id},
        )
        action = "calendar.pattern_changed"
    _audit(session, action, year.id, {"weekly_off": weekly, "saturdays_off": saturdays})
    log.info("academics.calendar_pattern_saved", resource_id=year.id)
    return _calendar_out(session, year, row)


def _class_sections(
    session: Session, year_id: uuid.UUID, class_id: uuid.UUID | None
) -> list[uuid.UUID] | None:
    if class_id is None:
        return None
    return [
        s.id
        for s in tenancy.list_sections(session, academic_year_id=year_id)
        if s.class_id == class_id
    ]


def add_entry(
    session: Session, ctx: UserContext, year_id: uuid.UUID, data: CalendarEntryIn
) -> CalendarEntryAddedOut:
    """Add a holiday, vacation or extra working day (FR-CAL-002, FR-CAL-003). 409
    ``calendar_not_set_up``, ``calendar_overlap`` or ``suggestion_already_added``; 422
    ``dates_reversed``, ``entry_too_long``, ``date_out_of_range``, ``unknown_class``,
    ``unknown_suggestion`` or ``too_many_entries``. Answers how many attendance marks already
    fall inside it (FR-CAL-008). Audit ``calendar.entry_added``."""
    cfg = load_calendar_config()
    year = _year(session, year_id)
    row = repo.get_calendar(session, year.id, for_update=True)
    if row is None:
        raise Conflict(
            "Save the weekly pattern first to set up this year's calendar.",
            code="calendar_not_set_up",
        )
    errors: list[dict[str, str]] = []
    if data.ends_on < data.starts_on:
        errors.append(_error("ends_on", "dates_reversed"))
    elif (data.ends_on - data.starts_on).days + 1 > cfg.limits.max_entry_days:
        errors.append(_error("ends_on", "entry_too_long"))
    if not (year.starts_on <= data.starts_on and data.ends_on <= year.ends_on):
        errors.append(_error("starts_on", "date_out_of_range"))
    if data.class_id is not None:
        try:
            tenancy.get_class(session, data.class_id)
        except NotFound:
            errors.append(_error("class_id", "unknown_class"))
    if data.suggestion is not None and cfg.suggestion(data.suggestion) is None:
        errors.append(_error("suggestion", "unknown_suggestion"))
    entries = repo.calendar_entries(session, year.id)
    if len(entries) >= cfg.limits.max_entries_per_year:
        errors.append(_error("entries", "too_many_entries"))
    if errors:
        raise ValidationFailed(errors, detail="This entry cannot be added. Fix it and try again.")
    if data.suggestion is not None and any(
        e.suggestion_key == data.suggestion and e.class_id == data.class_id for e in entries
    ):
        raise Conflict(
            "This suggestion is already in the calendar.", code="suggestion_already_added"
        )
    if overlapping(entries, data.class_id, data.starts_on, data.ends_on):
        raise Conflict(
            "Another holiday, vacation or working day already covers some of these dates. "
            "Remove it or choose other dates.",
            code="calendar_overlap",
        )
    entry = repo.insert_calendar_entry(
        session,
        {
            "id": new_id(),
            "academic_year_id": year.id,
            "class_id": data.class_id,
            "kind": data.kind,
            "name": data.name,
            "starts_on": data.starts_on,
            "ends_on": data.ends_on,
            "suggestion_key": data.suggestion,
            "created_by": ctx.user_id,
        },
    )
    marks = repo.marks_between(
        session, entry.starts_on, entry.ends_on, _class_sections(session, year.id, entry.class_id)
    )
    _audit(
        session,
        "calendar.entry_added",
        year.id,
        {
            "entry_id": entry.id,
            "kind": entry.kind,
            "first_date": entry.starts_on.isoformat(),
            "last_date": entry.ends_on.isoformat(),
            "class_id": entry.class_id,
            "suggestion_key": entry.suggestion_key,
            "attendance_marks": marks,
        },
    )
    log.info("academics.calendar_entry_added", resource_id=entry.id, count=marks)
    return CalendarEntryAddedOut(**_entry_out(entry).model_dump(), attendance_marks=marks)


def remove_entry(
    session: Session, ctx: UserContext, year_id: uuid.UUID, entry_id: uuid.UUID
) -> None:
    """Remove an entry of the year (FR-CAL-005); 404 when it is not one. Audit
    ``calendar.entry_removed``."""
    year = _year(session, year_id)
    repo.get_calendar(session, year.id, for_update=True)
    entry = next((e for e in repo.calendar_entries(session, year.id) if e.id == entry_id), None)
    if entry is None:
        raise NotFound("Calendar entry not found")
    summary = {
        "entry_id": entry.id,
        "kind": entry.kind,
        "first_date": entry.starts_on.isoformat(),
        "last_date": entry.ends_on.isoformat(),
        "class_id": entry.class_id,
        "suggestion_key": entry.suggestion_key,
    }
    repo.delete_calendar_entry(session, entry.id)
    _audit(session, "calendar.entry_removed", year.id, summary)
    log.info("academics.calendar_entry_removed", resource_id=entry_id)


def day_out(info: DayInfo) -> CalendarDayOut:
    return CalendarDayOut(
        on_date=info.on_date,
        working=info.working,
        kind=info.kind,
        name=info.name,
        entry_id=info.entry_id,
    )


def month_bounds(month: str) -> tuple[dt.date, dt.date]:
    year, mon = (int(p) for p in month.split("-"))
    try:
        return dt.date(year, mon, 1), dt.date(year, mon, stdcal.monthrange(year, mon)[1])
    except ValueError:
        raise ValidationFailed([_error("month", "invalid_month")]) from None


def calendar_month(
    session: Session,
    ctx: UserContext,
    year_id: uuid.UUID,
    month: str,
    class_id: uuid.UUID | None = None,
) -> CalendarMonthOut:
    """One month of the calendar for the whole school or one class (FR-CAL-009). Before set-up
    the default pattern is shown (``set_up`` false)."""
    year = _year(session, year_id)
    if class_id is not None:
        try:
            tenancy.get_class(session, class_id)
        except NotFound:
            raise NotFound("Class not found") from None
    first, last = month_bounds(month)
    row = repo.get_calendar(session, year.id)
    if row is None:
        pattern = load_calendar_config().default_pattern
        cal = Calendar.build(
            year.starts_on, year.ends_on, pattern.weekly_off, pattern.saturdays_off, ()
        )
    else:
        cal = _build(year, row, repo.calendar_entries(session, year.id))
    days = cal.days(first, last, class_id)
    return CalendarMonthOut(
        academic_year_id=year.id,
        month=month,
        class_id=class_id,
        set_up=row is not None,
        days=[day_out(d) for d in days],
        working_days=sum(1 for d in days if d.working),
    )


def suggestions(session: Session, ctx: UserContext, year_id: uuid.UUID) -> CalendarSuggestionsOut:
    """Holiday suggestions inside the year that the school has not added for the whole school
    yet (FR-CAL-013). ``verified`` is false: the school checks every date."""
    cfg = load_calendar_config()
    year = _year(session, year_id)
    taken = {
        e.suggestion_key
        for e in repo.calendar_entries(session, year.id)
        if e.suggestion_key is not None and e.class_id is None
    }
    items = [
        CalendarSuggestionOut(
            code=item.key,
            group=group.key,
            group_label=group.label,
            name=item.name,
            kind=item.kind,
            starts_on=item.starts_on,
            ends_on=item.ends_on,
        )
        for group in cfg.suggestions.sets
        for item in group.items
        if item.key not in taken
        and year.starts_on <= item.starts_on
        and item.ends_on <= year.ends_on
    ]
    return CalendarSuggestionsOut(
        verified=cfg.suggestions.verified, source=cfg.suggestions.source, items=items
    )


# --- working-day counts for other modules (FT-04, FT-07, FT-08, the TC) -----------------------


@dataclass(frozen=True, slots=True)
class AttendanceSummary:
    """One student's attendance in an academic year up to a date (FR-CAL-012).

    ``working_days`` and ``not_marked`` are None when the year has no calendar; then only the
    marked days are known. Marks dated on a day off (made before the day was declared a holiday)
    are left out of the status counts and counted in ``marks_on_days_off``."""

    first_day: dt.date
    last_day: dt.date
    working_days: int | None
    present: int
    late: int
    leave: int
    absent: int
    not_marked: int | None
    marks_on_days_off: int


def attendance_summary(
    session: Session,
    student_ids: Collection[uuid.UUID],
    academic_year_id: uuid.UUID,
    *,
    until: dt.date | None = None,
    today: dt.date | None = None,
) -> dict[uuid.UUID, AttendanceSummary]:
    """Working days, days per status and unmarked working days per student in a year, from the
    year start (or the student's explicit enrolment start in that year) to the earliest of
    ``until``, today (IST) and the year end. The class is the one of the student's active
    enrolment in that year (whole-school calendar otherwise). System read: ``student_ids`` MUST
    be students the caller already reached (same contract as ``attendance_history``)."""
    year = tenancy.get_academic_year(session, academic_year_id)
    now = today or dt.datetime.now(dt.UTC).astimezone(IST).date()
    last = min(year.ends_on, until or year.ends_on, now)
    cal = year_calendar(session, year)
    sections = {s.id: s.class_id for s in tenancy.list_sections(session, academic_year_id=year.id)}
    enrolments = students.active_enrolments(session, student_ids)
    marks: dict[uuid.UUID, dict[dt.date, str]] = {sid: {} for sid in student_ids}
    for sid, on_date, status in repo.student_marks(session, student_ids, since=year.starts_on):
        if on_date <= last:
            marks.setdefault(sid, {})[on_date] = status
    cache: dict[uuid.UUID | None, dict[dt.date, bool]] = {}
    out: dict[uuid.UUID, AttendanceSummary] = {}
    for sid in student_ids:
        enrolment = next(
            (e for e in enrolments.get(sid, []) if e.academic_year_id == year.id), None
        )
        class_id = sections.get(enrolment.section_id) if enrolment else None
        first = year.starts_on
        if enrolment and enrolment.started_on_explicit and enrolment.started_on:
            first = max(first, enrolment.started_on)
        own = marks.get(sid, {})
        if cal is None:
            counted = {d: s for d, s in own.items() if first <= d <= last}
            working: int | None = None
            not_marked: int | None = None
            off = 0
        else:
            if class_id not in cache:
                cache[class_id] = {
                    d.on_date: d.working for d in cal.days(year.starts_on, year.ends_on, class_id)
                }
            open_days = cache[class_id]
            days = [d for d, ok in open_days.items() if ok and first <= d <= last]
            counted = {d: s for d, s in own.items() if open_days.get(d, False) and d >= first}
            off = sum(1 for d in own if not open_days.get(d, False))
            working = len(days)
            not_marked = sum(1 for d in days if d not in own)
        values = list(counted.values())
        out[sid] = AttendanceSummary(
            first_day=first,
            last_day=last,
            working_days=working,
            present=values.count("present"),
            late=values.count("late"),
            leave=values.count("leave"),
            absent=values.count("absent"),
            not_marked=not_marked,
            marks_on_days_off=off,
        )
    return out


__all__ = [
    "CALENDAR_MANAGE",
    "CALENDAR_READ",
    "HOLIDAY_CODE",
    "AttendanceSummary",
    "add_entry",
    "attendance_summary",
    "calendar_month",
    "day_out",
    "get_calendar",
    "month_bounds",
    "remove_entry",
    "set_pattern",
    "suggestions",
    "year_calendar",
]
