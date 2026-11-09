"""Pydantic IO for attendance, exams and marks (docs/09 Attendance and marks; US-1701..US-1703;
FR-ATT-*, FR-MRK-*). Text is NFC-normalised and trimmed on input. Student names are shown to
people who can already read the student (``student.read_basic`` in the same scope).
"""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field

from app.core.textnorm import nfc

AttendanceStatus = Literal["present", "absent", "late", "leave"]
CalendarEntryKind = Literal["holiday", "vacation", "working_day"]
CalendarDayKind = Literal[
    "working", "working_day", "weekly_off", "saturday_off", "holiday", "vacation", "outside_year"
]
RecordSource = Literal["mark", "import"]


def _clean(value: Any) -> Any:
    return nfc(value).strip() if isinstance(value, str) else value


Text = Annotated[str, BeforeValidator(_clean)]
Month = Annotated[str, Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$", examples=["2026-09"])]


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class _Out(BaseModel):
    model_config = ConfigDict(frozen=True)


class RosterStudentOut(_Out):
    """A student of the section's roster (current-year active enrolment)."""

    student_id: uuid.UUID
    full_name: str | None
    admission_no: str | None
    roll_no: str | None


class RecordSectionOut(_Out):
    id: uuid.UUID
    class_id: uuid.UUID
    academic_year_id: uuid.UUID
    label: str = Field(description="Class code and section name, e.g. IX-A")


# --- attendance (FR-ATT-*) ----------------------------------------------------------------------


class AttendanceDayStudent(_Out):
    student: RosterStudentOut
    status: AttendanceStatus | None = Field(description="None: not marked that day")
    version: int = Field(
        default=0,
        description="The mark's version (0: not marked that day). Send it back as "
        "``expected_version`` so a save does not overwrite someone else's change.",
    )


class CalendarDayOut(_Out):
    """One date of the school calendar (FT-06, FR-CAL-004)."""

    on_date: dt.date
    working: bool
    kind: CalendarDayKind = Field(
        description="working (weekly pattern), working_day (an extra working day), weekly_off, "
        "saturday_off, holiday, vacation or outside_year"
    )
    name: str | None = Field(default=None, description="The holiday's or working day's name")
    entry_id: uuid.UUID | None = None


class AttendanceDayOut(_Out):
    section: RecordSectionOut
    on_date: dt.date
    marked: bool = Field(description="Whether any student of the section is marked that day")
    students: list[AttendanceDayStudent]
    counts: dict[str, int] = Field(description="Students per status that day")
    calendar: CalendarDayOut | None = Field(
        default=None,
        description="The day in the school calendar for the section's class (FR-CAL-010); "
        "None when the year has no calendar. Attendance on a day that is not ``working`` is "
        "refused (422 ``holiday``).",
    )


class AttendanceMonthStudent(_Out):
    student: RosterStudentOut
    days: dict[str, AttendanceStatus] = Field(description="ISO date -> status (marked days only)")
    counts: dict[str, int] = Field(
        description="Days per status; with a calendar also ``not_marked``: working days up to "
        "today (from the student's explicit start) without a mark (FR-CAL-011)"
    )


class AttendanceMonthOut(_Out):
    """The month register: school days x students (A4 print). Without a calendar a school day is
    a date with any mark; with one, the working days up to today plus any marked date."""

    section: RecordSectionOut
    month: str
    school_days: list[dt.date]
    students: list[AttendanceMonthStudent]
    calendar_set: bool = Field(default=False, description="Whether the year has a calendar")
    working_days: list[dt.date] = Field(
        default_factory=list,
        description="The month's working days for the section's class (empty without a calendar)",
    )


class AttendanceEntryIn(_In):
    student_id: uuid.UUID
    on_date: dt.date
    status: AttendanceStatus
    expected_version: int | None = Field(
        default=None,
        ge=0,
        le=2_147_483_647,
        description="The ``version`` read for this student and date (0: not marked then). When "
        "sent, the save is refused (412 ``attendance_changed``) if someone else changed the mark "
        "since; leave it out to overwrite (sheet imports).",
    )


class AttendanceWrite(_In):
    entries: list[AttendanceEntryIn] = Field(min_length=1, max_length=2500)
    source: RecordSource = Field(default="mark", description="mark (screen) or import (sheet)")


class WriteResultOut(_Out):
    written: int = Field(description="Entries created or changed")
    unchanged: int
    dates: list[dt.date] = Field(default_factory=list)


class SheetIn(_In):
    document_id: uuid.UUID = Field(
        description="An uploaded, virus-checked import file (POST /documents/uploads with "
        "purpose import_file); it is deleted once read."
    )


class SheetIssueOut(_Out):
    row: int
    column: int | None = Field(description="1-based column (A = 1); None for the whole row")
    code: str


class AttendanceSheetOut(_Out):
    """What the sheet would add. Nothing is stored until the entries are sent to
    ``POST /sections/{section_id}/attendance`` with ``source=import``."""

    entries: list[AttendanceEntryIn]
    dates: list[dt.date]
    students: int
    issues: list[SheetIssueOut]
    issue_count: int


# --- exams and marks (FR-MRK-*) -----------------------------------------------------------------


class ExamOut(_Out):
    id: uuid.UUID
    academic_year_id: uuid.UUID
    name: str
    held_on: dt.date
    version: int
    locked: bool = Field(
        default=False,
        description="Locked exams refuse marks writes and sheet imports (409 ``exam_locked``) "
        "until the principal unlocks them (FR-MRK-008).",
    )
    locked_at: dt.datetime | None = None
    unlocked_at: dt.datetime | None = Field(
        default=None, description="When the exam was last unlocked (None: never)"
    )
    unlock_reason: str | None = Field(
        default=None, description="The principal's reason for the last unlock"
    )


class ExamUnlockIn(_In):
    """Unlocking a locked exam (``exam.unlock``, principal, step-up; FR-MRK-010)."""

    reason: Text = Field(
        min_length=1,
        max_length=500,
        description="Why the marks must change (kept on the exam and on every change made "
        "while it stays unlocked). No Aadhaar numbers.",
    )


class ExamCreate(_In):
    name: Text = Field(min_length=1, max_length=80)
    held_on: dt.date


class MarkIn(_In):
    student_id: uuid.UUID
    subject: Text = Field(min_length=1, max_length=60)
    max_marks: Decimal = Field(gt=0, le=1000, max_digits=6, decimal_places=2)
    marks: Decimal | None = Field(default=None, ge=0, max_digits=6, decimal_places=2)
    absent: bool = False
    expected_version: int | None = Field(
        default=None,
        ge=0,
        le=2_147_483_647,
        description="The ``version`` read for this student and subject (0: no mark then). When "
        "sent, the save is refused (412 ``marks_changed``) if someone else changed the mark "
        "since; leave it out to overwrite (sheet imports).",
    )


class MarksWrite(_In):
    entries: list[MarkIn] = Field(min_length=1, max_length=2500)
    source: RecordSource = "mark"
    reason: Text | None = Field(
        default=None,
        max_length=500,
        description="Why marks are corrected (optional; kept in the marks history). After an "
        "unlock, the unlock reason is kept when this is left out. No Aadhaar numbers.",
    )


class MarkOut(_Out):
    subject: str
    max_marks: Decimal
    marks: Decimal | None
    absent: bool
    version: int = Field(
        default=1,
        description="The mark's version. Send it back as ``expected_version`` so a save does "
        "not overwrite someone else's change.",
    )
    changed_after_lock: bool = Field(
        default=False,
        description="The mark was changed after the exam was first locked (FR-MRK-014)",
    )


class MarksStudentOut(_Out):
    student: RosterStudentOut
    marks: list[MarkOut]
    percent: float | None = Field(
        description="Overall percentage over the subjects with marks (absent papers left out)"
    )


class MarksGridOut(_Out):
    section: RecordSectionOut
    exam: ExamOut
    subjects: list[str]
    students: list[MarksStudentOut]


class MarkChangeOut(_Out):
    """One change of a stored mark (append-only history; FR-MRK-012)."""

    student: RosterStudentOut
    subject: str
    previous_max_marks: Decimal
    previous_marks: Decimal | None
    previous_absent: bool
    new_max_marks: Decimal
    new_marks: Decimal | None
    new_absent: bool
    source: RecordSource
    after_lock: bool = Field(description="Changed after the exam was first locked")
    reason: str | None
    changed_by_name: str | None = Field(description="Who changed it (None: no longer a member)")
    changed_at: dt.datetime


class MarksHistoryOut(_Out):
    section: RecordSectionOut
    exam: ExamOut
    changes: list[MarkChangeOut] = Field(description="Newest first")


class MarksSheetOut(_Out):
    entries: list[MarkIn]
    subjects: list[str]
    students: int
    issues: list[SheetIssueOut]
    issue_count: int


__all__ = [
    "AttendanceDayOut",
    "AttendanceDayStudent",
    "AttendanceEntryIn",
    "AttendanceMonthOut",
    "AttendanceMonthStudent",
    "AttendanceSheetOut",
    "AttendanceStatus",
    "AttendanceWrite",
    "ExamCreate",
    "ExamOut",
    "ExamUnlockIn",
    "MarkChangeOut",
    "MarkIn",
    "MarkOut",
    "MarksGridOut",
    "MarksHistoryOut",
    "MarksSheetOut",
    "MarksStudentOut",
    "MarksWrite",
    "Month",
    "RecordSectionOut",
    "RecordSource",
    "RosterStudentOut",
    "SheetIn",
    "SheetIssueOut",
    "WriteResultOut",
]


# --- school calendar (FT-06; FR-CAL-*) ---------------------------------------------------------

Weekday = Annotated[int, Field(ge=0, le=6, description="0 = Monday .. 6 = Sunday")]
SaturdayNumber = Annotated[int, Field(ge=1, le=5, description="1 = first Saturday of the month")]
SuggestionKey = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{1,62}$")]


class CalendarPatternIn(_In):
    """The weekly pattern of a year's calendar (FR-CAL-001). Saving it the first time sets up the
    calendar; from then on attendance on days off is refused."""

    weekly_off: list[Weekday] = Field(max_length=6, description="Weekdays off, e.g. [6] Sunday")
    saturdays_off: list[SaturdayNumber] = Field(
        max_length=5, description="Saturdays of the month that are off, e.g. [2]"
    )


class CalendarEntryIn(_In):
    """A holiday, vacation or extra working day (FR-CAL-002), for the whole school or one class
    (``class_id``, a per-class exception, FR-CAL-003)."""

    kind: CalendarEntryKind
    name: Annotated[Text, Field(min_length=1, max_length=80)]
    starts_on: dt.date
    ends_on: dt.date
    class_id: uuid.UUID | None = None
    suggestion: SuggestionKey | None = Field(
        default=None, description="The suggestion this entry adopts (FR-CAL-014)"
    )


class CalendarEntryOut(_Out):
    id: uuid.UUID
    kind: CalendarEntryKind
    name: str
    starts_on: dt.date
    ends_on: dt.date
    class_id: uuid.UUID | None
    suggestion: str | None = Field(description="The adopted suggestion's code")
    days: int = Field(description="Calendar days the entry spans")


class CalendarEntryAddedOut(CalendarEntryOut):
    attendance_marks: int = Field(
        description="Attendance marks already dated inside the entry (FR-CAL-008): for a holiday "
        "or vacation they no longer count in working-day totals"
    )


class CalendarOut(_Out):
    """A year's school calendar (FR-CAL-001..FR-CAL-003)."""

    academic_year_id: uuid.UUID
    starts_on: dt.date
    ends_on: dt.date
    set_up: bool = Field(description="False until the pattern is saved the first time")
    weekly_off: list[int]
    saturdays_off: list[int]
    entries: list[CalendarEntryOut]
    working_days: int | None = Field(
        description="Whole-school working days of the year (None before set-up)"
    )
    version: int = Field(description="0 before set-up; send as If-Match to change the pattern")


class CalendarMonthOut(_Out):
    """One month of the calendar for the whole school or a class (FR-CAL-009)."""

    academic_year_id: uuid.UUID
    month: str
    class_id: uuid.UUID | None
    set_up: bool
    days: list[CalendarDayOut]
    working_days: int


class CalendarSuggestionOut(_Out):
    code: str = Field(description="Send as ``suggestion`` when adding it")
    group: str
    group_label: str
    name: str
    kind: CalendarEntryKind
    starts_on: dt.date
    ends_on: dt.date


class CalendarSuggestionsOut(_Out):
    """Holiday suggestions inside the year not yet adopted for the whole school (FR-CAL-013).
    Unverified: the school checks the dates before adding them."""

    verified: bool
    source: str
    items: list[CalendarSuggestionOut]
