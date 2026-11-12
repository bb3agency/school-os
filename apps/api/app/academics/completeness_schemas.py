"""Pydantic IO for records completeness (FT-14, lead decision D86; FR-ATT-016..FR-ATT-025,
FR-MRK-016..FR-MRK-025; docs/09 Attendance and marks).

Counts only: how many students of a section have a mark, never which ones. Staff are named
(the section's class teacher) only to holders of ``completeness.read`` (principal, exam
coordinator). Framed as task completeness per section, never a teacher score or ranking.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.academics.schemas import CalendarDayKind

AttendanceCompletenessStatus = Literal["complete", "partial", "not_taken", "day_off", "no_students"]
MarksCellStatus = Literal["complete", "partial", "missing", "no_students"]
MarksSectionStatus = Literal["complete", "partial", "not_started", "no_students"]
ReminderKind = Literal["attendance", "marks"]


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class _Out(BaseModel):
    model_config = ConfigDict(frozen=True)


class CompletenessTeacherRef(_Out):
    """The section's class teacher (membership id and display name only)."""

    membership_id: uuid.UUID
    display_name: str | None


class AttendanceCompletenessRow(_Out):
    section_id: uuid.UUID
    class_id: uuid.UUID
    label: str = Field(description="e.g. IX-A")
    students: int = Field(description="Students enrolled in the section on the date")
    marked: int = Field(description="Of those, students with a status for the date")
    status: AttendanceCompletenessStatus
    calendar: CalendarDayKind | None = Field(
        description="The day's kind for the section's class; null without a school calendar"
    )
    class_teacher: CompletenessTeacherRef | None = Field(
        description="Who the entry waits on; null when the section has no class teacher"
    )


class AttendanceCompletenessOut(_Out):
    on_date: dt.date
    academic_year_id: uuid.UUID
    sections: int
    complete: int = Field(description="Sections where every student has a status")
    partial: int
    not_taken: int
    day_off: int = Field(description="Sections whose class has no school that day")
    no_students: int
    reminders_enabled: bool = Field(description="Whether in-app reminders may be sent")
    rows: list[AttendanceCompletenessRow]


class MarksCompletenessCell(_Out):
    subject: str
    entered: int = Field(description="Students with a mark or marked absent for the paper")
    status: MarksCellStatus


class MarksCompletenessRow(_Out):
    section_id: uuid.UUID
    class_id: uuid.UUID
    label: str
    students: int = Field(description="Students actively enrolled in the section")
    status: MarksSectionStatus
    subjects: list[MarksCompletenessCell] = Field(description="One cell per subject of the exam")
    class_teacher: CompletenessTeacherRef | None


class MarksCompletenessOut(_Out):
    exam_id: uuid.UUID
    exam_name: str
    held_on: dt.date
    locked: bool
    subjects: list[str] = Field(
        description="Subjects entered for this exam in the sections shown (first written first)"
    )
    sections: int
    complete: int
    partial: int
    not_started: int
    no_students: int
    reminders_enabled: bool
    rows: list[MarksCompletenessRow]


class CompletenessReminderIn(_In):
    section_id: uuid.UUID
    kind: ReminderKind
    exam_id: uuid.UUID | None = Field(default=None, description="Required for marks")


class CompletenessReminderOut(_Out):
    sent: int = Field(description="0 when the class teacher was already reminded today")


__all__ = [
    "AttendanceCompletenessOut",
    "AttendanceCompletenessRow",
    "AttendanceCompletenessStatus",
    "CompletenessReminderIn",
    "CompletenessReminderOut",
    "CompletenessTeacherRef",
    "MarksCellStatus",
    "MarksCompletenessCell",
    "MarksCompletenessOut",
    "MarksCompletenessRow",
    "MarksSectionStatus",
    "ReminderKind",
]
