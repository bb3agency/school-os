"""Pydantic IO for behaviour notes, early-warning flags, indicators and the student timeline
(docs/09 Student insights; US-1704..US-1709; FR-EW-*). Text is NFC-normalised and trimmed on
input. Everything here is restricted (C3) and purpose-limited (08 §4): responses go only to the
student's class teacher and the principal (FR-EW-011).
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field

from app.core.textnorm import nfc

FlagStatus = Literal["open", "in_progress", "closed"]
FlagRule = Literal[
    "attendance_streak",
    "attendance_rate",
    "course_low",
    "course_decline",
    "behaviour_concerns",
    "manual",
]
IndicatorName = Literal["attendance", "behaviour", "course"]
NoteCategory = Literal["positive", "observation", "concern"]
ActionKind = Literal[
    "talked_with_student",
    "called_parent",
    "met_parent",
    "home_visit",
    "remedial_support",
    "referred_counsellor",
    "referred_principal",
    "other",
]
LogKind = Literal[
    "raised",
    "talked_with_student",
    "called_parent",
    "met_parent",
    "home_visit",
    "remedial_support",
    "referred_counsellor",
    "referred_principal",
    "other",
    "closed",
]
CloseReason = Literal[
    "improved",
    "support_in_place",
    "parent_informed",
    "no_concern",
    "student_left",
    "raised_in_error",
]
EraseReason = Literal["parent_request", "entered_in_error"]
FlagView = Literal["mine", "all"]
DueFilter = Literal["overdue", "due_soon"]
TimelineKind = Literal["enrolment", "attendance_month", "exam", "note", "flag", "certificate"]
Evidence = dict[str, int | float | str]


def _clean(value: Any) -> Any:
    return nfc(value).strip() if isinstance(value, str) else value


Text = Annotated[str, BeforeValidator(_clean)]


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class _Out(BaseModel):
    model_config = ConfigDict(frozen=True)


class StaffRef(_Out):
    """A staff member next to a record: membership id and display name only."""

    membership_id: uuid.UUID
    display_name: str | None


class InsightStudentRef(_Out):
    id: uuid.UUID
    full_name: str | None
    admission_no: str | None
    section_label: str | None = Field(description="Current section, e.g. IX-A")


# --- flags ----------------------------------------------------------------------------------------


class ActionOut(_Out):
    id: uuid.UUID
    kind: LogKind
    acted_on: dt.date
    note: str | None = Field(description="Restricted (C3); shown only to people who may act")
    by: StaffRef | None
    created_at: dt.datetime


class InsightFlagOut(_Out):
    id: uuid.UUID
    student: InsightStudentRef
    indicator: IndicatorName
    rule: FlagRule
    evidence: Evidence = Field(
        description="What the rule saw: numbers, codes and ISO dates only (e.g. days, from, "
        "to, rate, percent, drop, concerns, threshold)"
    )
    status: FlagStatus
    owner: StaffRef | None = Field(description="None: unassigned (the principal assigns it)")
    raised_on: dt.date
    due_on: dt.date
    overdue: bool = Field(description="Past the due date and nobody has acted yet")
    actioned: bool
    first_action_at: dt.datetime | None
    closed_at: dt.datetime | None
    close_reason: CloseReason | None
    raised_by: StaffRef | None = Field(description="Set for flags a person raised (manual)")
    version: int


class InsightFlagDetail(InsightFlagOut):
    actions: list[ActionOut]


class ManualFlagIn(_In):
    indicator: IndicatorName
    note: Text | None = Field(default=None, max_length=1000)


class ActionIn(_In):
    kind: ActionKind
    acted_on: dt.date | None = Field(default=None, description="Default: today (IST)")
    note: Text | None = Field(default=None, max_length=1000)


class CloseIn(_In):
    reason: CloseReason
    note: Text | None = Field(default=None, max_length=1000)


class AssignIn(_In):
    owner_membership_id: uuid.UUID


class EraseIn(_In):
    reason: EraseReason


class ErasedOut(_Out):
    """What an erasure removed (the text itself is never returned or kept)."""

    id: uuid.UUID
    erased: bool = True


class OwnerOut(_Out):
    membership_id: uuid.UUID
    display_name: str
    roles: list[str]


class InsightSummaryOut(_Out):
    """Counts only, for the caller's scope in this school (FR-EW-015; the M5 exit metric)."""

    since: dt.date
    raised: int = Field(description="Flags raised since the date")
    actioned_on_time: int = Field(description="Of those, first acted on by their due date")
    actioned_late: int
    not_actioned: int
    overdue: int = Field(description="Open flags past their due date with no action (now)")
    open: int = Field(description="Flags not closed (now)")


# --- notes ----------------------------------------------------------------------------------------


class NoteIn(_In):
    category: NoteCategory
    noted_on: dt.date | None = Field(default=None, description="Default: today (IST)")
    text: Text = Field(min_length=1, max_length=500)


class NoteOut(_Out):
    id: uuid.UUID
    student_id: uuid.UUID
    category: NoteCategory
    noted_on: dt.date
    text: str
    by: StaffRef | None
    created_at: dt.datetime


# --- indicators, settings -------------------------------------------------------------------------


class AttendanceIndicatorOut(_Out):
    days: int = Field(description="Marked school days in the window")
    present: int
    late: int
    absent: int
    leave: int
    rate: float | None
    streak: int = Field(description="Consecutive absences up to the last marked day")
    concern: bool


class BehaviourIndicatorOut(_Out):
    concerns: int
    window_days: int
    concern: bool


class CourseIndicatorOut(_Out):
    exam_id: uuid.UUID | None
    percent: float | None
    previous_percent: float | None
    change: float | None
    concern: bool


class IndicatorsOut(_Out):
    attendance: AttendanceIndicatorOut
    behaviour: BehaviourIndicatorOut
    course: CourseIndicatorOut


class RuleSettingOut(_Out):
    key: str
    indicator: IndicatorName
    enabled: bool
    can_disable: bool
    threshold: int
    default: int
    min: int
    max: int
    window: int | None
    min_days: int | None


class SettingsOut(_Out):
    rules: list[RuleSettingOut]
    rules_version: int
    due_days: int
    version: int = Field(description="ETag for PUT (0 until the school first changes them)")
    updated_at: dt.datetime | None


class RuleSettingIn(_In):
    enabled: bool | None = None
    threshold: int | None = Field(default=None, ge=1, le=100)


class SettingsIn(_In):
    rules: dict[str, RuleSettingIn] = Field(max_length=10)


# --- timeline -------------------------------------------------------------------------------------


class EnrolmentEvent(_Out):
    section_label: str | None
    status: str
    started_on: dt.date | None
    ended_on: dt.date | None


class AttendanceMonthEvent(_Out):
    month: str
    days: int
    present: int
    late: int
    absent: int
    leave: int


class ExamEvent(_Out):
    exam_id: uuid.UUID
    name: str
    percent: float | None
    papers: int
    absent_papers: int


class CertificateEvent(_Out):
    certificate_id: uuid.UUID
    certificate_type: str
    status: str
    serial: str | None


class TimelineItem(_Out):
    """One event; exactly the field named by ``kind`` is set."""

    kind: TimelineKind
    on: dt.date
    enrolment: EnrolmentEvent | None = None
    attendance: AttendanceMonthEvent | None = None
    exam: ExamEvent | None = None
    note: NoteOut | None = None
    flag: InsightFlagDetail | None = None
    certificate: CertificateEvent | None = None


class TimelineOut(_Out):
    student: InsightStudentRef
    indicators: IndicatorsOut
    items: list[TimelineItem]


__all__ = [
    "ActionIn",
    "ActionKind",
    "ActionOut",
    "AssignIn",
    "AttendanceIndicatorOut",
    "AttendanceMonthEvent",
    "BehaviourIndicatorOut",
    "CertificateEvent",
    "CloseIn",
    "CloseReason",
    "CourseIndicatorOut",
    "DueFilter",
    "EnrolmentEvent",
    "EraseIn",
    "EraseReason",
    "ErasedOut",
    "Evidence",
    "ExamEvent",
    "FlagRule",
    "FlagStatus",
    "FlagView",
    "IndicatorName",
    "IndicatorsOut",
    "InsightFlagDetail",
    "InsightFlagOut",
    "InsightStudentRef",
    "InsightSummaryOut",
    "LogKind",
    "ManualFlagIn",
    "NoteCategory",
    "NoteIn",
    "NoteOut",
    "OwnerOut",
    "RuleSettingIn",
    "RuleSettingOut",
    "SettingsIn",
    "SettingsOut",
    "StaffRef",
    "TimelineItem",
    "TimelineKind",
    "TimelineOut",
]
