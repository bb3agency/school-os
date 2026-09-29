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


class SectionOut(_Out):
    id: uuid.UUID
    class_id: uuid.UUID
    academic_year_id: uuid.UUID
    label: str = Field(description="Class code and section name, e.g. IX-A")


# --- attendance (FR-ATT-*) ----------------------------------------------------------------------


class AttendanceDayStudent(_Out):
    student: RosterStudentOut
    status: AttendanceStatus | None = Field(description="None: not marked that day")


class AttendanceDayOut(_Out):
    section: SectionOut
    on_date: dt.date
    marked: bool = Field(description="Whether any student of the section is marked that day")
    students: list[AttendanceDayStudent]
    counts: dict[str, int] = Field(description="Students per status that day")


class AttendanceMonthStudent(_Out):
    student: RosterStudentOut
    days: dict[str, AttendanceStatus] = Field(description="ISO date -> status (marked days only)")
    counts: dict[str, int]


class AttendanceMonthOut(_Out):
    """The month register: school days (dates with any mark) x students (A4 print)."""

    section: SectionOut
    month: str
    school_days: list[dt.date]
    students: list[AttendanceMonthStudent]


class AttendanceEntryIn(_In):
    student_id: uuid.UUID
    on_date: dt.date
    status: AttendanceStatus


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


class ExamCreate(_In):
    name: Text = Field(min_length=1, max_length=80)
    held_on: dt.date


class MarkIn(_In):
    student_id: uuid.UUID
    subject: Text = Field(min_length=1, max_length=60)
    max_marks: Decimal = Field(gt=0, le=1000, max_digits=6, decimal_places=2)
    marks: Decimal | None = Field(default=None, ge=0, max_digits=6, decimal_places=2)
    absent: bool = False


class MarksWrite(_In):
    entries: list[MarkIn] = Field(min_length=1, max_length=2500)
    source: RecordSource = "mark"


class MarkOut(_Out):
    subject: str
    max_marks: Decimal
    marks: Decimal | None
    absent: bool


class MarksStudentOut(_Out):
    student: RosterStudentOut
    marks: list[MarkOut]
    percent: float | None = Field(
        description="Overall percentage over the subjects with marks (absent papers left out)"
    )


class MarksGridOut(_Out):
    section: SectionOut
    exam: ExamOut
    subjects: list[str]
    students: list[MarksStudentOut]


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
    "MarkIn",
    "MarkOut",
    "MarksGridOut",
    "MarksSheetOut",
    "MarksStudentOut",
    "MarksWrite",
    "Month",
    "RecordSource",
    "RosterStudentOut",
    "SectionOut",
    "SheetIn",
    "SheetIssueOut",
    "WriteResultOut",
]
